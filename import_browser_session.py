"""Explicit administrator-only, read-only browser session enrollment.

Only this offline enrollment step needs Playwright. The resulting JSON contains
email and complete scoped cookie records for the existing cookie runtime.
Never launch, navigate, focus, click, generate, or close a browser here.
Treat data/ (including backups) as credentials: on Windows its ACL must already
be private to the administrator. POSIX directories/files use 0700/0600.
"""
import argparse
from datetime import datetime, timezone
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import time
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
EMAIL = re.compile(r"[^\s@()<>]+@[^\s@()<>]+\.[^\s@()<>]+")
COOKIE_DOMAINS = {"google.com", ".google.com", "gemini.google.com", ".gemini.google.com"}


class ImportFailure(ValueError):
    """Messages are intentionally independent of credentials and input values."""


def validate_name(name):
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,59}", name):
        raise ImportFailure("Invalid account name.")
    if name.upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(10)],
                        *[f"LPT{i}" for i in range(10)]}:
        raise ImportFailure("Invalid account name.")
    return name


def validate_cdp_url(value):
    try:
        if not isinstance(value, str) or any(c.isspace() or ord(c) < 32 for c in value) or "\\" in value:
            raise ValueError
        url = urlsplit(value)
        if url.scheme not in {"http", "https", "ws", "wss"} or url.username is not None or url.password is not None:
            raise ValueError
        if url.query or url.fragment or not url.port or not url.hostname or "%" in url.hostname:
            raise ValueError
        # Canonicalize localhost instead of trusting DNS resolution.
        host = "127.0.0.1" if url.hostname == "localhost" else url.hostname
        if not ipaddress.ip_address(host).is_loopback:
            raise ValueError
        if url.scheme in {"http", "https"} and url.path not in {"", "/"}:
            raise ValueError
        authority = f"[{host}]:{url.port}" if ":" in host else f"{host}:{url.port}"
        return urlunsplit((url.scheme, authority, url.path, "", ""))
    except (ValueError, TypeError):
        raise ImportFailure("CDP must use a loopback URL with an explicit port.") from None


def normalize_email(value):
    if not isinstance(value, str) or not EMAIL.fullmatch(value.strip()):
        raise ImportFailure("Invalid expected identity.")
    return value.strip().casefold()


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ImportFailure("CDP discovery redirects are not allowed.")


def resolve_cdp_endpoint(value):
    """Prevent HTTP discovery from redirecting/advertising a remote socket.

    This queries only the local debug server, never a Gemini endpoint. Ignore
    environment proxy settings so loopback discovery stays on this machine.
    """
    endpoint = validate_cdp_url(value)
    if urlsplit(endpoint).scheme in {"ws", "wss"}:
        return endpoint
    opener = build_opener(ProxyHandler({}), _NoRedirect())
    request = Request(endpoint.rstrip("/") + "/json/version", headers={"Accept": "application/json"})
    with opener.open(request, timeout=10) as response:
        raw = response.read(65537)
    if len(raw) > 65536:
        raise ImportFailure("Invalid CDP discovery response.")
    try:
        discovered = validate_cdp_url(json.loads(raw)["webSocketDebuggerUrl"])
    except (ValueError, KeyError, TypeError):
        raise ImportFailure("Invalid CDP discovery response.") from None
    if urlsplit(discovered).scheme not in {"ws", "wss"}:
        raise ImportFailure("Invalid CDP discovery response.")
    return discovered


def _is_link(path):
    return path.is_symlink() or getattr(path, "is_junction", lambda: False)()


def account_directory(value=None):
    data = DATA_DIR.absolute()
    path = Path(value) if value is not None else data / "accounts"
    if not path.is_absolute():
        path = ROOT / path
    path = path.absolute()
    # Reject traversal even if it would normalize back inside data/.
    if ".." in path.parts or not path.is_relative_to(data):
        raise ImportFailure("Account directory must be inside private data/.")
    # No symlink/junction redirects, including an existing data/ link.
    for parent in (path, *path.parents):
        if _is_link(parent):
            raise ImportFailure("Linked account directories are not allowed.")
        if parent.exists() and not parent.is_dir():
            raise ImportFailure("Invalid account directory.")
    if not path.resolve().is_relative_to(data.resolve()):
        raise ImportFailure("Account directory must be inside private data/.")
    return path


def _gemini_page(page):
    url = urlsplit(page.url)
    return (url.scheme == "https" and url.hostname == "gemini.google.com"
            and url.port in (None, 443) and url.username is None and url.password is None)


