# Cookie protocol / Cookie 协议

Upcoming **experimental 0.2.1**: implemented code for a planned public draft PR,
not a merge, stable launch or release. **Do NOT switch production based on this PR.**
The baseline `legacy` default is retained. Older builds may reject the new configuration. Branding and screenshots
describe the existing console, not evidence of new-protocol validation.

即将推出的**实验性 0.2.1**：代码已实现，计划创建公开草稿 PR，不是合并、稳定版上线或发布。
**不要根据此 PR 切换生产环境。** 保留基线 `legacy` 默认值。旧构建可能拒绝新配置。
现有品牌与截图不构成新协议验证证据。

## Protocol boundary / 协议边界

| Configuration / 配置 | Behavior / 行为 |
|---|---|
| `cookie_protocol: "legacy"` | Default existing Cookie HTTP path; no open webpage required per request, without a guarantee of upstream compatibility. 默认旧 Cookie HTTP 路径，每次请求不要求打开网页，但不保证上游兼容。 |
| `cookie_protocol: "process-session"` | Explicit opt-in; independent Cookie HTTP sends the `processSession` body. 主动启用，由独立 Cookie HTTP 客户端发送 `processSession` 正文。 |

The new path requires an enrolled, dedicated, signed-in browser to obtain fresh
validation and a request bundle. **All browser generation POSTs are blocked**;
the browser does not submit generation. This is not the separate managed-browser
transport. This experiment is Cookie-only, with no fallback to legacy or managed-browser generation.
Do not automatically retry a submission whose outcome is uncertain, including
after a timeout, disconnect, restart or transport change.

新路径必须有已录入、已登录的专用浏览器，以获取新鲜验证与请求包。
**所有浏览器生成 POST 均被阻断**，生成不由浏览器提交；它与托管网页生成通道不同。
本实验仅使用 Cookie，不回退旧协议或托管网页。超时、断线、重启或切换通道后，仍不得自动重试
已提交但结果未知的请求；应先核对提交状态。

Only the observed Pro model ID is accepted. Other models are rejected as
`notSubmitted`; unverifiable selection fails closed, without silent substitution. A model
listed by the general catalog is not automatically supported by this protocol.
Fresh validation is not a cryptographic guarantee of model identity or entitlement.

仅接受已观察到的 Pro 模型 ID。其他模型以 `notSubmitted` 拒绝；无法验证的选择拒绝执行，不静默换模型。
通用目录列出的模型不代表新协议支持；新鲜验证也不是模型身份或账号权益的密码学证明。

## Deployment

**Isolated experimental testing only, not production deployment guidance.**
**仅限隔离实验测试，不是生产部署建议。**

### English

1. Use a build that actually includes the new adapter. Drain in-flight requests,
   reconcile unknown outcomes, and privately back up existing configuration before
   changing it. Do not clear request-protection records.
2. Use the existing account manager to enroll a dedicated browser, sign in, verify
   the expected identity and retain that account's browser binding. Keep the
   dedicated browser available. Do not substitute an arbitrary browser/profile or
   guess binding identifiers. Cookie enrollment and managed-browser enrollment are
   separate stores; creating one does not silently import the other. Follow the
   existing explicit enrollment procedure in [Quick start](QUICKSTART.md).
3. Merge the following fields into private `data/settings.json` (or the existing
   configuration supplied to the service). This is a **partial configuration**, not
   a replacement file. Leave `api_key`, `admin_key`, account settings and other
   existing values unchanged. No new UI selector is assumed.

```json
{
  "transport": "cookie",
  "cookie_protocol": "process-session"
}
```

4. Restart the Cookie API with `python app.py api` after draining. Keep the existing
   API key and client base URL (default `http://127.0.0.1:8787/v1`). Use only the
   adapter's supported model and start with one short request. Missing/ambiguous
   bindings or failed validation must stop the request, not trigger another path.

Omitting `cookie_protocol` selects `legacy`; selecting `legacy` explicitly is an
operator configuration decision, not permission to replay an uncertain request.
An offline installation check is not end-to-end protocol validation. Do not deploy
unattended long-text workloads on the strength of short-response tests.

### 中文

1. 先确认构建实际包含新适配器。停止接收新请求、等待在途请求结束、核对未知结果，
   私下备份现有配置；不要清除请求保护记录。
2. 使用已有账号管理器录入专用浏览器，登录、验证预期身份，并保留该账号的浏览器绑定。
   保持专用浏览器可用，不借用任意浏览器或猜测绑定标识。Cookie 与托管网页账号存储
   独立，创建其中之一不会静默导入另一个；按[快速开始](QUICKSTART.md)明确录入。
