"""Opt-in, text-only Gemini UI transport. Never launches or closes Chrome.

The browser profile must already be authenticated and expose loopback CDP.
Model selection is UI evidence, not independent backend model attestation.
"""
import asyncio
import re
from types import SimpleNamespace
from urllib.parse import urlparse
from quota_policy import policy, states, record, inspect_banner, QuotaExceeded
import time
from pathlib import Path
from browser_failures import BrowserNotSubmitted, check_identity, transient_transport_error

# Headed Chromium menus can dismiss when another profile gains focus. Serialize
# browser UI work across accounts, in addition to each account's request lock.
BROWSER_UI_LOCK = asyncio.Lock()


class BrowserTransport:
    MODEL_LABELS = {"gemini-3-pro": "3.1 Pro", "gemini-pro": "3.1 Pro",
                    "gemini-3.1-pro": "3.1 Pro", "gemini-3-flash": "3.8 Flash",
                    "gemini-3.8-flash": "3.8 Flash", "gemini-3.5-flash-lite": "3.5 Flash-Lite",
                    "gemini-4-argon": "4 Argon", "gemini-argon": "4 Argon"}

    @classmethod
    async def initialize(cls, *, cdp_url, email, proxy=None, verify=True,
                         init_timeout=60, timeout=240):
        async with BROWSER_UI_LOCK:
            return await cls._initialize_unlocked(cdp_url=cdp_url, email=email,
                proxy=proxy, verify=verify, init_timeout=init_timeout, timeout=timeout)

    @classmethod
    async def _initialize_unlocked(cls, *, cdp_url, email, proxy=None, verify=True,
                                  init_timeout=60, timeout=240):
        """Read current profile cookies in memory; never rewrite exported accounts."""
        from playwright.async_api import async_playwright
        from gemini_webapi import GeminiClient
        obj = cls(None, cdp_url=cdp_url, email=email, timeout=timeout)
        pw = await async_playwright().start()
        try:
            browser = await pw.chromium.connect_over_cdp(cdp_url)
            if len(browser.contexts) != 1:
                raise RuntimeError("Ambiguous browser profile")
            context = browser.contexts[0]
            verified = False
            for page in context.pages:
                if urlparse(page.url).hostname != "gemini.google.com":
                    continue
                labels = await page.get_by_role("link", name="Google Account:").evaluate_all(
                    "els => els.map(e => e.getAttribute('aria-label') || '')")
                if any(f"({email.casefold()})" in label.casefold() for label in labels):
                    verified = True
                    break
            if not verified:
                raise RuntimeError("Open the expected logged-in Gemini account before initialization")
            probe = page
            obj._target_id = await obj._page_id(context, page)
            try:
                picker = probe.get_by_role("button", name=re.compile(r"^Open mode picker, currently "))
                await picker.wait_for(state="visible", timeout=90000)
                await picker.evaluate("el => el.click()")
                # Read a populated menu atomically: hydration may replace the menu
                # between a visibility check and a separate text query.
                handle = await probe.wait_for_function(r"""() => {
                    const items = Array.from(document.querySelectorAll('[role="menuitem"]'))
                        .map(e => e.textContent || '');
                    return items.some(t => /\d[\d.]*\s+(Flash-Lite|Flash|Pro|Argon)\b/.test(t))
                        ? items : false;
                }""")
                options = await handle.json_value()
                await handle.dispose()
                obj.available_browser_models = {}
                for option in options:
                    match = re.search(r"(?:Selected\s+)?(\d[\d.]*\s+(?:Flash-Lite|Flash|Pro|Argon))\b", option)
                    if match:
                        label = match.group(1)
                        obj.available_browser_models["gemini-" + label.lower().replace(" ", "-")] = label
                if not obj.available_browser_models:
                    raise RuntimeError("No supported models discovered in browser picker")
                obj.MODEL_LABELS = {**cls.MODEL_LABELS, **obj.available_browser_models}
            finally:
                if await picker.get_attribute("aria-expanded") == "true":
                    await picker.evaluate("el => el.click()")
            cookies = await context.cookies("https://gemini.google.com")
        finally:
            await pw.stop()
        client = GeminiClient(proxy=proxy, verify=verify)
        for cookie in cookies:
            client.cookies.set(cookie["name"], cookie["value"], domain=cookie["domain"], path=cookie["path"])
        try:
            await client.init(timeout=init_timeout, auto_refresh=False, supplied_cookies_only=True)
        except BaseException:
            await client.close()
            raise
        obj.delegate = client
        obj._session_options = dict(proxy=proxy, verify=verify, init_timeout=init_timeout)
        return obj

    def __init__(self, delegate, *, cdp_url, email, timeout=240):
        parsed = urlparse(cdp_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Browser CDP must use loopback HTTP")
        if not email or "@" not in email:
            raise ValueError("Expected browser account email is required")
        self.delegate = delegate
        self.cdp_url = cdp_url
        self.email = email.casefold()
        self.timeout = timeout
        self._lock = asyncio.Lock()
        self._quarantine_file = Path(__file__).resolve().parent / 'data' / f'browser-uncertain-{parsed.port}.flag'
        self._blocked = self._quarantine_file.exists()
        self.last_conversation_url = None
        self._target_id = None
        self._turns = 0
        self._conversation_model = None
        self._expected_responses = None
        self.turns_per_conversation = 4
        self.allow_basic_override = None
        self.last_actual_model = None

    def quota_state(self):
        state = states().get(self.email)
        return state if state and state['until'] > time.time() else None

    def quota_paused(self):
        return policy()['pause_on_limit'] or self.allow_basic_override is False

    async def check_quota(self, page, *, disabled=False, after_submit=False):
        banner = await inspect_banner(page)
        if banner['text'] or disabled:
            state = record(self.email, banner['text'] or 'Requested model disabled; reset time unavailable', banner['offset'])
        else:
            state = self.quota_state()
        if state and (self.quota_paused() or after_submit):
            raise QuotaExceeded(state)
        return state

    @staticmethod
    async def _page_id(context, page):
        session = await context.new_cdp_session(page)
        try:
            return (await session.send("Target.getTargetInfo"))["targetInfo"]["targetId"]
        finally:
            await session.detach()

    async def _existing_page(self, context):
        for page in context.pages:
            if urlparse(page.url).hostname != "gemini.google.com":
                continue
            target_id = await self._page_id(context, page)
            if self._target_id is None or self._target_id == target_id:
                self._target_id = target_id
                return page
        raise RuntimeError("Dedicated Gemini tab is closed or navigated away; reopen it before use")

    def _needs_new_conversation(self, response_count, model_label, url):
        return (self._expected_responses is None
                or self._turns >= self.turns_per_conversation
                or self._conversation_model != model_label
                or self._expected_responses != response_count
                or (self.last_conversation_url is not None and self.last_conversation_url != url))

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    async def refresh_entitlement(self):
        try:
            return await self._refresh_entitlement()
        except Exception as exc:
            if transient_transport_error(exc):
                raise BrowserNotSubmitted('Entitlement connection temporarily unavailable; not submitted; account status not changed') from exc
            raise

    async def _verify_page_identity(self, page, timeout=30):
        deadline = time.monotonic() + timeout
        while True:
            labels = await page.locator('a[aria-label]').evaluate_all(
                "els => els.map(e => e.getAttribute('aria-label') || '')")
            if check_identity(labels, self.email):
                return
            if time.monotonic() >= deadline:
                raise BrowserNotSubmitted('Browser identity not loaded yet; not submitted; login status unknown')
            await asyncio.sleep(1)

    async def _refresh_entitlement(self):
        """Rebootstrap HTTP entitlement from the verified live browser session.

        Do not repeatedly query an obsolete HTTP delegate: browser cookies may
        rotate while it remains alive. Model/status checks still belong to the
        caller and must not be replaced by a successful browser identity check.
        """
        from playwright.async_api import async_playwright
        from gemini_webapi import GeminiClient
        options = self.__dict__.get('_session_options')
        if options is None:
            raise RuntimeError('Browser entitlement refresh configuration missing')
        async with BROWSER_UI_LOCK:
            async with async_playwright() as pw:
                browser = await pw.chromium.connect_over_cdp(self.cdp_url)
                if len(browser.contexts) != 1:
                    raise RuntimeError('Ambiguous browser profile')
                context = browser.contexts[0]
                page = await self._existing_page(context)
                await self._verify_page_identity(page)
                await self.check_quota(page)
                cookies = await context.cookies('https://gemini.google.com')
        fresh = GeminiClient(proxy=options['proxy'], verify=options['verify'])
        try:
            for cookie in cookies:
                fresh.cookies.set(cookie['name'], cookie['value'], domain=cookie['domain'], path=cookie['path'])
            await fresh.init(timeout=options['init_timeout'], auto_refresh=False, auto_close=False,
                             supplied_cookies_only=True)
        except BaseException:
            await fresh.close()
            raise
        finally:
            cookies.clear()
        previous = self.delegate
        self.delegate = fresh
        if previous is not None:
            await previous.close()

    async def generate_content(self, prompt, *, model, files=None, **kwargs):
        name = getattr(model, "model_name", model)
        if name not in self.MODEL_LABELS:
            raise ValueError(f"Unsupported browser model: {name}; no model substitution")
        available = self.__dict__.get("available_browser_models")
        if available is not None and self.MODEL_LABELS[name] not in available.values():
            raise ValueError("Requested model is absent from this account's browser picker")
        if files:
            raise ValueError("Browser transport currently supports text only")
        if not prompt.strip():
            raise ValueError("Empty prompt")
        async with self._lock:
            state = self.quota_state()
            if state and self.quota_paused():
                raise QuotaExceeded(state)
            if self._blocked:
                raise RuntimeError("Browser transport quarantined after uncertain submission; inspect its tab before restarting")
            async with BROWSER_UI_LOCK:
                return await self._generate(prompt, self.MODEL_LABELS[name])

    async def _generate(self, prompt, model_label):
        from playwright.async_api import async_playwright, TimeoutError as BrowserTimeout
        pw = await async_playwright().start()
        page = None
        submitted = False
        completed = False
        try:
            async with asyncio.timeout(self.timeout):
                browser = await pw.chromium.connect_over_cdp(self.cdp_url)
                if len(browser.contexts) != 1:
                    raise RuntimeError("Ambiguous browser profile")
                page = await self._existing_page(browser.contexts[0])
                page.set_default_timeout(30000)
                box = page.get_by_role("textbox", name="Enter a prompt for Gemini", exact=True)
                await box.wait_for(state="visible")
                if (await box.inner_text()).strip():
                    raise RuntimeError("Gemini input contains an unsent draft; refusing to overwrite it")
                if await page.get_by_role("button", name=re.compile(r"^Stop response")).count():
                    raise RuntimeError("Gemini is already generating; refusing concurrent submission")
                await self._verify_page_identity(page)
                limited = await self.check_quota(page)
                if limited:
                    model_label = '3.5 Flash-Lite'
                count = await page.locator("message-content").count()
                if self._needs_new_conversation(count, model_label, page.url):
                    # Navigate this SAME tab, never open/activate a browser target.
                    if count or urlparse(page.url).path != "/app":
                        await page.goto("https://gemini.google.com/app", wait_until="domcontentloaded")
                        await box.wait_for(state="visible")
                    self._turns = 0
                    self._expected_responses = 0
                    self._conversation_model = model_label
                    self.last_conversation_url = None
                    if await page.locator("message-content").count():
                        raise RuntimeError("New conversation did not clear previous responses")
                picker = page.get_by_role("button", name=re.compile(r"^Open mode picker, currently "))
                # Account/model controls hydrate later than the input box.
                await picker.wait_for(state="visible", timeout=90000)
                expected_label = "Open mode picker, currently " + model_label.split()[-1]
                if await picker.get_attribute("aria-label") != expected_label:
                    await picker.evaluate("el => el.click()")
                    option = page.get_by_role("menuitem", name=re.compile(r"^(Selected )?" + re.escape(model_label) + r"\b"))
                    await option.wait_for(state="visible")
                    if await option.get_attribute("aria-disabled") == "true":
                        await self.check_quota(page, disabled=True)
                        # Explicit administrator opt-in only; before any submission.
                        model_label = '3.5 Flash-Lite'
                        expected_label = 'Open mode picker, currently Flash-Lite'
                        option = page.get_by_role('menuitem', name=re.compile(r'^(Selected )?3\.5 Flash-Lite\b'))
                        await option.wait_for(state='visible')
                        if await option.get_attribute('aria-disabled') == 'true':
                            raise QuotaExceeded(self.quota_state())
                    await option.evaluate("el => el.click()")
                    await option.wait_for(state="hidden", timeout=90000)
                await page.get_by_role("button", name=expected_label, exact=True).wait_for(state="visible")
                selected_label = await picker.get_attribute("aria-label")
                if not selected_label or model_label.split()[-1] not in selected_label:
                    raise RuntimeError("Model selection was not confirmed")
                baseline = await page.locator("message-content").count()
                copies = await page.get_by_role("button", name="Copy", exact=True).count()
                if baseline != self._expected_responses or copies != baseline:
                    raise RuntimeError("Conversation changed or has an unfinished response")
                # DOM input/click avoids OS keyboard, activation and bring_to_front.
                await box.evaluate("""(el, text) => {
                    el.textContent = text;
                    el.dispatchEvent(new InputEvent('input', {
                        bubbles: true, inputType: 'insertText', data: text
                    }));
                }""", prompt)
                if " ".join((await box.inner_text()).split()) != " ".join(prompt.split()):
                    raise RuntimeError("Prompt fill verification failed")
                send = page.get_by_role("button", name="Send message", exact=True)
                await send.wait_for(state="visible")
                if not await send.is_enabled():
                    raise RuntimeError("Send button is disabled; request not submitted")
                # Set before click: a lost acknowledgement must never cause blind resend.
                submitted = True
                await send.evaluate("el => el.click()")
                await self._wait_completed_response(page, baseline, copies)
                response = page.locator("message-content")
                if await response.count() != baseline + 1:
                    raise RuntimeError("Unexpected response count; possible concurrent browser edit")
                await picker.wait_for(state="visible", timeout=90000)
                # A newly displayed quota or auto-downgrade is not a valid requested-model answer.
                # Existing opt-in cooldown is okay only when Flash-Lite was selected before sending.
                if model_label != '3.5 Flash-Lite':
                    await self.check_quota(page, after_submit=True)
                if await picker.get_attribute("aria-label") != selected_label:
                    raise RuntimeError("Model selection changed during generation")
                text = (await response.nth(baseline).inner_text()).strip()
                self.last_conversation_url = page.url
                completed = True
                self._expected_responses = baseline + 1
                from openai_compatible_server import validate_generated_text
                try:
                    validate_generated_text(text)
                except Exception:
                    # Known error text is not a successful turn; rotate next time.
                    self._expected_responses = None
                    raise
                self._turns += 1
                self.last_actual_model = 'gemini-' + model_label.lower().replace(' ', '-')
                return SimpleNamespace(text=text, text_delta=text, images=[], candidates=[],
                                       browser_model_label=model_label, actual_model=self.last_actual_model)
        except TimeoutError as exc:
            if submitted:
                raise RuntimeError('Browser submission outcome unknown: completion deadline exceeded; inspect before retry') from exc
            raise BrowserNotSubmitted('Browser preparation timed out; not submitted') from exc
        except QuotaExceeded:
            # Known quota rejection: preserve cooldown, rotate conversation on next eligible request.
            completed = True
            self._expected_responses = None
            if page:
                picker = page.get_by_role('button', name=re.compile(r'^Open mode picker, currently '))
                if await picker.get_attribute('aria-expanded') == 'true':
                    await picker.evaluate('el => el.click()')
            raise
        except Exception as exc:
            if not submitted and transient_transport_error(exc):
                raise BrowserNotSubmitted('Browser page or connection temporarily unavailable; not submitted') from exc
            raise
        finally:
            if submitted and not completed:
                self._blocked = True
                self._quarantine_file.parent.mkdir(parents=True, exist_ok=True)
                self._quarantine_file.write_text(
                    'Submission outcome unknown. Reconcile existing response before clearing.\n', encoding='utf-8')
                if page:
                    self.last_conversation_url = page.url
            # The existing tab belongs to the user and must remain open.
            await pw.stop()

    async def _wait_completed_response(self, page, baseline, copies):
        """Never reload an in-flight turn. Copy + no Stop + stable text required.

        The caller owns the overall deadline, including model selection. A
        deadline after submission quarantines the account instead of resending.
        """
        previous = None
        stable = 0
        ticks = 0
        while True:
            responses = page.locator('message-content')
            count = await responses.count()
            if count > baseline + 1:
                raise RuntimeError('Unexpected response count; possible concurrent browser edit')
            # A consent dialog can hide the chat from the accessibility tree
            # without removing its completed response from the DOM.
            stop = page.get_by_role('button', name=re.compile(r'^Stop response'), include_hidden=True)
            busy = await stop.count() > 0 and await stop.first.is_visible()
            copy = page.get_by_role('button', name='Copy', exact=True, include_hidden=True).nth(copies)
            ready = count == baseline + 1 and await copy.is_visible() and not busy
            text = (await responses.nth(baseline).inner_text()).strip() if count == baseline + 1 else ''
            if ready and text:
                stable = stable + 1 if text == previous else 1
                if stable >= 3:
                    return
            else:
                stable = 0
            previous = text
            if ticks % 10 == 0:
                await self.check_quota(page, after_submit=True)
            ticks += 1
            await asyncio.sleep(1)

    async def generate_content_stream(self, prompt, **kwargs):
        # Buffered: one final chunk, never a partial answer marked as complete.
        yield await self.generate_content(prompt, **kwargs)

    async def close(self):
        await self.delegate.close()
