# Changelog

## Upcoming experimental 0.2.1 — draft PR / 即将推出的实验版（草稿 PR）

- Cookie adapter implemented; 224 offline tests pass. Fixed length-prefixed ACK
  handling and empty routing IDs. These results do not prove live integration works.
- Public draft PR planned, not a merge or release. **Do not switch production based
  on this PR.** `cookie_protocol` remains `legacy` by
  default; `process-session` requires explicit opt-in and an adapter-enabled build.
- Independent Cookie HTTP sends the `processSession` body. An enrolled, dedicated,
  signed-in browser provides fresh validation and the request bundle only; all
  browser generation POSTs are blocked in this path. Cookie-only, no fallback.
- Only the observed Pro model ID is accepted; other models fail closed as `notSubmitted`.
  Never automatically retry an uncertain submission.
- Client HTTP compatibility remains `/v1/chat/completions`. Initial streaming may
  buffer an answer until validated, rather than relay live upstream tokens.
- Earlier short lab requests passed on three accounts; later integrated requests
  timed out without a response body. A 31,502-character input was actually submitted
  and timed out after 360 seconds without an answer. Long translation is not validated;
  no translation-usability claim. The newest model is unverified on the new protocol.
- Editor `U+200B` escapes are accepted only immediately before Markdown list markers
  at line start. Original captured payload/proof stays unchanged; no broad normalization.
- Earlier `StreamGenerate` lab failures persisted after startup authentication.
  Observed upstream changes do not prove global removal of the old protocol.
  An error card and an operator-set 12-hour cooldown do not prove quota exhaustion.
- Bilingual protocol/deployment documentation; branding and screenshots retained.
  This provisional entry is not a claim that the adapter has shipped.

- Cookie 适配器已实现，224 项离线测试通过；修复长度前缀 ACK 与空路由 ID 处理，
  但离线通过不代表线上集成成功。计划创建公开草稿 PR，不是合并或发布，**不要切换生产环境**。
- `cookie_protocol` 保留默认 `legacy`，`process-session`
  必须主动启用，并使用包含适配器的构建。
- 独立 Cookie HTTP 发送 `processSession` 正文；已录入、已登录的专用浏览器仅用于
  新鲜验证与请求包准备，该路径阻断所有浏览器生成 POST，仅使用 Cookie、无回退。
- 仅接受已观察到的 Pro 模型 ID，其他模型以 `notSubmitted` 拒绝；结果未知的提交不得自动重试。
- 保持 `/v1/chat/completions` HTTP 兼容；初期流式响应可能在验证回答后才输出。
- 此前三个账号短文本实验通过，后续集成请求无响应正文超时。31,502 字符输入已实际
  提交，360 秒超时、无回答；长篇翻译未验证通过，不声称翻译可用。新协议未验证最新模型。
- 编辑器 `U+200B` 转义仅允许在行首 Markdown 列表标记紧前方；原始捕获载荷与证明
  保持不变，不做宽泛归一化。
- 旧 `StreamGenerate` 实验在启动鉴权后仍失败，但不证明 Google 全面移除旧协议。
  错误卡片与操作人员手动设置的 12 小时冷却都不能证明额度耗尽。
- 更新双语协议及部署文档，保留品牌与截图；此暂定记录不代表适配器已经发布。

Details / 详情：[Cookie protocol](docs/COOKIE_PROTOCOL.md).
Feedback / 反馈：[parichu1dao@gmail.com](mailto:parichu1dao@gmail.com).

## 0.2.0-preview.1 — 2026-10-17

- Standalone local application; fresh per-install secrets and non-destructive setup.
- Windows installer, one CLI, offline doctor, English/Chinese onboarding.
- Account enrollment, dedicated browser profiles, verification, rename and deletion.
- Activity reporting by date/account; token estimates explicitly distinguished.
- Client API keys, model scopes, audit records, drain controls and diagnostics.
- Cookie dynamic model catalog, request deduplication and bounded dispatch budgets.
- Browser transient failures distinguished from identity mismatch; unknown replies
  remain protected against blind resubmission. Same-tab conversation reuse.
- Public issue templates, Windows/Linux CI, release-file privacy checks.
- Bilingual release tour, illustrated dashboard documentation and explicit
  Cookie-to-browser recovery steps. Image generation added to the roadmap,
  not advertised as an available feature.
- Text-only preview: image API disabled. No promise of upstream availability,
  exact browser token counts, or cryptographic verification of model identity.

本版为公开反馈测试版，不是生产 SLA 承诺。仅发布程序代码，不含任何本机账号或运行数据。
