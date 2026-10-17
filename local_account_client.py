import json
import sys
import time
import hashlib
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ACCOUNT_DIR = ROOT / "data" / "accounts"
sys.path.insert(0, str(ROOT / "src"))

from gemini_webapi import GeminiClient  # noqa: E402
from curl_cffi.requests import Cookies  # noqa: E402


def session_version(account_name, account_dir):
    path = (Path(account_dir).resolve() / f'{account_name}.json').resolve()
    if path.parent != Path(account_dir).resolve():
        raise ValueError('Invalid account file')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_account_session(account_name, account_dir, cookies, expected_version):
    """Persist only this account's refreshed jar; never overwrite a new import."""
    directory = Path(account_dir).resolve()
    path = (directory / f'{account_name}.json').resolve()
    if path.parent != directory:
        raise ValueError('Invalid account file')
    lock = directory / f'.{account_name}.import.lock'
    try:
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return expected_version
    temporary = None
    try:
        os.close(fd)
        if session_version(account_name, directory) != expected_version:
            return expected_version  # An administrator has imported a newer session.
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        data['cookies'] = [dict(name=c.name, value=c.value, domain=c.domain,
                                path=c.path, secure=c.secure, expires=c.expires)
                           for c in cookies.jar if c.domain.lstrip('.') in {'google.com','gemini.google.com'}
                           and not c.is_expired()]
        data['session_updated_at'] = time.time()
        payload = json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8')
        fd, temporary = tempfile.mkstemp(prefix=f'.{account_name}.', suffix='.tmp', dir=directory)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
        return hashlib.sha256(payload).hexdigest()
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)
        lock.unlink(missing_ok=True)


def load_account_cookie_session(
    account_name: str,
    ignore_proxy: bool = False,
    account_dir: str | Path | None = None,
):
    resolved_account_dir = Path(account_dir).resolve() if account_dir else ACCOUNT_DIR
    account_path = (resolved_account_dir / f"{account_name}.json").resolve()
    if account_path.parent != resolved_account_dir:
        raise ValueError("Account name must identify a single account file")
    if not account_path.is_file():
        raise FileNotFoundError(f"Account file not found: {account_path}")

    data = json.loads(account_path.read_text(encoding="utf-8-sig"))
    jar = Cookies()
    for cookie in data.get("cookies") or []:
        if not isinstance(cookie, dict):
            continue
        domain = cookie.get("domain")
        if not isinstance(domain, str):
            continue
        host = domain.lstrip(".").lower()
        if host != "google.com" and not host.endswith(".google.com"):
            continue
        name, value = cookie.get("name"), cookie.get("value")
        path = cookie.get("path", "/")
        if not isinstance(name, str) or not name or not isinstance(value, str):
            continue
        if not isinstance(path, str) or not path.startswith("/"):
            raise ValueError("Invalid Google cookie path")
        expires = cookie.get("expires", cookie.get("expirationDate"))
        if isinstance(expires, (int, float)) and expires > 0 and expires <= time.time():
            continue
        jar.set(name, value, domain=domain, path=path, secure=bool(cookie.get("secure", False)))
        # Cookies.set has no expires parameter. Preserve it on the underlying
        # Cookie rather than silently converting persistent cookies to sessions.
        stored = next(c for c in jar.jar if c.name == name and c.domain == domain and c.path == path)
        stored.expires = int(expires) if isinstance(expires, (int, float)) and expires > 0 else None
        stored.discard = stored.expires is None

    if not any(c.name == "__Secure-1PSID" and c.value
               and c.domain.lstrip(".") in {"google.com", "gemini.google.com"}
               and c.path == "/" for c in jar.jar):
        raise ValueError(f"{account_name}: missing Google __Secure-1PSID")

    proxy = None if ignore_proxy else data.get("proxy")
    return jar, proxy


def load_account_credentials(
    account_name: str,
    ignore_proxy: bool = False,
    account_dir: str | Path | None = None,
):
    """Compatibility API; session creation uses the complete scoped cookie jar."""
    jar, proxy = load_account_cookie_session(account_name, ignore_proxy, account_dir)
    def value(name):
        return next((c.value for c in jar.jar if c.name == name
                     and c.domain.lstrip(".") in {"google.com", "gemini.google.com"}
                     and c.path == "/"), "")
    psid, psidts = value("__Secure-1PSID"), value("__Secure-1PSIDTS")
    return psid, psidts, proxy


async def create_initialized_client(
    account_name: str,
    *,
    ignore_proxy: bool = False,
    account_dir: str | Path | None = None,
    proxy_override: str | None = None,
    timeout: float = 60,
    verify: bool = False,
    auto_refresh: bool = False,
):
    cookies, proxy = load_account_cookie_session(
        account_name,
        ignore_proxy=ignore_proxy,
        account_dir=account_dir,
    )
    if not ignore_proxy and proxy_override:
        proxy = proxy_override
    client = GeminiClient(proxy=proxy, verify=verify)
    client.cookies = cookies
    try:
        await client.init(timeout=timeout, auto_refresh=auto_refresh, supplied_cookies_only=True)
        return client
    except Exception:
        await client.close()
        raise
