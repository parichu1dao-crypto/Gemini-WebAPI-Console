# Gemini WebAPI Console

![Gemini WebAPI Console](web/banner.svg)

### Your sessions. Your controls. Every request accounted for.

[简体中文](README_CN.md) · [Quick start](docs/QUICKSTART.md) · [Known limitations](docs/RELEASE_SCOPE.md) · [Changelog](CHANGELOG.md)

**Upcoming experimental 0.2.1 — draft PR, not a stable launch.**
Manage dedicated browser sessions, expose text-generation APIs, inspect failures,
and understand usage without mixing your credentials with someone else's service.

> Independent community software, not a Google product or official API.
> Upstream availability, account eligibility and limits still apply. This preview
> is for public feedback, not an availability guarantee or a hosted multi-tenant service.

## What is Gemini WebAPI Console?

**Do NOT switch production based on this PR.** The adapter is implemented and
224 offline tests pass, including fixes for length-prefixed ACKs and empty routing
IDs. Earlier short lab requests passed on three accounts, but later integrated
requests timed out without a response body. A 31,502-character input was actually
submitted and timed out after 360 seconds without an answer. Long translation is
**not validated**; this is not a claim of translation usability. The newest model
has not been verified on the new protocol. A public draft PR is planned, not a
merge or release; the baseline `legacy` default is retained.
See [Cookie protocol and deployment](docs/COOKIE_PROTOCOL.md).

**Gemini WebAPI Console is an open-source, self-hosted application that connects
your own Gemini web sessions to a manageable text-generation API.** It brings
account login, model selection, request history, usage estimates and recovery
controls into one bilingual dashboard, with a Windows launcher for local use.

It is designed for developers connecting scripts or compatible chat clients,
creators running translation and writing workflows, and operators who need to
see which account handled a request and why it failed. Instead of maintaining
separate login scripts and guessing whether a timeout consumed quota, you get
account-level visibility and explicit recovery controls.

### How it works

Your client sends a request to the local API. The selected transport uses an
eligible account's Gemini session and returns the generated text. The dashboard
records the attempt, its timing, status and available usage information.

| Path | Role | What must stay available |
|---|---|---|
| Cookie API: `legacy` (default) | Existing Cookie HTTP generation | Valid cookies and network; no open webpage required per request, but upstream compatibility is not guaranteed |
| Cookie API: `process-session` (opt-in preview) | Independent Cookie HTTP sends the `processSession` body | Enrolled, dedicated, signed-in browser for fresh validation and request-bundle preparation only; all browser generation POSTs are blocked |
| Managed browser | An alternative when Cookie calls fail but the website still works | The account's dedicated, signed-in Chrome window and Gemini tab |
| Dashboard | Account management, model settings, request records and diagnostics | The local console service |

Switching paths is an administrator decision, not automatic replay of failed
requests. It does not grant additional model access or bypass quotas. Available
models depend on the account and transport. The new protocol accepts only its
observed Pro model ID; all other models fail closed as `notSubmitted`. A catalog entry alone is
not proof of protocol support. This is **not the Google AI Studio/Vertex AI API**, an
account marketplace or an unlimited-access service. Current APIs are text-only;
image generation is on the roadmap.

**First run:** install → start the console → enroll and verify an account →
choose a transport and model → test one short request → connect your workflow.
Follow the [step-by-step setup](docs/QUICKSTART.md) and
[browser fallback guide](docs/BROWSER_FALLBACK.md).

## What is inside?

### Explicit protocol selection, no automatic replay

`cookie_protocol` accepts `legacy` (default) or `process-session` (explicit opt-in).
The new path is Cookie HTTP, not managed-browser generation. It never automatically
falls back to legacy or browser generation. Never retry an uncertain submission.
The browser supplies fresh validation and a request bundle; the independent HTTP
client submits the body. All browser generation POSTs remain blocked in this path.

This experiment is **Cookie-only, with no fallback**. Managed-browser generation
is a separate baseline feature, not a recovery path for this PR. Historical browser recovery guides do not override
the new protocol's blocked browser generation POSTs or fail-closed model checks.
There is no guaranteed latest-model support or silent Flash substitution.

## A look inside

Real dashboard screenshots with synthetic demonstration data, not measured
production performance. No real accounts or credentials are shown.

![English usage dashboard](docs/images/overview-en.png)

<details>
<summary>Account management: enroll, verify, enable, rename and delete</summary>

![English account management](docs/images/accounts-en.png)

</details>

## What changed in 0.2?

The existing 0.2 baseline turned the earlier gateway into a standalone local application:
installation and startup, account lifecycle, browser fallback, per-request
observability, safer error handling and operations are now documented together.
See the [bilingual release tour](docs/RELEASE_0_2.md) for the complete change list
and the [Cookie → browser recovery guide](docs/BROWSER_FALLBACK.md) for exact steps.

