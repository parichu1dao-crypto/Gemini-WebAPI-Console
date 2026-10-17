"""Portable product entry point. Never imports credentials from another checkout."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
VERSION = '0.2.0-preview.1'


def initialize(root=ROOT):
    data = Path(root) / 'data'
    data.mkdir(mode=0o700, parents=True, exist_ok=True)
    (data / 'accounts').mkdir(mode=0o700, exist_ok=True)
    target = data / 'settings.json'
    if target.exists():
        return False
    config = json.loads((Path(root) / 'config.example.json').read_text(encoding='utf-8'))
    config['api_key'] = secrets.token_urlsafe(32)
    config['admin_key'] = secrets.token_urlsafe(32)
    with target.open('x', encoding='utf-8') as stream:
        json.dump(config, stream, ensure_ascii=False, indent=2)
    if os.name != 'nt':
        target.chmod(0o600)
    return True


def doctor():
    checks = {'python_3_11_or_newer': sys.version_info >= (3, 11)}
    for module in ('curl_cffi', 'loguru', 'orjson', 'pydantic', 'playwright'):
        checks[module] = importlib.util.find_spec(module) is not None
    checks['settings_present'] = (ROOT / 'data/settings.json').is_file()
    checks['account_directory'] = (ROOT / 'data/accounts').is_dir()
    print(json.dumps({'version': VERSION, 'checks': checks,
        'note': 'Offline installation checks only; no Google requests or credentials printed.'}, indent=2))
    return 0 if all(checks.values()) else 1


def main():
    parser = argparse.ArgumentParser(description='Gemini WebAPI Console — local session management')
    parser.add_argument('command', choices=('init', 'doctor', 'console', 'api', 'desktop', 'version'))
    args, extra = parser.parse_known_args()
    if args.command == 'version':
        print(VERSION)
        return 0
    if args.command == 'init':
        print('Created private data/settings.json. Protect this directory.' if initialize()
              else 'Existing settings preserved.')
        return 0
    if args.command == 'doctor':
        return doctor()
    initialize()
    script = {'console':'console.py', 'api':'service.py', 'desktop':'launcher.py'}[args.command]
    return subprocess.call([sys.executable, str(ROOT / script), *extra], cwd=ROOT)


if __name__ == '__main__':
    raise SystemExit(main())