def _identity(context, expected):
    identities = set()
    found_page = False
    for page in context.pages:
        if not _gemini_page(page):
            continue
        found_page = True
        # Same parenthesized identity evidence as Accounts.verify, with exact
        # parsing instead of substring matching. Do not use page text/storage.
        labels = page.locator("[aria-label]").evaluate_all(
            "els => els.map(e => e.getAttribute('aria-label'))"
        )
        for label in labels:
            if not isinstance(label, str):
                continue
            candidates = re.findall(r"\(([^()]*)\)", label)
            if EMAIL.fullmatch(label.strip()):
                candidates.append(label.strip())
            for candidate in candidates:
                if EMAIL.fullmatch(candidate.strip()):
                    identities.add(candidate.strip().casefold())
    if not found_page or identities != {expected}:
        raise ImportFailure("Missing, mismatched, or ambiguous browser identity.")


def collect_session(browser, expected_email):
    expected = normalize_email(expected_email)
    contexts = list(browser.contexts)
    if len(contexts) != 1:
        raise ImportFailure("Exactly one existing browser context is required.")
    context = contexts[0]
    _identity(context, expected)
    # Read the complete cookie jar to preserve all paths/metadata/duplicates,
    # then retain ONLY these exact domains (not arbitrary Google subdomains).
    cookies = [dict(c) for c in context.cookies()
               if isinstance(c, dict) and isinstance(c.get("domain"), str)
               and c["domain"].lower() in COOKIE_DOMAINS]
    for cookie in cookies:
        if (not isinstance(cookie.get("name"), str) or not cookie["name"]
                or not isinstance(cookie.get("value"), str)
                or not isinstance(cookie.get("path"), str) or not cookie["path"].startswith("/")):
            raise ImportFailure("Invalid scoped cookie record.")
    now = time.time()
    def authenticated(c):
        expiry = c.get("expires", -1)
        return (c["name"] == "__Secure-1PSID" and bool(c["value"].strip()) and c["path"] == "/"
                and isinstance(expiry, (int, float)) and (expiry == -1 or expiry > now))
    if not any(authenticated(c) for c in cookies):
        raise ImportFailure("Missing usable authenticated session cookie.")
    # Detect account switching during collection; never export on stale proof.
    if list(browser.contexts) != contexts:
        raise ImportFailure("Browser context changed during collection.")
    _identity(context, expected)
    return {"email": expected, "cookies": cookies}


def _regular_target(path):
    if _is_link(path):
        raise ImportFailure("Linked account files are not allowed.")
    if path.exists():
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ImportFailure("Invalid account file.")


def _private_write(path, payload):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def write_session(name, session, account_dir=None):
    name = validate_name(name)
    directory = account_directory(account_dir)
    # Create each level privately, not mkdir(parents=True)'s default modes.
    missing = []
    parent = directory
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    for parent in reversed(missing):
        parent.mkdir(mode=0o700, exist_ok=True)
    directory = account_directory(directory)
    if os.name != "nt":
        os.chmod(directory, 0o700)
    target = directory / f"{name}.json"
    lock = directory / f".{name}.import.lock"
    try:
        lock_fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise ImportFailure("An import is already in progress for this account.") from None
    temporary = None
    try:
        os.close(lock_fd)
        _regular_target(target)
        payload = (json.dumps(session, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
        fd, tmp = tempfile.mkstemp(prefix=f".{name}.", suffix=".tmp", dir=directory)
        temporary = Path(tmp)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if target.exists():
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
            backup = directory / f"{name}.{stamp}.bak"
            _private_write(backup, target.read_bytes())
        account_directory(directory)
        _regular_target(target)
        os.replace(temporary, target)
        temporary = None
        return target
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        lock.unlink(missing_ok=True)


def import_session(name, cdp_url, expected_email, account_dir=None):
    validate_name(name)
    endpoint = validate_cdp_url(cdp_url)
    expected = normalize_email(expected_email)
    directory = account_directory(account_dir)
    _regular_target(directory / f"{name}.json")
    endpoint = resolve_cdp_endpoint(endpoint)
    from playwright.sync_api import sync_playwright
    # Stopping our Playwright transport disconnects; browser.close/context.close
    # must never be called on the administrator's existing browser.
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(endpoint, timeout=10000)
        session = collect_session(browser, expected)
    return write_session(name, session, directory)


class _PrivateParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's default error can echo unknown arguments (including secrets).
        self.exit(2, "Invalid arguments; use --help for usage.\n")


def main(argv=None):
    parser = _PrivateParser(description="Explicit administrator import from an existing local browser; no generation.")
    parser.add_argument("--name", required=True, help="Safe account filename stem (letters, digits, underscore, hyphen).")
    parser.add_argument("--cdp-url", required=True, help="Loopback CDP URL, including port.")
    parser.add_argument("--expected-email", required=True, help="Exact signed-in identity to verify; never logged.")
    parser.add_argument("--account-dir", default=None, help="Directory within private data/ (default: data/accounts).")
    args = parser.parse_args(argv)
    try:
        import_session(args.name, args.cdp_url, args.expected_email, args.account_dir)
    except Exception:
        print("Import failed; no session accepted. Check local identity, endpoint, and private storage.", file=sys.stderr)
        return 1
    print("Session imported into private account storage.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
