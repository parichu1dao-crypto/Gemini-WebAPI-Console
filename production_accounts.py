"""Explicit bindings for existing production accounts; no default-profile fallback."""
import json
import os
import subprocess
from pathlib import Path
from urllib.parse import urlsplit


def binding(data, name):
    data=Path(data)
    integration=json.loads((data/'integration.json').read_text(encoding='utf-8'))
    mapping=json.loads(Path(integration['browser_mapping']).read_text(encoding='utf-8-sig'))
    if name not in mapping:
        raise ValueError('Historical account has no active browser binding')
    row=dict(mapping[name])
    profiles=json.loads((data/'browser_profiles.json').read_text(encoding='utf-8'))
    row['profile']=profiles.get(name)
    parsed=urlsplit(row['cdp_url'])
    if parsed.hostname not in ('127.0.0.1','localhost') or parsed.scheme!='http' or not parsed.port:
        raise ValueError('Only local dedicated browsers are supported')
    return row


def open_browser(data, name):
    row=binding(data,name)
    from playwright.sync_api import sync_playwright
    import socket
    address=urlsplit(row['cdp_url'])
    try:
        with socket.create_connection((address.hostname,address.port),timeout=1):pass
        running=True
    except OSError:
        running=False
    if running:
        with sync_playwright() as pw:
            browser=pw.chromium.connect_over_cdp(row['cdp_url'],timeout=10000)
            if len(browser.contexts)!=1:
                raise ValueError('Ambiguous browser profile')
            for page in browser.contexts[0].pages:
                if urlsplit(page.url).hostname!='gemini.google.com':continue
                labels=page.locator('a[aria-label]').evaluate_all("els=>els.map(e=>e.getAttribute('aria-label')||'')")
                if any('('+row['email'].casefold()+')' in x.casefold() for x in labels):
                    page.bring_to_front()
                    return {'opened':True,'identity_verified':True}
            raise ValueError('Browser identity does not match; sign in to the bound account first')
    profile=Path(row['profile'] or '')
    if not row['profile'] or not profile.is_absolute() or not profile.is_dir():
        raise ValueError('Dedicated browser profile not configured or missing')
    chrome=Path(os.environ.get('PROGRAMFILES',r'C:\Program Files'))/'Google/Chrome/Application/chrome.exe'
    if not chrome.is_file():raise ValueError('Chrome not installed')
    subprocess.Popen([str(chrome),'--user-data-dir='+str(profile),'--remote-debugging-address=127.0.0.1',
        '--remote-debugging-port='+str(address.port),'--no-first-run','https://gemini.google.com/app'],
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    return {'opened':True,'identity_verified':False,'warning':'Dedicated profile opened; check the signed-in account. Login may have expired.'}
