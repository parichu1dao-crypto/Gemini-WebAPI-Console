# Experimental Cookie protocol / 实验性 Cookie 新协议

**Draft — production validation has NOT passed. Do not merge as a recovery release.**

Implements an explicit `process-session` Cookie HTTP adapter while retaining the
`legacy` default and the existing `/v1/chat/completions` interface. An enrolled
browser supplies validation only; generation POSTs from the browser are blocked.
No automatic browser fallback or replay of uncertain generation requests.

Includes per-account locking integration, shared request deadlines, explicit
not-submitted vs uncertain outcomes, length-prefixed ACK parsing, empty routing
identity handling, and model/terminal checks. Only the observed Pro model is
supported. Narrow Markdown-list editor escapes are recognized without modifying
the captured request or proof. Reader/writer HTTP sessions are isolated.

Validation: 224 offline tests pass; release scan reports zero findings. Early
isolated short requests succeeded, but subsequent integration tests still had
no-body timeouts and an upstream error card. One 31,502-character input timed out
at360 seconds without an answer. Translation usability is NOT established.
No account credentials, private logs, or session profiles are included.

## 中文

**草稿 PR：生产验证未通过，不应作为“翻译恢复”的稳定版本合并。**

新增显式 `process-session` Cookie HTTP 适配器，保留默认旧协议与现有翻译接口。
浏览器仅准备校验，正文由独立 HTTP 提交，不自动切到网页生成、不重放未知请求。
修正发送确认包长度前缀、空路由字段、截止时间、已提交状态跟踪等问题。

224项离线测试通过，发布扫描零发现，但正式集成仍存在无正文超时与上游错误卡片；
31,502字符长文本请求等待360秒未得到答案。**尚不能宣布翻译脚本已经可用。**
文档说明旧协议故障、新协议依赖、配置与尚未解决的限制；未上传任何账号凭据。

Feedback / 反馈：[parichu1dao@gmail.com](mailto:parichu1dao@gmail.com)
