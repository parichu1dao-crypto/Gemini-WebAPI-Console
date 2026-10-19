"""Fresh in-memory browser validation. Browser generation never reaches Google."""
import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .errors import NotSubmitted
from .diagnostics import stage
from .protocol import ENDPOINT, SUPPORTED_MODELS, generation_frames, request_model


@dataclass(repr=False)
class Bundle:
    query: dict
    payloads: tuple
    cookies: list


@dataclass(repr=False)
class CaptureGuard:
    query: dict = field(default_factory=dict)
    payloads: dict = field(default_factory=dict)
    done: asyncio.Event = field(default_factory=asyncio.Event)
    failed: bool = False
    armed: bool = False

    async def route(self, route):
        req = route.request
        url = urlsplit(req.url)
        process = 'processSession' in url.path
        # Block legacy generation regardless of method/body or capture phase.
        if 'StreamGenerate' in url.path:
            self.failed = True
            self.done.set()
            await route.abort()
            return
        if not process:
            # Once armed, no unknown POST can silently become browser fallback.
            if self.armed and req.method.upper() not in ('GET', 'HEAD', 'OPTIONS'):
                await route.abort()
            else:
                await route.continue_()
            return
        try:
            if url.scheme != 'https' or url.netloc != urlsplit(ENDPOINT).netloc or url.path != '/v1/processSession':
                raise ValueError('Unexpected channel endpoint')
            query = parse_qs(url.query, keep_blank_values=True)
            if any(len(v) != 1 for v in query.values()):
                raise ValueError('Ambiguous query')
            if '$httpHeaders' in query:
                incoming = {k: v[0] for k, v in query.items()}
                if self.query and self.query.get('$httpHeaders') != incoming['$httpHeaders']:
                    raise ValueError('Channel headers changed')
                self.query = incoming
            form = parse_qs(req.post_data or '', keep_blank_values=True)
            if req.method.upper() == 'POST' and form == {'count': ['0']}:
                await route.continue_()  # Empty handshake cannot generate.
                return
            if req.method.upper() == 'GET' and not form:
                await route.continue_()  # Receive-only browser channel.
                return
            if req.method.upper() != 'POST' or any(len(v) != 1 for v in form.values()):
                raise ValueError('Invalid forward request')
            count, offset = int(form['count'][0]), int(form['ofs'][0])
            if not 1 <= count <= 2 or offset < 0:
                raise ValueError('Unexpected frame count')
            if set(form) != {'count', 'ofs'} | {f'req{i}___data__' for i in range(count)}:
                raise ValueError('Unexpected forward fields')
            for i in range(count):
                index = offset + i
                value = form[f'req{i}___data__'][0]
                if index not in (0, 1) or (index in self.payloads and self.payloads[index] != value):
                    raise ValueError('Changed or unexpected captured frame')
                self.payloads[index] = value
            if set(self.payloads) == {0, 1}:
                self.done.set()
            # Only a local synthetic ACK. Never continue a forward body.
            await route.fulfill(status=200, body='[1,0,0]', content_type='text/plain')
        except Exception:
            self.failed = True
            self.done.set()
            await route.abort()


def check_quota(account, email):
    import quota_policy
    limits = quota_policy.states()
    if any(float(limits.get(key, {}).get('until', 0)) > time.time() for key in (account, email)):
        raise NotSubmitted('Active account quota; not submitted')


