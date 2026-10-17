import argparse
import os
import asyncio
import base64
import json
import mimetypes
import signal
import sys
import tempfile
import threading
import time
import uuid
from contextvars import ContextVar
from functools import wraps
from contextlib import asynccontextmanager
from concurrent.futures import TimeoutError as FutureTimeoutError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import url2pathname
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
ACCOUNT_DIR = ROOT / "data" / "accounts"
ACCOUNT_ATTEMPT_LOG = ROOT / "openai_account_attempts.jsonl"
ACCOUNT_ATTEMPT_LOG_LOCK = threading.Lock()
sys.path.insert(0, str(ROOT / "src"))

from gemini_webapi.constants import AccountStatus, Model  # noqa: E402
from gemini_webapi.exceptions import TimeoutError as GeminiTimeoutError
from gemini_webapi.exceptions import AuthError
from curl_cffi.requests.exceptions import ConnectionError as CurlConnectionError, Timeout as CurlTimeout
from gemini_webapi.request_diagnostics import diagnostic_sink, emit  # noqa: E402
from local_account_client import GeminiClient, load_account_cookie_session, session_version, save_account_session  # noqa: E402
from cookie_model_catalog import CookieModelCatalog, ModelCatalogError
from cookie_transport import generate as cookie_generate, generate_stream as cookie_generate_stream, SubmissionUncertain
import operations

GATE = operations.AdmissionGate()
TRACE = ContextVar('operational_request', default=None)
RESTART_REQUESTED = False


class PoolUnavailable(RuntimeError):
    """No eligible account, or admission queue exhausted; nothing was sent."""

    def __init__(self, message, *, accounts=None, retry_after=60):
        super().__init__(message)
        self.accounts = accounts or {}
        self.retry_after = max(1, int(retry_after))


class StartupError(RuntimeError):
    startup_only = True

    def __init__(self, message, *, cleanup_complete=False, retryable=False):
        super().__init__(message)
        self.cleanup_complete = bool(cleanup_complete)
        self.retryable = bool(retryable and self.cleanup_complete)


class EndpointDisabled(RuntimeError):
    pass


class CleanupIncomplete(RuntimeError):
    """Do not restart while cancelled work or session cleanup is outstanding."""


REQUEST_BUDGET = ContextVar('request_budget', default=None)
CLEANUP_TIMEOUT = 10.0


