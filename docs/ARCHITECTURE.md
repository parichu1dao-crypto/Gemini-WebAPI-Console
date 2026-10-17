# Architecture / 架构边界

## One application, explicit boundaries

| Layer | Owner | Responsibility |
| --- | --- | --- |
| Configuration / 配置 | `data/settings.json`, `service.py` | One default configuration; validated startup; bounded proxy waiting |
| Management / 管理 | `console.py`, `launcher.py`, `web/` | Local authenticated administration; shared configuration; no generation logic |
| HTTP API | `openai_compatible_server.py` | OpenAI contract, account selection, deadlines, admission, structured errors |
| Model catalog / 模型 | `cookie_model_catalog.py` | Resolve actual advertised models, not enum guesses or silent substitutions |
| Cookie transport | `cookie_transport.py`, `local_account_client.py` | Single-dispatch generation; account-scoped supplied credentials |
| Enrollment / 录入 | `import_browser_session.py`, `account_manager.py` | Explicit administrator login/identity checks; private credential storage |
| State / 状态 | `quota_policy.py`, `request_ledger.py`, `observability.py` | Quota, optional idempotency, usage history; private local databases |
| Upstream protocol | `src/gemini_webapi/` | Vendored protocol implementation; maintain attribution and upstream compatibility |

The normal path is `caller -> HTTP API -> Cookie transport -> Gemini`.
The management UI is not a required relay. Browser automation is separately
selected and is not an automatic retry path for uncertain Cookie submissions.
An existing external reverse proxy may continue forwarding to the API, but it
is not a dependency of this repository. Translation tasks and chapter validation
belong to the calling application, not this service.

正常路径为“调用程序 → API → Cookie → Gemini”。管理台不作为必经转发层；
浏览器通道明确选择，不在 Cookie 结果不确定时自动重放。原有外部网关可以继续
转发到 API，但不是独立仓库的依赖。小说任务与正文质检属于调用方，不放入服务内核。

## Runtime state is not source code / 私有运行数据不进入源码

`data/` contains settings, account sessions, logs, databases and backup files.
It is excluded from Git. Never publish it, browser profiles, account mappings,
real API keys or experiment artifacts. A copied repository needs its own private
configuration and explicit account enrollment. Environment keys override file
keys and never enter process arguments. Protect the directory with OS permissions;
Cookie theft is account-session theft. Local-only binding is intentional.

## Operational contract / 运行约定

- A healthy process is not proof that a model will answer. Verify authenticated
  `/health`, `/v1/models`, then one bounded generation when necessary.
- An unavailable account does not force all other accounts offline. Quota and
  cooldown are enforced; no fallback to a lower-tier model is implicit.
- Startup retries are separate from generation. No startup wrapper replays
  submitted work. `Idempotency-Key` gives callers durable replay protection.
- A completed response is not a chapter-quality pass. Token usage without
  upstream measurements is unknown/estimated, never exact.
- Runtime configuration is a startup snapshot. Console-owned services are
  stopped through the console; externally owned services through their owner.
  Do not restart during active requests just to apply settings.

## Before commercial deployment / 商业部署前

This change establishes maintainable boundaries, not production certification.
Still required: sustained load/long-text testing, quota observation, credential
rotation and encrypted-at-rest design, retention/deletion policy, multi-user
authorization if remote administration is introduced, dependency/security review,
upstream-terms and license compliance review, and documented recovery procedures.
Do not promise unsupported model identity, unlimited quota or service availability.

本次建立独立入口和清晰模块边界，不等于已经商业验收。长期压力测试、凭据轮换与
静态加密、日志保留策略、远程多用户权限、依赖安全、上游条款及许可证合规都需要
上线前继续落实。不得承诺无限额度、绝对模型身份或上游服务可用性。
