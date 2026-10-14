import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ACCOUNT_DIR = ROOT / "data" / "accounts"
sys.path.insert(0, str(ROOT / "src"))

from gemini_webapi import GeminiClient  # noqa: E402


def load_account_credentials(
    account_name: str,
    ignore_proxy: bool = False,
    account_dir: str | Path | None = None,
):
    resolved_account_dir = Path(account_dir).resolve() if account_dir else ACCOUNT_DIR
    account_path = resolved_account_dir / f"{account_name}.json"
    if not account_path.is_file():
        raise FileNotFoundError(f"Account file not found: {account_path}")

    data = json.loads(account_path.read_text(encoding="utf-8"))
    psid = ""
    psidts = ""
    for cookie in data.get("cookies") or []:
        if not isinstance(cookie, dict) or cookie.get("domain") != ".google.com":
            continue
        if cookie.get("name") == "__Secure-1PSID":
            psid = cookie.get("value") or psid
        elif cookie.get("name") == "__Secure-1PSIDTS":
            psidts = cookie.get("value") or psidts

    if not psid:
        raise ValueError(f"{account_name}: missing .google.com __Secure-1PSID")

    proxy = None if ignore_proxy else data.get("proxy")
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
    psid, psidts, proxy = load_account_credentials(
        account_name,
        ignore_proxy=ignore_proxy,
        account_dir=account_dir,
    )
    if not ignore_proxy and proxy_override:
        proxy = proxy_override
    client = GeminiClient(psid, psidts or "", proxy=proxy, verify=verify)
    try:
        await client.init(timeout=timeout, auto_refresh=auto_refresh)
        return client
    except Exception:
        await client.close()
        raise