class RequestBudget:
    def __init__(self, timeout):
        self.deadline = time.monotonic() + timeout
        self.submitted = False

    def expired(self):
        if self.submitted:
            return SubmissionUncertain('submission outcome unknown; request deadline expired')
        return PoolUnavailable('Request deadline expired; not submitted')

    def remaining(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise self.expired()
        return remaining


async def within_deadline(coro, budget):
    token = REQUEST_BUDGET.set(budget)
    try:
        remaining = budget.deadline - time.monotonic()
        if remaining <= 0:
            coro.close()
            raise budget.expired()
        async with asyncio.timeout(remaining):
            return await coro
    except TimeoutError:
        raise budget.expired() from None
    finally:
        REQUEST_BUDGET.reset(token)


def request_deadline(fn):
    @wraps(fn)
    async def wrapped(*args, **kwargs):
        if REQUEST_BUDGET.get() is not None:
            return await fn(*args, **kwargs)
        return await within_deadline(
            fn(*args, **kwargs), RequestBudget(require_state().args.request_timeout))
    return wrapped


def http_generation_timeout(headers, configured):
    """Finish before the caller disconnects, including legacy 300s clients."""
    raw = headers.get('X-Request-Timeout-Seconds')
    if raw is None and headers.get('X-Client-Name') == 'webnovel-translation':
        raw = '300'
    if raw is None:
        return configured
    import math
    seconds = float(raw)
    if not math.isfinite(seconds) or seconds < 31:
        raise ValueError('X-Request-Timeout-Seconds must be finite and at least 31')
    return min(configured, seconds - 30)


STRICT_PRO_MODEL = Model.BASIC_PRO.model_name
STRICT_PRO_MODEL_ID = Model.BASIC_PRO.model_id


class StrictProUnavailable(RuntimeError):
    """Raised when a request cannot be proven eligible for Gemini Pro."""


def validate_generated_text(text: str) -> None:
    normalized = " ".join(text.split()).casefold().rstrip(".!。")
    if not normalized or normalized in {
        "i seem to be encountering an error. can i try something else for you?",
        "sorry, something went wrong. please try your request again",
        "i encountered an error doing what you asked. could you try again?",
        "i'm having a hard time fulfilling your request. can i help you with something else instead?",
    }:
        raise RuntimeError("Gemini returned empty content or a service error message")


class ServerState:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.account_dir = Path(args.account_dir).resolve()
        self.clients: list[tuple[str, Any]] = []
        self.next_index = 0
        self.client_lock = threading.Lock()
        self.account_health: dict[str, dict[str, Any]] = {}
        self.startup_errors = {}
        self.account_locks: dict[str, asyncio.Lock] = {}
        self._owned_clients = []
        self._credential_versions = {}
        self._session_checked_at = {}
        self._session_recovery_at = {}
        self._tasks = set()
        self._cleanup_failed = False
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run_loop, daemon=True)

    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def start(self):
        self.thread.start()
        account_names = self.resolve_accounts()
        transient_failure = False
        for account_name in account_names:
            try:
                client = self.run_async(
                    self.initialize_client(account_name), timeout=self.args.init_timeout
                )
            except Exception as exc:
                # Only explicit temporary failures permit service startup retry.
                # Here PoolUnavailable is the initialization deadline, not pool admission.
                transient_failure |= isinstance(exc, (
                    ConnectionError, TimeoutError, GeminiTimeoutError,
                    CurlConnectionError, CurlTimeout, PoolUnavailable))
                self.startup_errors[account_name] = type(exc).__name__
                print(f"[WARN] account {account_name} init failed: {type(exc).__name__}")
                if isinstance(exc, CleanupIncomplete):
                    break
                continue

            if self.args.strict_pro:
                eligible, reason, _ = self.strict_pro_eligibility(client)
                if not eligible:
                    self.startup_errors[account_name] = reason
                    print(f"[WARN] account {account_name} excluded by strict Pro mode: {reason}")
                    try:
                        self.run_async(client.close(), timeout=10)
                    except Exception:
                        self._cleanup_failed = True
                        break
                    continue

            self.clients.append((account_name, client))
            client.timeout = self.args.request_timeout
            from account_health import load
            self.account_health[account_name] = load(account_name)
            self.account_locks[account_name] = asyncio.Lock()
            print(f"[OK] account {account_name} initialized")

        if not self.clients or self._cleanup_failed:
            cleaned = self.stop()
            raise StartupError(
                "Gemini startup unavailable or cleanup incomplete. Check accounts under "
                f"{self.account_dir}", cleanup_complete=cleaned,
                retryable=transient_failure
            )

    async def recover_enrolled_session(self, account_name):
        last = self._session_recovery_at.get(account_name, float('-inf'))
        if time.monotonic() - last < 60:
            return False
        self._session_recovery_at[account_name] = time.monotonic()
        try:
            from session_recovery import recover_session
            recovered = await asyncio.to_thread(recover_session, account_name, self.account_dir)
            emit('session_reimported' if recovered else 'session_reimport_unavailable', account=account_name)
            return recovered
        except Exception:
            emit('session_reimport_failed', account=account_name)
            return False

    async def initialize_client(self, account_name, *, allow_browser_recovery=True):
        # Own the client before the first await, including cancelled init calls.
        self._credential_versions[account_name] = session_version(account_name, self.account_dir)
        cookies, proxy = load_account_cookie_session(
            account_name, ignore_proxy=self.args.ignore_proxy, account_dir=self.account_dir)
        if not self.args.ignore_proxy and self.args.proxy:
            proxy = self.args.proxy
        client = GeminiClient(proxy=proxy, verify=not self.args.skip_verify)
        self._owned_clients.append(client)
        client.cookies = cookies
        try:
            await client.init(timeout=self.args.init_timeout,
                              auto_refresh=self.args.auto_refresh,
                              supplied_cookies_only=True)
            if client.account_status != AccountStatus.AVAILABLE:
                raise AuthError('Account is not authenticated; explicitly refresh its login session')
            self._credential_versions[account_name] = save_account_session(
                account_name, self.account_dir, client.cookies, self._credential_versions[account_name])
            self._session_checked_at[account_name] = time.monotonic()
            return client
        except BaseException as exc:
            try:
                await asyncio.wait_for(client.close(), CLEANUP_TIMEOUT)
            except BaseException:
                self._cleanup_failed = True
                raise CleanupIncomplete('Cancelled initialization cleanup incomplete') from None
            if isinstance(exc, AuthError) and allow_browser_recovery and await self.recover_enrolled_session(account_name):
                return await self.initialize_client(account_name, allow_browser_recovery=False)
            raise

    def resolve_accounts(self) -> list[str]:
        if self.args.account:
            return [self.args.account]

        raw_accounts = [part.strip() for part in self.args.accounts.split(",") if part.strip()]
        if raw_accounts and raw_accounts != ["all"]:
            return raw_accounts

        return sorted(path.stem for path in self.account_dir.glob("*.json") if path.is_file())

    def strict_pro_eligibility(self, client: Any) -> tuple[bool, str, Any | None]:
        """Return whether the current account can be trusted for strict Pro requests."""

        status = getattr(client, "account_status", None)
        if status != AccountStatus.AVAILABLE:
            status_name = getattr(status, "name", str(status or "UNKNOWN"))
            return False, f"account status is {status_name}, expected AVAILABLE", None

        models = client.list_models() or []
        pro_model = next(
            (
                model
                for model in models
                if getattr(model, "model_id", "") == STRICT_PRO_MODEL_ID
                or getattr(model, "model_name", "") == STRICT_PRO_MODEL
            ),
            None,
        )
        if pro_model is None:
            return False, "Gemini Pro is absent from the account's dynamic model list", None
        if not bool(getattr(pro_model, "is_available", False)):
            return False, "Gemini Pro is listed but marked unavailable", None

        capacity = int(getattr(pro_model, "capacity", 1) or 1)
        if not self.args.allow_free_pro and capacity <= 1:
            return (
                False,
                "paid Pro entitlement is not confirmed (capacity indicates a free account)",
                None,
            )

        return True, "ok", pro_model

    async def prepare_strict_pro_model(self, account_name: str, client: Any) -> Any:
        """Refresh entitlement immediately before generation and return the dynamic Pro model."""

        registry = getattr(client, "_model_registry", None)
        snapshot = dict(registry) if isinstance(registry, dict) else None
        old_status = getattr(client, 'account_status', None)
        if isinstance(registry, dict):
            registry.clear()
        try:
            await client._fetch_user_status()
        except BaseException:
            if snapshot is not None:
                client._model_registry = registry
                registry.clear()
                registry.update(snapshot)
            client.account_status = old_status
            # Snapshot permits future admission, never generation on this failure.
            raise
        eligible, reason, pro_model = self.strict_pro_eligibility(client)
        if not eligible:
            raise StrictProUnavailable(f"{account_name}: {reason}")
        return pro_model

    async def ensure_authenticated(self, account_name, client, budget):
        if client.account_status == AccountStatus.AVAILABLE:
            return
        if not await self.recover_enrolled_session(account_name):
            raise AuthError('Account session invalid; verified browser reimport unavailable; not submitted')
        await client.close()
        cookies, _ = load_account_cookie_session(account_name, account_dir=self.account_dir)
        client.cookies.clear()
        client.cookies = cookies
        self._credential_versions[account_name] = session_version(account_name, self.account_dir)
        await client.init(timeout=budget.remaining(), supplied_cookies_only=True,
                          auto_refresh=False, auto_close=False, verbose=False)
        if client.account_status != AccountStatus.AVAILABLE:
            raise AuthError('Account remains unauthenticated after verified import; not submitted')
        self._session_checked_at[account_name] = time.monotonic()

    async def prepare_request_model(self, account_name, client, model_name):
        budget = REQUEST_BUDGET.get()
        # Keep per-account cookies, but do not reuse an old page access token /
        # TLS session across independent temporary conversations. This is a
        # pre-dispatch handshake, never a second generation attempt.
        if account_name in getattr(self, '_session_checked_at', {}) and getattr(client, '_running', False):
            await client.close()
        reconnected = False
        if getattr(client, '_running', True) is False:
            await client.init(timeout=budget.remaining(), supplied_cookies_only=True,
                              auto_refresh=False, auto_close=False, verbose=False)
            reconnected = True
            await self.ensure_authenticated(account_name, client, budget)
        checked = getattr(self, '_session_checked_at', {})
        # Account lock is already held. Refresh only this session, never the
        # SDK's shared cookie cache, and never race a generation in progress.
        if account_name in checked and reconnected:
            checked[account_name] = time.monotonic()
        elif account_name in checked:
            # An open browser can rotate the same account's credentials between
            # turns. Check current RPC authorization before spending generation.
            await client._fetch_user_status()
            await self.ensure_authenticated(account_name, client, budget)
        budget.remaining()
        if self.args.strict_pro:
            return await self.prepare_strict_pro_model(account_name, client)
        return CookieModelCatalog.from_client(client).resolve(model_name)

    def request_lock(self, account_name: str) -> asyncio.Lock:
        lock = self.account_locks.get(account_name)
        if lock is None:
            lock = asyncio.Lock()
            self.account_locks[account_name] = lock
        return lock

    def stop(self):
        if not self.thread.is_alive():
            if not self.loop.is_closed():
                self.loop.close()
            return not self._cleanup_failed
        async def cleanup():
            pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            for task in pending:
                task.cancel()
            if pending:
                done, outstanding = await asyncio.wait(pending, timeout=CLEANUP_TIMEOUT)
                for task in done:
                    if not task.cancelled():
                        task.exception()  # Consume errors from cancelled operations.
                if outstanding:
                    return False
            clients = {id(c): c for c in self._owned_clients}
            clients.update({id(c): c for _, c in self.clients})
            for client in clients.values():
                try:
                    await asyncio.wait_for(client.close(), CLEANUP_TIMEOUT)
                except BaseException:
                    return False
            return not self._cleanup_failed
        future = asyncio.run_coroutine_threadsafe(cleanup(), self.loop)
        try:
            cleaned = future.result(timeout=CLEANUP_TIMEOUT * (len(self._owned_clients) + 2))
        except Exception:
            cleaned = False
        if not cleaned:
            self._cleanup_failed = True
            return False  # Keep the loop alive for outstanding cleanup; never retry startup.
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)
        if not self.thread.is_alive() and not self.loop.is_closed():
            self.loop.close()
        return not self.thread.is_alive() and self.loop.is_closed()

    def account_available(self, account_name):
        from account_control import enabled
        if not enabled(account_name):
            return False
        from quota_policy import states
        now = time.time()
        health = self.account_health.get(account_name, {})
        if float(health.get('cooldown_until') or 0) > now:
            return False
        limits = states()
        identity = json.loads((self.account_dir / f'{account_name}.json').read_text(
            encoding='utf-8-sig')).get('email', '').casefold()
        return not any(float(limits.get(key, {}).get('until') or 0) > now
                       for key in (account_name, identity))

    def next_client(self, model_name=None) -> tuple[str, Any]:
        if not self.clients:
            raise RuntimeError("No initialized Gemini accounts are available")

        with self.client_lock:
            total = len(self.clients)
            for offset in range(total):
                index = (self.next_index + offset) % total
                account_name, client = self.clients[index]
                self.account_health.setdefault(
                    account_name, {"failures": 0, "cooldown_until": 0.0}
                )
                if model_name:
                    try:
                        CookieModelCatalog.from_client(client).resolve(model_name)
                    except ModelCatalogError:
                        continue
                if self.account_available(account_name):
                    self.next_index = index + 1
                    return account_name, client

            details = {name: self.admission_status(name, client, model_name)
                       for name, client in self.clients}
            waits = [d['retry_after'] for d in details.values() if d['retry_after']]
            raise PoolUnavailable('No eligible account; inspect account reasons; not submitted',
                                  accounts=details, retry_after=min(waits) if waits else 60)

    def admission_status(self, name, client, model_name=None):
        from quota_policy import states
        now = time.time()
        health = self.account_health.get(name, {})
        until = float(health.get('cooldown_until') or 0)
        reason = 'failure_cooldown' if until > now else 'available'
        identity = json.loads((self.account_dir / f'{name}.json').read_text(
            encoding='utf-8-sig')).get('email', '').casefold()
        limits = states()
        quota_until = max(float(limits.get(k, {}).get('until') or 0) for k in (name, identity))
        if quota_until > now:
            until = max(until, quota_until)
            reason = 'quota_limit'
        if model_name:
            try:
                CookieModelCatalog.from_client(client).resolve(model_name)
            except ModelCatalogError:
                reason = 'model_unavailable'
        from account_control import enabled
        if not enabled(name):
            reason = 'administrator_stopped'
        return dict(reason=reason, available=reason == 'available',
                    retry_after=max(0, int(min(43200, until - now + 1))) if until > now else 0,
                    cooldown_until=until if until > now else None,
                    last_error_type=health.get('last_error_type'))

    def mark_account_success(self, account_name: str) -> None:
        versions = getattr(self, '_credential_versions', {})
        if account_name in versions:
            client = next(c for n, c in self.clients if n == account_name)
            try:
                versions[account_name] = save_account_session(account_name, self.account_dir,
                    client.cookies, versions[account_name])
            except OSError:
                emit('session_save_failed', account=account_name)
        with self.client_lock:
            health = self.account_health.setdefault(
                account_name, {"failures": 0, "cooldown_until": 0.0}
            )
            health["failures"] = 0
            health["cooldown_until"] = 0.0
            from account_health import save
            save(account_name, health)

    def mark_account_failure(self, account_name: str, error: Exception) -> None:
        if isinstance(error, PoolUnavailable):
            return  # Queue rejection is not an account failure.
        from gemini_webapi.exceptions import UsageLimitExceeded
        if isinstance(error, UsageLimitExceeded):
            from quota_policy import record
            identity = json.loads((self.account_dir / f'{account_name}.json').read_text(encoding='utf-8-sig')).get('email', '').casefold()
            record(identity or account_name, 'Cookie RPC usage limit exceeded')
        threshold = max(1, int(getattr(self.args, "account_failure_threshold", 1)))
        cooldown = max(1.0, float(getattr(self.args, "account_cooldown", 900)))
        with self.client_lock:
            health = self.account_health.setdefault(
                account_name, {"failures": 0, "cooldown_until": 0.0}
            )
            health["failures"] = int(health.get("failures") or 0) + 1
            health['last_error_type'] = type(error).__name__
            if health["failures"] >= threshold:
                health["cooldown_until"] = time.time() + cooldown
                if not getattr(self.args, "quiet", False):
                    print(
                        f"[WARN] account {account_name} cooling down for {int(cooldown)}s "
                        f"after {health['failures']} failure(s): {str(error)[:240]}"
                    )
            from account_health import save
            save(account_name, health)

    def run_async(self, coro, timeout: float | None = None):
        budget = RequestBudget(self.args.request_timeout if timeout is None else timeout)
        finished = threading.Event()
        async def run():
            task = asyncio.current_task()
            self._tasks.add(task)
            try:
                return await within_deadline(coro, budget)
            finally:
                self._tasks.discard(task)
                finished.set()
        future = asyncio.run_coroutine_threadsafe(run(), self.loop)
        try:
            # The coroutine owns the deadline. Allow its finally blocks to drain.
            return future.result(timeout=max(0, budget.deadline - time.monotonic()) + CLEANUP_TIMEOUT)
        except FutureTimeoutError:
            future.cancel()
            if not finished.wait(CLEANUP_TIMEOUT):
                self._cleanup_failed = True
                raise CleanupIncomplete('Cancellation cleanup incomplete; do not restart') from None
            raise budget.expired() from None

    @asynccontextmanager
    async def account_request(self, account_name, request_id):
        def sink(fields):
            if TRACE.get():
                operations.event(TRACE.get(), fields.get('stage','unknown'), **{
                    k:v for k,v in fields.items() if k in {'http_status','wait_sec','elapsed_sec','code','submitted'}})
            budget = REQUEST_BUDGET.get()
            if budget is not None and fields.get('stage') == 'generation_dispatched':
                budget.submitted = True
            record = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                      "request_id": request_id, "account": account_name, **fields}
            with ACCOUNT_ATTEMPT_LOG_LOCK:
                with (ROOT / "openai_request_diagnostics.jsonl").open("a", encoding="utf-8") as f:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
        token = diagnostic_sink.set(sink)
        queued = time.monotonic()
        started = None
        try:
            emit("queued")
            lock = self.request_lock(account_name)
            try:
                queue_timeout = getattr(getattr(self, 'args', None), 'queue_timeout', 2)
                budget = REQUEST_BUDGET.get()
                if budget is not None:
                    queue_timeout = min(queue_timeout, budget.remaining())
                await asyncio.wait_for(lock.acquire(), timeout=queue_timeout)
            except TimeoutError:
                raise PoolUnavailable('Account queue busy; not submitted') from None
            try:
                started = time.monotonic()
                emit("account_acquired", wait_sec=round(started - queued, 3))
                if budget is not None:
                    budget.remaining()
                managed = hasattr(self, 'account_health')
                if managed and not self.account_available(account_name):
                    raise PoolUnavailable('Account now cooling down or quota limited; not submitted')
                try:
                    yield
                    if budget is not None:
                        budget.remaining()
                except BaseException as exc:
                    if managed and isinstance(exc, Exception):
                        self.mark_account_failure(account_name, exc)
                    elif managed and isinstance(exc, asyncio.CancelledError) and budget and budget.submitted:
                        self.mark_account_failure(account_name, budget.expired())
                    raise
                else:
                    if managed:
                        self.mark_account_success(account_name)
            finally:
                lock.release()
                emit("account_released", active_sec=round(time.monotonic() - started, 3))
        except asyncio.CancelledError:
            emit("cancelled", during="queued" if started is None else "active")
            raise
        except Exception as exc:
            emit("request_error", error_type=type(exc).__name__)
            raise
        finally:
            diagnostic_sink.reset(token)


