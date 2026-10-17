"""Render the real dashboard with synthetic data; never connect to a service.

Run: python tools/capture_docs.py (installed Chrome required).
All requests are intercepted. Screenshots are documentation, not live usage proof.
"""
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs' / 'images'
NOW = 1792231200
ACCOUNTS = [dict(id=f'demo-{i}', name=name, email=f'demo{i}@example.test',
                 status='verified', enabled=True, verified=NOW, archived=False)
            for i, name in enumerate(['Studio A', 'Studio B', 'Studio C'], 1)]
REPORT = dict(summary=dict(requests=24, success=22, failed=2, running=0,
    reported_requests=0, estimated_input=96000, estimated_output=48000,
    unknown_usage=2, interrupted=0),
    note='Demo data / 演示数据 · Usage estimates are not billing measurements.',
    daily=[dict(day='2026-10-17', requests=24, success=22, failed=2,
                estimated_input=96000, estimated_output=48000)],
    accounts=[dict(account=f'demo-{i}', requests=8, success=n, failed=8-n)
              for i,n in [(1,8),(2,7),(3,7)]], records=[])

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='chrome', headless=True)
        context = browser.new_context(viewport={'width':1440,'height':1000}, device_scale_factor=1)
        def route(request):
            path = urlparse(request.request.url).path
            if path == '/api/accounts':
                return request.fulfill(json={'accounts':ACCOUNTS})
            if path == '/api/activity':
                return request.fulfill(json=REPORT)
            allowed = {'/':'index.html','/app.css':'app.css','/app.js':'app.js','/i18n.js':'i18n.js'}
            if path in allowed:
                mime='text/html' if path=='/' else 'text/css' if path.endswith('.css') else 'text/javascript'
                return request.fulfill(body=(ROOT/'web'/allowed[path]).read_bytes(), content_type=mime)
            request.abort()
        context.route('**/*', route)
        page = context.new_page()
        for language in ['zh','en']:
            page.goto('http://documentation.invalid/')
            page.evaluate('(lang)=>localStorage.setItem("gemini-console-language",lang)', language)
            page.reload()
            page.locator('#key').fill('synthetic-documentation-only')
            page.locator('#connect').click()
            page.locator('#auth').wait_for(state='hidden')
            page.locator('#metrics .metric').first.wait_for()
            page.screenshot(path=str(OUT/f'overview-{language}.png'))
            page.locator('[data-page="accounts"]').click()
            page.locator('#accountRows tbody tr').first.wait_for()
            page.locator('#message').evaluate('(el)=>{el.textContent="Demo accounts / 演示账号 · No real credentials"}')
            page.screenshot(path=str(OUT/f'accounts-{language}.png'))
        browser.close()

if __name__ == '__main__':
    main()
