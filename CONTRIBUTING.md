# Contributing / 参与贡献

Please open an issue with a redacted error, version, OS, transport and whether the
request was submitted. Never upload cookies, access tokens, browser profiles,
account email addresses, logs containing chapter text, or `data/` databases.

请附版本、系统、通道、脱敏错误和是否已经提交。不要上传账号、Cookie、密钥、
浏览器目录、正文或数据库。反馈邮箱：parichu1dao@gmail.com。

Run `python -m unittest discover -p "test_*.py" -q` and
`python tools/release_check.py` before submitting a PR. Live model tests are
manual and opt-in; CI must not consume an account's quota. Preserve the AGPL
license, upstream attribution, and the no-blind-replay invariant.