STATE: ServerState | None = None


def now_ts() -> int:
    return int(time.time())


def write_account_attempt_log(event: dict[str, Any]) -> None:
    record = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        **event,
    }
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    with ACCOUNT_ATTEMPT_LOG_LOCK:
        with ACCOUNT_ATTEMPT_LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


def model_names() -> list[str]:
    if STATE and STATE.args.strict_pro:
        return [STRICT_PRO_MODEL]

    names = []
    if STATE:
        for _, client in STATE.clients:
            for entry in CookieModelCatalog.from_client(client).entries:
                if entry.available and entry.api_name not in names:
                    names.append(entry.api_name)
    return names


def normalize_messages(messages: list[dict[str, Any]]) -> str:
    if len(messages) == 1 and messages[0].get('role', 'user') == 'user' and isinstance(messages[0].get('content'), str):
        return messages[0]['content'].strip()
    lines: list[str] = []
    for message in messages:
        role = str(message.get("role") or "user")
        content = message.get("content", "")
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        parts.append(str(item.get("text") or ""))
                    elif item.get("type") == "image_url":
                        image_url = item.get("image_url") or {}
                        if isinstance(image_url, dict):
                            parts.append(f"[image_url: {image_url.get('url', '')}]")
                else:
                    parts.append(str(item))
            content_text = "\n".join(part for part in parts if part)
        else:
            content_text = str(content)

        if content_text.strip():
            lines.append(f"{role}: {content_text.strip()}")

    return "\n\n".join(lines).strip()