async def capture(account, account_dir, prompt, model_id):
    from production_accounts import binding
    from quota_policy import inspect_banner
    from playwright.async_api import async_playwright

    stage('capture_started')

    if model_id not in SUPPORTED_MODELS:
        raise NotSubmitted('No observed UI mapping for requested model; not submitted')
    account_dir = Path(account_dir)
    if not isinstance(account, str) or not re.fullmatch(r'[\w.-]+', account) or account in ('.', '..'):
        raise NotSubmitted('Invalid account name; not submitted')
    enrolled = binding(account_dir.parent, account)
    expected = json.loads((account_dir / f'{account}.json').read_text(encoding='utf-8-sig'))['email'].casefold()
    if not expected or '@' not in expected or enrolled['email'].casefold() != expected:
        raise NotSubmitted('Account binding mismatch; not submitted')
    check_quota(account, expected)
    pw = await async_playwright().start()
    page = None
    try:
        browser = await pw.chromium.connect_over_cdp(enrolled['cdp_url'], timeout=15000)
        if len(browser.contexts) != 1:
            raise NotSubmitted('Ambiguous browser context; not submitted')
        context = browser.contexts[0]
        for existing in context.pages:
            if urlsplit(existing.url).hostname == 'gemini.google.com':
                if await existing.get_by_role('button', name=re.compile(r'Stop response|停止回复|停止回答', re.I)).count():
                    raise NotSubmitted('Existing browser generation is active; not submitted')
        page = await context.new_page()
        # Gemini may render the account chip well before its model picker/editor.
        # The outer request deadline still bounds this entire validation phase.
        page.set_default_timeout(45000)
        guard = CaptureGuard()
        await page.route('**/*', guard.route)
        cdp = await context.new_cdp_session(page)
        await cdp.send('Network.enable')
        await cdp.send('Network.setBypassServiceWorker', {'bypass': True})
        await page.goto('https://gemini.google.com/app', wait_until='domcontentloaded', timeout=45000)
        await page.wait_for_function("email => [...document.querySelectorAll('a[aria-label]')].some(e => (e.getAttribute('aria-label') || '').toLowerCase().includes('('+email+')'))", arg=expected)
        stage('identity_verified')
        if (await inspect_banner(page))['text']:
            raise NotSubmitted('Visible account quota; not submitted')
        picker = page.locator('button[aria-label*="mode picker"]')
        await picker.wait_for(state='visible')
        if not re.search(SUPPORTED_MODELS[model_id], await picker.get_attribute('aria-label') or ''):
            # Existing production BrowserTransport observes this menuitem UI.
            # Avoid opening it when the lab-verified picker already shows Pro.
            await picker.click()
            choices = page.get_by_role('menuitem').filter(has_text=re.compile(SUPPORTED_MODELS[model_id]))
            await choices.first.wait_for(state='visible')
            if await choices.count() != 1 or not await choices.is_enabled() or await choices.get_attribute('aria-disabled') == 'true':
                raise NotSubmitted('Requested model unavailable or ambiguous; not submitted')
            await choices.click()
        if not re.search(SUPPORTED_MODELS[model_id], await picker.get_attribute('aria-label') or ''):
            raise NotSubmitted('Requested model not selected; not submitted')
        if (await inspect_banner(page))['text']:
            raise NotSubmitted('Visible account quota; not submitted')
        editor = page.locator('[role="textbox"][contenteditable="true"]')
        await editor.wait_for(state='visible')
        guard.armed = True
        if '\n' in prompt:
            # Observed Gemini editor exposes Quill's public setText/getText API.
            # Browser insertText creates U+200B placeholders for blank lines.
            # Update the editor document, not raw DOM or the captured request.
            inserted = await editor.evaluate("""(el, text) => {
                const q = el.parentElement && el.parentElement.__quill;
                if (!q || typeof q.setText !== 'function' || typeof q.getText !== 'function') return false;
                q.setText(text, 'user');
                if (q.getText() !== text && q.getText() !== text + '\\n') return false;
                return true;
            }""", prompt)
            if not inserted:
                raise NotSubmitted('Plain-text editor API unavailable or changed; not submitted')
        else:
            await editor.fill(prompt)
        # contenteditable innerText reflects layout (extra block newlines/NBSP),
        # not the serialized request. Validate the EXACT captured wire prompt
        # below (allowing only documented Markdown-list escapes) before HTTP
        # submission; never rewrite the captured payload or validation proof.
        await page.get_by_role('button', name='Send message', exact=True).click()
        await asyncio.wait_for(guard.done.wait(), 45)
        if guard.failed or not guard.query or set(guard.payloads) != {0, 1}:
            raise NotSubmitted('Incomplete browser packet capture; not submitted')
        payloads = generation_frames([guard.payloads[0], guard.payloads[1]], prompt)
        if request_model(guard.query) != model_id:
            raise NotSubmitted('Captured model does not match requested model; not submitted')
        stage('model_verified')
        check_quota(account, expected)
        if (await inspect_banner(page))['text']:
            raise NotSubmitted('Visible account quota; not submitted')
        # Fresh browser cookies only. No persisted SDK jar or credential writes.
        result = Bundle(guard.query, payloads, await context.cookies())
        stage('bundle_captured')
        return result
    finally:
        try:
            if page is not None:
                await asyncio.wait_for(page.close(), 3)
        finally:
            # Disconnect Playwright, NEVER browser.close/context.close.
            await asyncio.wait_for(pw.stop(), 3)