| Capability | What you get |
|---|---|
| Accounts | Dedicated login windows, identity verification, rename, enable/disable and deletion |
| Two local transports | Cookie API and managed browser text generation; explicit selection, no hidden fallback |
| Models | Available-model discovery, requested-model checks and no silent Flash substitution |
| Observability | Per-account calls, day/week/month/custom ranges, reported versus estimated tokens |
| Safer recovery | Queue admission, timeout boundaries, uncertain-submission protection and Cookie idempotency keys |
| Operations | Client keys, model scopes, audit events, diagnostics and drain controls |
| Desktop + web | Windows launcher and bilingual dashboard, using private local configuration |

**Strict Pro checks default to on.** Cookie checks rely on upstream metadata, not
cryptographic model identity. Managed browser mode cannot yet verify paid Pro
entitlement and requires an explicit policy choice; it does not inherit a claim
of paid authorization merely because a menu shows Pro. See the [mode guide](docs/QUICKSTART.md).

## Start in minutes

For isolated experimental testing only, follow the [protocol deployment guide](docs/COOKIE_PROTOCOL.md#deployment).
Do not use this PR as a production upgrade recommendation.
Preserve existing keys and enroll browser bindings through the existing account
manager. Protocol selection is a configuration setting, not a new documented UI control.

Install Python **3.11+**. On Windows:

```powershell
powershell -ExecutionPolicy Bypass -File .\Install-Windows.ps1
.\Start-Windows.bat
```

Or use the terminal:

```bash
python -m pip install -r requirements.txt
python app.py init
python app.py doctor
python app.py console
```

Open **http://127.0.0.1:8791/**. The first run generates distinct management and API
keys in private `data/settings.json`. No default shared password, no bundled
accounts, and no changes to other applications' proxy settings.

- Cookie API: `python app.py api`, default **8787/v1**.
  `legacy` is the default; `process-session` additionally requires its enrolled browser.
- Managed browser API: enroll/verify accounts, select managed mode and Start API,
  then use **8791/v1**. Keep its dedicated browser tabs open.
- Existing external browser services are an advanced compatibility option, not
  a required second checkout or an automatically provisioned service.

Full installation, account enrollment, API examples and upgrades:
**[Quick start →](docs/QUICKSTART.md)**

## Understand the failure before retrying

Observed upstream behavior has changed: earlier `StreamGenerate` lab requests
failed even after startup authentication. This is not proof that Google removed
the old protocol globally. A separate account returned an error card; an
operator-imposed 12-hour cooldown is not proof of quota exhaustion.

The client-facing HTTP API remains `/v1/chat/completions` with the same compatible
request/response format. Initial streaming may buffer the answer until validated;
this is not a promise of live upstream token streaming.

Network interruptions and missing page controls are not proof of an invalid
account. Pre-submit failures use short backoff. Explicit account identity mismatch,
quota exhaustion and unknown submitted outcomes are separate conditions.
Never blindly resend an uncertain request or remove its protection flag.

Browser usage can be estimated, not exact upstream billing. A successful reply
does not establish translation quality. **Image generation is not supported by
this preview API.** Cookie calls can fail while the webpage still works; browser
automation can break when the upstream UI changes. See [release scope](docs/RELEASE_SCOPE.md).

## Help shape the next release

**Next: image generation.** Planned work includes image-capability discovery,
image request/result handling, downloads and clear error reporting. This is a
roadmap item, **not an available feature in 0.2**. It will ship only after
end-to-end verification; no release date is promised. We also plan broader
new-model regression coverage and clearer recovery diagnostics.

Found a new error? Open a [bug report](https://github.com/parichu1dao-crypto/Gemini-WebAPI-Console/issues/new/choose)
or email [parichu1dao@gmail.com](mailto:parichu1dao@gmail.com).
Include version, transport, OS, redacted error and whether submission occurred.
**Never attach cookies, passwords, API keys, profiles, databases or private prompts.**

New-model compatibility remains a priority, subject to verification. Displayed
dynamic models do not establish support in the new protocol. Latest-model and
long-text support are not guaranteed. Useful reports help prioritize work.

Editor-inserted `U+200B` escapes are accepted **only immediately before Markdown
list markers at line start**. Original captured payloads and proof remain unchanged;
there is no broad normalization. See the protocol document for the evidence limits.

## Development and provenance

```bash
python -m unittest discover -p "test_*.py" -q
python tools/release_check.py
```

CI runs offline: it does not log into Google or consume generation quota.
[Contributing](CONTRIBUTING.md) · [Security](SECURITY.md) · [Attribution](ATTRIBUTION.md)

Includes modified code from [HanaokaYuzu/Gemini-API](https://github.com/HanaokaYuzu/Gemini-API).
The bundled **GNU AGPL v3** [license](LICENSE) and upstream notices are preserved.
No official affiliation or endorsement is implied.