def image_suffix_from_media_type(media_type: str) -> str:
    return mimetypes.guess_extension(media_type.split(";")[0].strip()) or ".png"


def materialize_image_url(url: str, temp_dir: Path) -> str:
    if url.startswith("data:"):
        header, encoded = url.split(",", 1)
        media_type = header[5:].split(";")[0] or "image/png"
        suffix = image_suffix_from_media_type(media_type)
        image_path = temp_dir / f"input_image_{len(list(temp_dir.iterdir()))}{suffix}"
        image_path.write_bytes(base64.b64decode(encoded))
        return str(image_path)

    parsed = urlparse(url)
    if parsed.scheme == "file":
        path = Path(url2pathname(parsed.path.lstrip("/") if parsed.netloc else parsed.path))
        if not path.is_file():
            raise ValueError(f"image file does not exist: {path}")
        return str(path)

    if parsed.scheme in ("http", "https"):
        request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(request, timeout=60) as response:
            content_type = response.headers.get("content-type", "image/png")
            suffix = image_suffix_from_media_type(content_type)
            image_path = temp_dir / f"remote_image_{len(list(temp_dir.iterdir()))}{suffix}"
            image_path.write_bytes(response.read())
            return str(image_path)

    path = Path(url)
    if path.is_file():
        return str(path)

    raise ValueError(
        "image_url must be a local path, file:// URL, data:image base64 URL, or http(s) URL"
    )


