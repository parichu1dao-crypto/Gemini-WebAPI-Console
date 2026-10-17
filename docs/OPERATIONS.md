# 运维与安全 / Operations & Security

Open **8791 → 运维与安全 / Operations** after administrator login.
后台所有运维写入仍要求管理密钥、正确 Host 和同源校验。调用端密钥不是管理密钥。

## Request timeline / 请求诊断

Cookie request events: received → model_resolved → queued → account_acquired → generation_dispatched → upstream_headers → first_output → output_validated → completed, or a failure/uncertain outcome. Stages reflect observed events, not invented timestamps. `first_output` means the first nonempty SDK output, not a claim of true token-level latency. Browser relay records handoff and terminal state only.

Cookie 记录请求、排队、提交、上游响应头、首次非空输出、完成或未知结果；浏览器入口记录转交与结束。历史缺失事件不会伪造。模型字段仅为请求/接口报告值，不代表独立证明模型身份。

The latest 500 events are searchable by pseudonymous trace ID. Cookie JSON responses include `X-Request-ID`. Export is allowlisted metadata only: no prompts, outputs, cookies, credentials, account emails or raw exception strings. Unknown outcomes must be reconciled before replay.

## Drain and restart / 排空与重启

- Drain atomically rejects new work with `503`, `submitted:false`; accepted/queued work finishes.
- Cookie restart waits for HTTP admission count AND asynchronous tasks/cleanup to finish, up to 15 minutes. On timeout it stays drained and never force-kills or replays.
- Restart reexecutes the original Python entrypoint/configuration. Current in-memory clients are closed only after draining. Expired persisted Cookie sessions can still prevent startup: verify identity and explicitly refresh the session; restart cannot manufacture authentication or quota.
- Browser/managed entrypoints have drain/resume; their configuration is read per new request. Account browsers are not restarted.
- Saving staged Cookie model/strict policy does not hot-change in-flight work. Saved/applied values are compared against live health. A missing service is **unverified**, not “applied”.
- 自管旧停止按钮也先排空；有在途任务或无法确认状态时拒绝强杀。外部服务仍由其原入口负责，平滑重启由服务自己完成。

## Client keys / 调用端密钥

Create a named key with explicit model scope (no wildcard), 1–365 day expiry and 1–600 generation requests/minute. Only a SHA-256 digest is stored; the random 256-bit secret is shown once. Invalid, expired and revoked keys fail closed. Revocation applies to subsequent admissions, not already submitted work. Idempotency keys are namespaced per client to prevent cross-client cached-response reuse.

`Authorization: Bearer <client-key>` works with Cookie `/v1/models` and `/v1/chat/completions`, and the enabled console browser/managed endpoint. Models are filtered to the key scope. Keys cannot access `/health`, `/internal/operations` or management routes. The existing master API key remains compatible and unrestricted; do not distribute it to untrusted clients. Management remains loopback-only. Remote deployments require a separately secured TLS/auth boundary, not merely opening the port.

旧主密钥继续兼容，因此不会让现有翻译配置失效；要限制某个调用端，需将该调用端换成新密钥。新密钥无法控制服务或读取账号健康明细。

## Retention / 保留策略

Default 90 days, configurable 7–3650. A daily console worker and explicit CLEAN action remove expired operational events, audit rows and completed accounting records. Preview shows eligible counts. Running/unknown activity, request idempotency ledgers, raw legacy logs, chapter outputs, profiles and cookies are **not** deleted. Old audit entries age out by this policy; this local audit is not a tamper-proof compliance ledger. Account scope/name hashes are pseudonyms, not proof of anonymous data.

## Preference backups / 偏好备份恢复

Backups are restricted to `data/backups/<timestamp>-<random>.json` and contain a versioned allowlist of non-secret runtime preferences plus retention policy. They deliberately exclude login sessions, API keys, proxy addresses, call history, browser profiles and idempotency results. This is not a complete disaster-recovery backup. Restore requires RESTORE and a stopped API, validates schema/ranges, backs up current preferences first, preserves credentials/history and leaves the API stopped. Deleted history cannot be recovered from these preference backups.

恢复仅恢复非敏感运行偏好及保留策略，不能恢复登录、密钥或已清理记录。首次部署仍需重新录入账号/配置凭据。完整私有备份应使用操作系统加密备份，不应上传公开仓库。

## Verification / 核验

`python -m unittest discover -q` exercises the existing suite plus offline key scope/expiry/revocation, concurrent rate limiting, safe diagnostics, drain/restart timeout, cleanup refusal, backup schema/path protection and synthetic HTTP contracts. Tests do not generate real Gemini content. Live console checks additionally verify key creation/revocation, scoped model listing, rejection before submission, diagnostics download, preference backup and responsive layout.

Contact / 联系：parichu1dao@gmail.com — sanitized information only.
