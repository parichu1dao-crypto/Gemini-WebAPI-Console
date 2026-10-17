# Quick start / 快速开始

Python 3.11+ is required. Windows desktop support needs Tkinter and Chrome.
Linux is supported for the HTTP console and offline tests; desktop enrollment
on non-Windows platforms is not a verified deployment path.

## Windows

```powershell
powershell -ExecutionPolicy Bypass -File .\Install-Windows.ps1
.\Start-Windows.bat
```

The installer creates a project-local virtual environment. It does not install
scheduled tasks or modify other applications' proxy settings.

## Terminal

```bash
python -m venv .venv
# Activate .venv with the command appropriate for your shell.
python -m pip install -r requirements.txt
python app.py init
python app.py doctor
python app.py console
```

Open http://127.0.0.1:8791 . The management password is `admin_key` in private
`data/settings.json`; the API bearer key is a different value, `api_key`.
Never paste either into a GitHub issue. Chrome is used for managed enrollment;
the service does not reuse your default browser profile.

打开账号管理，录入账号，使用专用浏览器登录，验证邮箱身份，再启用账号。
新账号默认按时间命名，可重命名。不要向任何人提供 Cookie 或密码。

## Choose a transport deliberately / 明确选择通道

| Mode | Entry point | Requirement |
|---|---|---|
| Cookie | `python app.py api`, localhost:8787/v1 | Explicitly enrolled account JSON in data/accounts; valid session |
| Managed browser | Console Settings → managed, then Start API; localhost:8791/v1 | Enroll and verify here; keep dedicated Chrome tabs open |
| External browser | Advanced compatibility option in Settings | A separate browser service that reports browser transport; not installed automatically |

Cookie and managed accounts are separate stores. Adding a managed browser does
not silently export its cookies or join it to another pool. To enroll Cookie
credentials explicitly, use `python import_browser_session.py --help` and supply
the correct account name, loopback CDP endpoint, and expected email. Never guess
the browser identity. The console's managed verification discovers models from
the page; unavailable models must not be replaced by Flash.

严格 Pro 默认开启；Cookie 校验与网页选中模型证据不同。托管网页目前不能证明付费
Pro 权限，管理台会拒绝启用其严格模式，必须由管理员明确理解后关闭该额外校验。
这不会允许请求模型被静默替换。网页模式需要浏览器持续运行，不能关闭专用标签页。

## API example

```python
import json, os, urllib.request
payload = {'model': 'gemini-3-pro', 'messages': [{'role':'user','content':'Hello'}]}
request = urllib.request.Request(os.environ['GEMINI_BASE_URL'].rstrip('/') + '/chat/completions',
    data=json.dumps(payload).encode(), headers={
        'Authorization': 'Bearer ' + os.environ['GEMINI_API_KEY'],
        'Content-Type':'application/json'})
with urllib.request.urlopen(request, timeout=330) as response:
    print(json.load(response)['choices'][0]['message']['content'])
```

Set `GEMINI_BASE_URL` to the chosen mode's `/v1` URL. Use the actual advertised
model from `/v1/models`. Never automatically retry an unknown submission.

## Upgrade / 升级

Stop admitting new requests and wait for in-flight work. Back up private `data/`
outside the checkout. Upgrade code and dependencies, then run `app.py doctor`.
Never replace existing settings with the example. Database migrations in this
preview are additive; restoring older code may still require your matching data
backup. Do not delete request ledgers to bypass pending/unknown submissions.

Feedback: parichu1dao@gmail.com. Include version, transport and redacted error.