def normalize_messages_with_files(
    messages: list[dict[str, Any]], temp_dir: Path
) -> tuple[str, list[str]]:
    if len(messages) == 1 and messages[0].get('role', 'user') == 'user' and isinstance(messages[0].get('content'), str):
        return messages[0]['content'].strip(), []
    lines: list[str] = []
    files: list[str] = []

    for message in messages:
        role = str(message.get("role") or "user")
        content = message.get("content", "")
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        parts.append(str(item.get("text") or ""))
                    elif item.get("type") == "image_url":
                        image_url = item.get("image_url") or {}
                        raw_url = (
                            image_url.get("url")
                            if isinstance(image_url, dict)
                            else str(image_url)
                        )
                        if raw_url:
                            files.append(materialize_image_url(str(raw_url), temp_dir))
                            parts.append("[attached image]")
                else:
                    parts.append(str(item))
            content_text = "\n".join(part for part in parts if part)
        else:
            content_text = str(content)

        if content_text.strip():
            lines.append(f"{role}: {content_text.strip()}")

    return "\n\n".join(lines).strip(), files


def require_state() -> ServerState:
    if STATE is None or not STATE.clients:
        raise RuntimeError("Server is not initialized")
    return STATE


async def prepare_messages(messages, directory):
    # URL/file materialization must not block the account/event-loop scheduler.
    worker = asyncio.create_task(asyncio.to_thread(normalize_messages_with_files, messages, directory))
    try:
        result = await asyncio.shield(worker)
    except asyncio.CancelledError:
        # A thread cannot be cancelled. Keep its temp directory alive until it exits.
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not worker.cancelled():
            worker.exception()
        raise
    REQUEST_BUDGET.get().remaining()
    return result


def openai_error(message: str, status: int = 500, error_type: str = "server_error"):
    return status, {
        "error": {
            "message": message,
            "type": error_type,
            "param": None,
            "code": None,
        }
    }


MODEL_ALIASES = {
    "gemini-3.1-pro-low": "gemini-3-pro",
    "gemini-3.1-pro-preview": "gemini-3-pro",
    "gemini-3-flash-preview": "gemini-3-flash",
}


def normalize_model_name(model: Any, default_model: str) -> str:
    model_name = str(model or default_model)
    return MODEL_ALIASES.get(model_name, model_name)


def resolve_requested_model(payload: dict[str, Any], state: ServerState) -> tuple[str, str]:
    requested_model = str(payload.get("model") or state.args.default_model)
    normalized_model = normalize_model_name(requested_model, state.args.default_model)
    if state.args.strict_pro and normalized_model != STRICT_PRO_MODEL:
        raise ValueError(
            f"Strict Pro mode only accepts '{STRICT_PRO_MODEL}', got '{requested_model}'"
        )
    return requested_model, normalized_model