3. 将上面的 JSON 字段合并到私有 `data/settings.json` 或当前服务配置中。
   这是局部配置，不是整文件替换；保留原 `api_key`、`admin_key`、账号设置及其他值。
   本文不假设新增协议选择 UI。
4. 排空后用 `python app.py api` 重启 Cookie API。客户端沿用原密钥与 Base URL，
   默认 `http://127.0.0.1:8787/v1`。只使用适配器明确支持的模型，先验证一个短请求。
   绑定缺失、歧义或验证失败时应停止，不得切换其他路径继续提交。

省略 `cookie_protocol` 时使用 `legacy`。手动选回 `legacy` 不代表可以重放未知请求。
离线安装检查不等于端到端协议验证；不要根据短回复测试就部署无人值守长文本任务。

## Client compatibility / 客户端兼容

The client-facing HTTP endpoint remains **`/v1/chat/completions`**, with the same
compatible request/response format and existing bearer-key authentication. Changing
the Cookie protocol does not require switching to the managed-browser API port.
Initial streaming may buffer the answer until it is validated, then emit compatible
stream output; it does not promise live upstream token-by-token delivery. HTTP
compatibility is not a guarantee of every model or feature.

客户端仍使用 **`/v1/chat/completions`**，保持兼容的请求/响应格式与原 Bearer 密钥鉴权。
更换 Cookie 协议不需要改用托管网页 API 端口。初期流式响应可能先缓存回答，验证通过后
再输出兼容流，而非上游逐 Token 实时透传。HTTP 兼容不代表所有模型或功能都受支持。

## Evidence and limits / 证据与限制

- Earlier `StreamGenerate` lab requests failed even after startup authentication.
  This is an observed old-path failure amid changed upstream behavior, **not proof
  that Google globally removed the protocol**.
- The implementation passes 224 offline tests, including fixes for length-prefixed
  ACKs and empty routing IDs. Offline correctness is not live-generation validation.
- Earlier short Pro lab requests passed on three accounts. Later integrated
  requests timed out without a response body; earlier lab success does not override this failure.
- A separate account produced an error card. Its operator-imposed 12-hour cooldown
  is a local precaution, **not evidence of upstream quota exhaustion**.
- A 31,502-character input was actually submitted and timed out after 360 seconds
  without an answer. This is a failed integration test, not validated long translation
  or a `notSubmitted` result. Preserve uncertain-submission protection; do not retry.
- No translation-usability claim is made. The newest model is not verified on this
  protocol; all-account support and production readiness are not established.

- 旧 `StreamGenerate` 实验在启动鉴权后仍失败。这是上游行为变化期间观察到的旧路径
  故障，**不能证明 Google 已全面移除协议**。
- 实现通过 224 项离线测试，包括长度前缀 ACK 与空路由 ID 处理修复；离线正确不等于线上生成验证。
- 此前三个账号的短文本 Pro 实验通过，后续集成请求却出现无响应正文超时；不能用此前成功覆盖此次失败。
- 另有账号出现错误卡片；操作人员手动设置的 12 小时冷却属于本地预防措施，
  **不是上游额度耗尽的证据**。
- 31,502 字符输入已实际提交，360 秒后超时，没有回答。这是集成测试失败，不是长篇翻译
  验证通过，也不是 `notSubmitted`。必须保留未知提交保护，不重试。
- 不声称翻译可用。最新模型尚未在此协议验证，全部账号支持与生产就绪也未得到证明。

## Narrow editor escape handling / 严格限定的编辑器转义处理

Editor-inserted `U+200B` escapes are accepted **only immediately before Markdown
list markers at line start**. This narrow exception does not authorize stripping
zero-width characters elsewhere, whitespace cleanup, Unicode normalization or
other broad rewriting. The original captured payload and proof are kept unchanged.

编辑器插入的 `U+200B` 转义**仅允许位于行首 Markdown 列表标记紧前方**。
此局部例外不允许清除其他位置的零宽字符、清理空白、Unicode 归一化或其他宽泛改写。
原始捕获载荷与证明保持不变。

## Feedback / 反馈

Email [parichu1dao@gmail.com](mailto:parichu1dao@gmail.com) with version, selected
protocol, redacted error, and whether submission occurred. Never publish account
names/emails, conversation IDs (CIDs), local user paths, cookies, keys, browser
profiles, request bundles or private prompts.

请向 [parichu1dao@gmail.com](mailto:parichu1dao@gmail.com) 反馈版本、协议、脱敏错误及
是否已提交。不要公开账号名/邮箱、会话 ID（CID）、本机用户路径、Cookie、密钥、
浏览器配置、请求包或私有正文。
