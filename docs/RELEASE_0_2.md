# 0.2 preview — From gateway to console / 从网关到管理台

## 中文更新说明

本次升级保留原仓库链接与 AGPL-3.0 许可，将分散的脚本整理为独立部署的公开测试版。

| 更新 | 使用价值 |
|---|---|
| 统一入口 | Windows 安装脚本、启动器与 `app.py`；新安装生成独立随机密钥 |
| Cookie 与网页两种方式 | Cookie 失败而网页正常时，可由管理员打开专用网页并切换继续 |
| 新模型选择 | 动态读取 Cookie 模型，网页核对模型菜单；包含上游支持的 Gemini 4 Argon |
| 完整账号管理 | 登录窗口、身份核对、日期命名、重命名、启停与删除；历史记录保留 |
| 调用与 Token 统计 | 按天、周、月及最近五小时筛选；实际报告、估算和未知分别显示 |
| 更安全的失败处理 | 区分网络问题、账号问题、限额、未提交和结果未知，避免盲目重复发送 |
| 运维与反馈 | 客户端密钥、审计、排空、诊断、错误帮助、中英界面与问题模板 |
| 发布质量 | 独立虚拟环境测试、Windows/Linux 自动化检查与发布隐私扫描 |

**开始使用：** [安装指南](QUICKSTART.md) → [网页备选方式](BROWSER_FALLBACK.md)。
请先使用短请求验证；离线自动化测试通过不代表所有真实账号与模型都能生成。

### 下一步：支持作图

计划增加图像能力检测、作图请求、生成结果收取、下载和错误提示，完成端到端验证后再开放。
**当前版本仅支持文本，尚未提供可用的作图 API。** 不承诺发布日期；同时继续改善新模型
适配、会话恢复和诊断信息。欢迎通过 Issues 或 parichu1dao@gmail.com 反馈需求和新报错。
请勿发送 Cookie、密码、密钥、浏览器档案或私有正文。

## English release notes

This preview packages the existing gateway as a standalone local application,
retaining the repository URL, AGPL-3.0 license and upstream attribution.

| Change | Why it matters |
|---|---|
| Unified entry points | Windows installation, desktop launcher and `app.py`; random per-install secrets |
| Cookie + browser paths | Explicitly switch to a dedicated browser when Cookie fails but the website works |
| New-model selection | Dynamic Cookie catalog and browser menu checks, including upstream-supported Gemini 4 Argon |
| Account lifecycle | Login windows, identity checks, date-based names, rename, enable/disable and deletion |
| Usage visibility | Daily, weekly, monthly and last-five-hour views; reported, estimated and unknown usage separated |
| Safer failures | Separate network errors, identity issues, quotas, unsubmitted requests and uncertain outcomes |
| Operations & feedback | Client keys, auditing, drain controls, diagnostics, error help and bilingual UI/docs |
| Release checks | Clean-environment tests, Windows/Linux CI and publication privacy scan |

**Get started:** [installation](QUICKSTART.md) → [browser fallback](BROWSER_FALLBACK.md).
Verify one short request first. Offline CI success is not proof that every live
account or advertised model can generate successfully.

### Next: image generation

Planned work: capability detection, image requests, result collection, downloads
and actionable errors. Availability requires end-to-end validation first.
**This version is text-only; no working image API is included.** No date is
promised. New-model compatibility, recovery and diagnostics remain priorities.
Send feedback through Issues or parichu1dao@gmail.com, never credentials or
private conversation content.