@request_deadline
async def create_chat_completion(payload: dict[str, Any]) -> dict[str, Any]:
    state = require_state()
    requested_model, model_name = resolve_requested_model(payload, state)
    messages = payload.get("messages") or []
    if not isinstance(messages, list) or not messages:
        raise ValueError("messages must be a non-empty array")

    with tempfile.TemporaryDirectory(prefix="gemini_openai_images_") as tmp:
        prompt, files = await prepare_messages(messages, Path(tmp))
        if not prompt:
            raise ValueError("messages did not contain any text content")

        account_name, client = state.next_client(model_name)
        model: Any = model_name
        request_started = time.time()
        request_id = f"chat-{uuid.uuid4().hex}"
        write_account_attempt_log({
            "request_id": request_id,
            "endpoint": "/v1/chat/completions",
            "stream": False,
            "event": "begin",
            "account": account_name,
            "requested_model": requested_model,
            "model": model_name,
            "prompt_chars": len(prompt),
            "file_count": len(files or []),
        })
        try:
            async with state.account_request(account_name, request_id):
                model = await state.prepare_request_model(account_name, client, model_name)
                output = await cookie_generate(
                    client, prompt,
                    files=files or None,
                    model=model,
                    timeout=REQUEST_BUDGET.get().remaining(),
                )
                emit("output_received", response_chars=len(output.text or ""))
                try:
                    validate_generated_text(output.text or "")
                except Exception:
                    emit("output_rejected", reason="empty_or_service_error")
                    raise
                emit("output_validated", response_chars=len(output.text or ""))
        except Exception as exc:
            write_account_attempt_log({
                "request_id": request_id,
                "endpoint": "/v1/chat/completions",
                "stream": False,
                "event": "failure",
                "account": account_name,
                "requested_model": requested_model,
                "model": model_name,
                "elapsed_sec": round(time.time() - request_started, 3),
                "error_type": type(exc).__name__,
                "error": str(exc)[:2000],
            })
            raise

    text = output.text or ""
    write_account_attempt_log({
        "request_id": request_id,
        "endpoint": "/v1/chat/completions",
        "stream": False,
        "event": "success",
        "account": account_name,
        "requested_model": requested_model,
        "model": model_name,
        "elapsed_sec": round(time.time() - request_started, 3),
        "response_chars": len(text),
    })
    completion_id = f"chatcmpl-gemini-web-{now_ts()}"

    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": now_ts(),
        "model": model_name,
        "account": account_name,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": text,
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "reported": False,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }


@request_deadline
async def create_chat_completion_chunks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    state = require_state()
    requested_model, model_name = resolve_requested_model(payload, state)
    messages = payload.get("messages") or []
    if not isinstance(messages, list) or not messages:
        raise ValueError("messages must be a non-empty array")

    model: Any = model_name
    account_name, client = state.next_client(model_name)
    request_started = time.time()
    request_id = f"stream-{uuid.uuid4().hex}"
    completion_id = f"chatcmpl-gemini-web-{now_ts()}"
    chunks: list[dict[str, Any]] = [
        {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": now_ts(),
            "model": model_name,
            "account": account_name,
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant"},
                    "finish_reason": None,
                }
            ],
        }
    ]

    with tempfile.TemporaryDirectory(prefix="gemini_openai_images_") as tmp:
        prompt, files = await prepare_messages(messages, Path(tmp))
        if not prompt:
            raise ValueError("messages did not contain any text content")

        previous_text = ""
        write_account_attempt_log({
            "request_id": request_id,
            "endpoint": "/v1/chat/completions",
            "stream": True,
            "event": "begin",
            "account": account_name,
            "requested_model": requested_model,
            "model": model_name,
            "prompt_chars": len(prompt),
            "file_count": len(files or []),
        })
        try:
            async with state.account_request(account_name, request_id):
                model = await state.prepare_request_model(account_name, client, model_name)
                async for output in cookie_generate_stream(
                    client, prompt,
                    files=files or None,
                    model=model,
                    timeout=REQUEST_BUDGET.get().remaining(),
                ):
                    delta = output.text_delta or ""
                    if not delta:
                        text = output.text or ""
                        if text.startswith(previous_text):
                            delta = text[len(previous_text) :]
                        previous_text = text or previous_text

                    if not delta:
                        continue

                    chunks.append(
                        {
                            "id": completion_id,
                            "object": "chat.completion.chunk",
                            "created": now_ts(),
                            "model": model_name,
                            "account": account_name,
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {"content": delta},
                                    "finish_reason": None,
                                }
                            ],
                        }
                    )
                validate_generated_text("".join(
                    chunk["choices"][0]["delta"].get("content", "")
                    for chunk in chunks
                ))
        except Exception as exc:
            write_account_attempt_log({
                "request_id": request_id,
                "endpoint": "/v1/chat/completions",
                "stream": True,
                "event": "failure",
                "account": account_name,
                "requested_model": requested_model,
                "model": model_name,
                "elapsed_sec": round(time.time() - request_started, 3),
                "error_type": type(exc).__name__,
                "error": str(exc)[:2000],
            })
            raise

    chunks.append(
        {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": now_ts(),
            "model": model_name,
            "account": account_name,
            "choices": [
                {
                    "index": 0,
                    "delta": {},
                    "finish_reason": "stop",
                }
            ],
        }
    )
    write_account_attempt_log({
        "request_id": request_id,
        "endpoint": "/v1/chat/completions",
        "stream": True,
        "event": "success",
        "account": account_name,
        "requested_model": requested_model,
        "model": model_name,
        "elapsed_sec": round(time.time() - request_started, 3),
        "response_chars": len(previous_text),
        "chunk_count": len(chunks),
    })
    return chunks


async def create_image_generation(payload: dict[str, Any]) -> dict[str, Any]:
    raise EndpointDisabled('Image generation is temporarily disabled; not submitted')


