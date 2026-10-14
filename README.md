# Gemini WebAPI Console

![Gemini WebAPI Console — local control and optional Pro checks](web/banner.svg)

### Your browser session. A local API. A clearer view of every request.

[简体中文](README_CN.md) · [Security](SECURITY.md) · [License](LICENSE)

A self-hosted console for the Gemini web client, with an OpenAI-compatible endpoint, an account pool, and **optional strict Pro checks**. Built for people who need to understand failures—not just retry them.

> Independent community project based on [HanaokaYuzu/Gemini-API](https://github.com/HanaokaYuzu/Gemini-API). Not an official Google API or a Google product. Web availability, account eligibility and limits still apply.

## What changed

| Capability | Why it matters |
| --- | --- |
| Web console + Windows launcher | Start and stop the local API, with a shared configuration |
| Strict Pro switch in both interfaces | Enabled by default; choose whether to enforce the backend's Pro eligibility/model checks |
| Per-account request serialization | Requests to one account queue rather than overlap; different accounts can work in parallel |
| Request lifecycle diagnostics | Trace queuing, cancellation, stream completion and recovery using request identifiers |
| Timeout cancellation and guarded recovery | Cancel timed-out work and constrain conversation recovery to eligible response states |
| Isolated runtime configuration | Generated local keys; no bundled account cookies, personal profiles or production settings |

**Strict is a policy, not a model attestation.** It uses the upstream metadata available to this client. It cannot independently prove which weights Google ran. Disabling it relaxes checks; it does not unlock Pro, increase quota or guarantee a particular model.

## Start on Windows

Requires Python 3.10+ with Tkinter. In this repository folder:

```powershell
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe launcher.py
```

After installation, double-click **Start-Windows.bat**. The launcher starts the local manager; closing the launcher leaves that manager running. Use **Stop** to stop its API worker. Stopping interrupts active requests.

For the web-only manager:

```powershell
.venv\Scripts\python.exe console.py
```

Open **http://127.0.0.1:8790/**. The first run creates `data/settings.json`: use `admin_key` to connect the console and `api_key` for API requests. These are different secrets. Keep the file private.

### Add your own account

Put one cookie export per account in `data/accounts/<name>.json`. Use only accounts you own or are authorized to operate. The accepted structure is an object containing a `cookies` array:

```json
{"cookies": [{"domain": ".google.com", "name": "__Secure-1PSID", "value": "YOUR_PRIVATE_COOKIE"}]}
```

Include the current `__Secure-1PSIDTS` cookie when available. Never send cookie exports to the maintainer or commit them. Browser profile folders are not cookie JSON exports. Login/session validity is checked by the upstream service; merely having a file does not prove an account is usable.

### Choose the policy, then start

1. Stop the API before changing settings.
2. Toggle **Strict Pro** in the web console or Windows launcher and save.
3. Start the API. Inspect `data/server.log` for account initialization results.

The API defaults to `http://127.0.0.1:8787/v1`. A running process is **not** proof that generation works. Supported model aliases in the console reflect this client's mappings, not a live official Google model catalog.

If a proxy is needed, set its URL in the web console, for example `http://127.0.0.1:7890`. A blank value requests ignore-proxy mode from the client; it is not a guarantee that every dependency or operating-system network setting is bypassed. Start your VPN/proxy first. This console does not install an OS startup task or change other applications' proxy settings.

### API example

```python
import json
import urllib.request
from pathlib import Path

settings = json.loads(Path("data/settings.json").read_text())
request = urllib.request.Request(
    f"http://127.0.0.1:{settings['port']}/v1/chat/completions",
    data=json.dumps({"model": settings["model"], "messages": [
        {"role": "user", "content": "Say hello in one sentence."}
    ]}).encode(),
    headers={"Authorization": f"Bearer {settings['api_key']}",
             "Content-Type": "application/json"},
)
with urllib.request.urlopen(request, timeout=180) as response:
    print(response.read().decode())
```

## Diagnose before retrying

- **401 / expired session:** refresh your own login and cookie export.
- **Connection refused:** check the local worker and configured proxy port.
- **Quota / eligibility failures:** check the account in the official web interface. A paid subscription alone is not proof of available capacity.
- **Interrupted response:** inspect `openai_request_diagnostics.jsonl` and `openai_account_attempts.jsonl` in the repository root, plus `data/server.log`. Redact identifiers, content and credentials before sharing.
- **Settings refused while running:** stop, save and restart. Policy changes must not silently alter in-flight work.

## Gemini 4: our commitment

**We will prioritize a Gemini 4-compatible release as soon as the upstream web service makes it available and we can validate compatibility.** This is a maintenance commitment—not a claim of current Gemini 4 support or a promised release date. Availability, model identifiers and verification mechanisms depend on upstream behavior.

## Feedback / contribute

Have a reproducible issue or an improvement idea? Open an issue or email **[parichu1dao@gmail.com](mailto:parichu1dao@gmail.com)**. Include the version, OS, sanitized error and reproduction steps—not credentials or private conversations. Stars and clear bug reports help this project improve.

Run offline regression tests with `python -m unittest discover -v`. These tests do not prove live account availability.

## License and attribution

Distributed under **GNU AGPL v3**, retaining the included upstream license. See [ATTRIBUTION.md](ATTRIBUTION.md). Hosting a modified version for network users carries source-availability obligations under that license. Respect the upstream service's terms and account restrictions.
