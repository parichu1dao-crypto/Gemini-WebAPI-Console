"""Explicit enrolled-profile session refresh; never opens or navigates a browser."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def recover_session(name, account_dir):
    from import_browser_session import import_session, normalize_email
    directory = Path(account_dir).resolve()
    target = (directory / f'{name}.json').resolve()
    if target.parent != directory:
        raise ValueError('Invalid account name')
    integration = ROOT / 'data' / 'integration.json'
    if not integration.is_file():
        return False
    config = json.loads(integration.read_text(encoding='utf-8-sig'))
    mapping_path = config.get('browser_mapping')
    if not mapping_path:
        return False
    mapping = json.loads(Path(mapping_path).read_text(encoding='utf-8-sig'))
    enrolled = mapping.get(name)
    if not enrolled:
        return False
    stored = json.loads(target.read_text(encoding='utf-8-sig'))
    expected = normalize_email(stored.get('email', ''))
    if not expected or expected != normalize_email(enrolled.get('email', '')):
        raise ValueError('Enrolled browser identity does not match account')
    import_session(name, enrolled['cdp_url'], expected, directory)
    return True