class OpenAICompatibleHandler(BaseHTTPRequestHandler):
    server_version = "GeminiOpenAICompat/0.1"

    def log_message(self, fmt: str, *args):
        if STATE and STATE.args.quiet:
            return
        super().log_message(fmt, *args)

    def _send_json(self, status: int, data: dict[str, Any]):
        if getattr(self, '_trace', None):
            error = data.get('error', {}) if isinstance(data,dict) else {}
            stage = 'completed' if status < 400 else ('not_submitted' if error.get('submitted') is False else
                     'uncertain' if error.get('type') == 'submission_uncertain' else 'failed')
            operations.event(self._trace, stage, model=data.get('model'), http_status=status)
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        if getattr(self, '_trace', None):self.send_header('X-Request-ID',operations.identifier(self._trace))
        self.end_headers()
        self.wfile.write(raw)

    def _send_sse(self, chunks: list[dict[str, Any]]):
        if getattr(self,'_trace',None):operations.event(self._trace,'completed',model=chunks[0].get('model'))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        for chunk in chunks:
            line = f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
            self.wfile.write(line.encode("utf-8"))
            self.wfile.flush()

        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        if length > 2_000_000:raise ValueError('Request too large')
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def _authorized(self) -> bool:
        state = require_state()
        api_key = state.args.api_key
        if not api_key:
            return True

        auth = self.headers.get("Authorization") or ""
        x_api_key = self.headers.get("x-api-key") or ""
        token = auth.removeprefix('Bearer ') if auth else x_api_key
        try:
            self._client_id=operations.authorize(token,api_key)
            self._client_token=token
            return True
        except operations.AccessError:
            return False

    def do_GET(self):
        try:
            if not self._authorized():
                self._send_json(*openai_error("Unauthorized", 401, "invalid_request_error"))
                return

            if self.path == "/health":
                if getattr(self,'_client_id','legacy') != 'legacy':
                    self._send_json(*openai_error('Master key required',403));return
                state = require_state()
                account_details = {}
                for name, client in state.clients:
                    eligible, reason, pro_model = state.strict_pro_eligibility(client)
                    status = getattr(client, "account_status", None)
                    account_details[name] = {
                        "status": getattr(status, "name", str(status or "UNKNOWN")),
                        "strict_pro_eligible": eligible,
                        "strict_pro_reason": reason,
                        "pro_model_id": getattr(pro_model, "model_id", None),
                        "pro_capacity": getattr(pro_model, "capacity", None),
                        "admission": state.admission_status(name, client),
                    }
                self._send_json(
                    200,
                    {
                        "status": "ok",
                        "transport": "cookie",
                        "account_control": True,
                        "operations_v1": True,
                        "admission_gate": GATE.state() | {'async_tasks':len(state._tasks),
                                                          'cleanup_pending':bool(state._cleanup_failed)},
                        "runtime_config": {'model':state.args.default_model,'strict_pro':bool(state.args.strict_pro),
                                           'port':state.args.port},
                        "startup_errors": state.startup_errors,
                        "strict_pro": bool(state.args.strict_pro),
                        "allow_free_pro": bool(state.args.allow_free_pro),
                        "accounts": [name for name, _ in state.clients],
                        "account_count": len(state.clients),
                        "account_details": account_details,
                    },
                )
                return

            if self.path == "/v1/models":
                self._send_json(
                    200,
                    {
                        "object": "list",
                        "data": [
                            {
                                "id": name,
                                "object": "model",
                                "created": 0,
                                "owned_by": "gemini-webapi",
                            }
                            for name in operations.allowed_models(self._client_token,require_state().args.api_key,model_names())
                        ],
                    },
                )
                return

            self._send_json(*openai_error("Not found", 404, "invalid_request_error"))
        except Exception as exc:
            self._send_json(*openai_error(str(exc)))

    def do_POST(self):
        global RESTART_REQUESTED
        if self.path == '/internal/operations':
            if self.client_address[0] not in ('127.0.0.1','::1') or not self._authorized() or self._client_id!='legacy':
                self._send_json(*openai_error('Master authorization required',403));return
            try:
                action=self._read_json().get('action')
                if action=='drain':result=GATE.drain()
                elif action=='resume':result=GATE.drain(False)
                elif action=='restart':
                    def restart():
                        global RESTART_REQUESTED
                        RESTART_REQUESTED=True
                        self.server.shutdown()
                    state=require_state()
                    result=GATE.restart(restart,ready=lambda:not state._tasks and not state._cleanup_failed)
                else:raise ValueError('Unknown operation')
                self._send_json(200,result)
            except ValueError as exc:self._send_json(*openai_error(str(exc),400))
            return
        self._trace=uuid.uuid4().hex
        token=TRACE.set(self._trace)
        try:
            with GATE.enter():self._post_completion()
        except operations.AccessError as exc:
            self._send_json(exc.status,{'error':{'message':str(exc),'type':'not_submitted','submitted':False,'retry_after':30}})
        finally:
            TRACE.reset(token)

    def _post_completion(self):
        from request_ledger import execute, LedgerConflict
        try:
            if not self._authorized():
                self._send_json(*openai_error("Unauthorized", 401, "invalid_request_error"))
                return

            if self.path not in ("/v1/chat/completions", "/v1/images/generations"):
                self._send_json(*openai_error("Not found", 404, "invalid_request_error"))
                return

            payload = self._read_json()
            if self.path == "/v1/images/generations":
                raise EndpointDisabled('Image generation is temporarily disabled; not submitted')

            state = require_state()
            requested, resolved=resolve_requested_model(payload,state)
            operations.authorize(self._client_token,state.args.api_key,requested,consume=True)
            operations.event(self._trace,'received',model=requested,client_id=self._client_id)
            operations.event(self._trace,'model_resolved',model=resolved)
            ledger_key=operations.idempotency_key(self._client_id,self.headers.get('Idempotency-Key'))
            timeout = http_generation_timeout(self.headers, state.args.request_timeout)
            def run(factory):
                budget = RequestBudget(timeout)
                async def invoke():
                    return await within_deadline(factory(), budget)
                return state.run_async(invoke())
            if payload.get("stream"):
                chunks = execute(ledger_key, payload,
                                 lambda: run(lambda: create_chat_completion_chunks(payload)),
                                 not_submitted_errors=(PoolUnavailable,))
                self._send_sse(chunks)
                return

            response = execute(ledger_key, payload,
                               lambda: run(lambda: create_chat_completion(payload)),
                               not_submitted_errors=(PoolUnavailable,))
            self._send_json(200, response)
        except operations.AccessError as exc:
            self._send_json(exc.status,{'error':{'message':str(exc),'type':'not_submitted','submitted':False}})
        except LedgerConflict as exc:
            self._send_json(*openai_error(str(exc), 409, 'idempotency_conflict'))
        except EndpointDisabled as exc:
            self._send_json(*openai_error(str(exc), 501, 'not_implemented'))
        except ValueError as exc:
            self._send_json(*openai_error(str(exc), 400, "invalid_request_error"))
        except PoolUnavailable as exc:
            status, body = openai_error(str(exc), 503, "not_submitted")
            body['error'].update(submitted=False, retryable=True,
                                 retry_after=exc.retry_after, accounts=exc.accounts)
            self._send_json(status, body)
        except (SubmissionUncertain, FutureTimeoutError) as exc:
            status, body = openai_error('submission outcome unknown; reconcile before retrying', 409, 'submission_uncertain')
            body['error'].update(submitted=True, retryable=False)
            self._send_json(status, body)
        except Exception as exc:
            self._send_json(*openai_error(str(exc)))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="OpenAI-compatible local HTTP wrapper for gemini-webapi."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument(
        "--accounts",
        default="all",
        help="Comma-separated account names under ../account, or 'all'",
    )
    parser.add_argument(
        "--account",
        default=None,
        help="Single account name under ../account. Overrides --accounts.",
    )
    parser.add_argument(
        "--account-dir",
        default=str(ACCOUNT_DIR),
        help="Directory containing account JSON files",
    )
    parser.add_argument("--api-key", default=os.environ.get("GEMINI_CONSOLE_API_KEY", ""), help="Local bearer key (or GEMINI_CONSOLE_API_KEY)")
    parser.add_argument("--default-model", default=STRICT_PRO_MODEL)
    strict_group = parser.add_mutually_exclusive_group()
    strict_group.add_argument(
        "--strict-pro",
        dest="strict_pro",
        action="store_true",
        default=True,
        help="Fail closed unless an authenticated, dynamically advertised Pro model is available (default)",
    )
    strict_group.add_argument(
        "--allow-non-pro",
        dest="strict_pro",
        action="store_false",
        help="Disable strict Pro enforcement and restore the original model behavior",
    )
    parser.add_argument(
        "--allow-free-pro",
        action="store_true",
        help="Allow Pro shown on a free-tier account; paid entitlement is required by default",
    )
    parser.add_argument("--ignore-proxy", action="store_true")
    parser.add_argument(
        "--proxy",
        default=None,
        help="Optional proxy override; does not rewrite shared account JSON files",
    )
    parser.add_argument("--skip-verify", action="store_true")
    parser.add_argument("--auto-refresh", action="store_true")
    parser.add_argument("--init-timeout", type=float, default=60)
    parser.add_argument("--request-timeout", type=float, default=300)
    parser.add_argument("--queue-timeout", type=float, default=2)
    parser.add_argument(
        "--account-failure-threshold",
        type=int,
        default=1,
        help="Consecutive request failures before temporarily cooling down an account",
    )
    parser.add_argument(
        "--account-cooldown",
        type=float,
        default=900,
        help="Seconds to skip an unhealthy account after it reaches the failure threshold",
    )
    parser.add_argument("--quiet", action="store_true")
    return parser


