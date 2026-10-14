# Security / 安全说明

- The manager and its API worker bind to loopback by default. Do not expose them directly to the internet. / 默认仅监听本机，不要直接暴露公网。
- Management and API keys are generated locally. Cookie exports grant account access. Treat `data/` as confidential. / 密钥本地生成，Cookie 可授予账号访问权限，整个 data 目录须保密。
- No credentials are distributed. `.gitignore` is a safeguard, not a substitute for reviewing commits. / 仓库不包含真实凭据，提交前仍需人工检查。
- Logs can contain sensitive request metadata or errors. Sanitize before sharing. / 日志可能包含敏感信息，分享前脱敏。
- This is a web-client integration, not a guaranteed-stable official API. Account restrictions and upstream changes may break it. / 网页协议可能变化，账号权限仍受上游控制。
- Report vulnerabilities privately to parichu1dao@gmail.com without sending live secrets. / 安全问题请私下邮件反馈，不要发送真实密钥。