def main(argv=None) -> int:
    global STATE

    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.api_key:
        parser.error("Set GEMINI_CONSOLE_API_KEY or --api-key; empty authentication is not allowed")
    if args.strict_pro and normalize_model_name(args.default_model, STRICT_PRO_MODEL) != STRICT_PRO_MODEL:
        parser.error(
            f"--default-model must resolve to '{STRICT_PRO_MODEL}' while strict Pro mode is enabled"
        )
    httpd = ThreadingHTTPServer((args.host, args.port), OpenAICompatibleHandler)
    STATE = None
    try:
        STATE = ServerState(args)
        STATE.start()
    except BaseException as exc:
        httpd.server_close()
        cleaned = STATE.stop() if STATE is not None else True
        if isinstance(exc, StartupError):
            exc.cleanup_complete = bool(cleaned)
            exc.retryable = bool(exc.retryable and cleaned)
        raise

    def stop_server(*_):
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    print(f"OpenAI-compatible Gemini wrapper listening on http://{args.host}:{args.port}/v1")
    print(f"Accounts: {', '.join(name for name, _ in STATE.clients)}")
    print(f"Default model: {args.default_model}")
    print(
        "Strict Pro mode: enabled "
        f"(paid entitlement required: {'no' if args.allow_free_pro else 'yes'})"
        if args.strict_pro
        else "Strict Pro mode: disabled"
    )
    print("Use Authorization: Bearer <api-key>")

    try:
        signal.signal(signal.SIGINT, stop_server)
        signal.signal(signal.SIGTERM, stop_server)
        httpd.serve_forever()
    finally:
        httpd.server_close()
        STATE.stop()

    if RESTART_REQUESTED:
        os.execv(sys.executable,[sys.executable,*sys.argv])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
