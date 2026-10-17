# Gemini WebAPI Console

![Gemini WebAPI Console](web/banner.svg)

### 你的会话，你的控制台。让每次请求都有迹可循。

[English](README.md) · [快速开始](docs/QUICKSTART.md) · [已知限制](docs/RELEASE_SCOPE.md) · [更新记录](CHANGELOG.md)

**0.2.0 公开测试版：面向个人部署的 Gemini 账号与调用管理台。**
把登录、模型选择、调用记录、限额保护和错误诊断集中在一起，而不是遇到失败只会不停重试。

> 独立社区项目，非 Google 官方产品或官方 API。账号权限、额度及上游服务限制仍然适用。
> 本版用于收集反馈，不承诺生产 SLA，也不是多租户云服务。

## 能做什么？

### 平时用 Cookie，必要时用网页继续

当 Cookie 调用失败，但同一账号在 Gemini 网页仍能正常回复时，可以打开账号的
独立浏览器，核对身份，在运行设置选择**本台托管浏览器**，启动该通道继续调用。
客户端保持相同的聊天接口格式，将 Base URL 从 `8787/v1` 改为 `8791/v1`。
这是**管理员明确选择的备选方式**，不是失败后偷偷重发；结果未知的请求必须先核对。

**可选择新一代模型，包括 Gemini 4 Argon。** 刷新模型列表，选择本账号实际可用的模型。
两种通道均保留请求模型检查，不会因为目标模型不可用就静默换成 Flash。
模型名称、开放范围与账号资格以上游实际返回为准，不承诺所有账号始终可用。

## 界面预览

以下是实际管理台使用虚构演示数据的截图，不是生产成功率证明，不包含真实账号或密钥。

![中文用量管理台](docs/images/overview-zh.png)

<details>
<summary>展开账号管理：录入、验证、启停、重命名与删除</summary>

![中文账号管理](docs/images/accounts-zh.png)

</details>

## 这次更新了什么？

从原来的网关脚本整理为可独立安装的本地应用：统一安装启动入口、账号生命周期、
网页备选通道、逐次调用统计、错误分类、限额保护与运维管理，并补全中英文说明。
完整清单见[本次发布导览](docs/RELEASE_0_2.md)，实际切换步骤见
[Cookie 失败后如何使用网页继续](docs/BROWSER_FALLBACK.md)。

| 功能 | 说明 |
|---|---|
| 账号管理 | 独立登录窗口、身份验证、重命名、启停和删除 |
| 双通道文本调用 | Cookie API 与托管网页；明确选择，不偷偷切换 |
| 模型选择 | 读取可用模型、检查请求模型，不静默降级 Flash |
| 使用统计 | 按账号及日/周/月/自选时间查看；区分真实报告与估算 Token |
| 安全恢复 | 排队准入、超时边界、未知提交保护、Cookie 请求去重 |
| 运维工具 | 调用密钥、模型权限、审计事件、诊断与排空控制 |
| 两种界面 | 双语网页管理台及 Windows 启动器 |

**严格 Pro 默认开启。** Cookie 校验依赖上游元数据，不是模型身份的独立证明。
托管网页尚不能核验付费 Pro 资格，启用该通道需管理员明确调整额外校验策略；
不能把“网页出现 Pro 菜单”当成付费授权证明，也不允许静默换模型。

## 下一步：支持作图

计划接入图像能力检测、作图请求、结果收取与下载，并完善失败提示。
**作图是下一阶段计划，不是本版已上线的能力。** 完成端到端验证后再开放，暂不承诺日期。
同时继续跟进新模型适配和会话恢复；欢迎通过 Issues 或邮件反馈实际需求。

## 安装与启动

需要 Python **3.11+**。Windows 在项目目录运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\Install-Windows.ps1
.\Start-Windows.bat
```

命令行方式：

```bash
python -m pip install -r requirements.txt
python app.py init
python app.py doctor
python app.py console
```

管理地址：**http://127.0.0.1:8791/**。首次运行随机生成独立管理密码和 API 密钥，
保存在私有 `data/settings.json`；不附带任何用户账号，不修改其他软件的代理。

- Cookie 接口：运行 `python app.py api`，默认 **8787/v1**。
- 托管网页接口：在管理台录入、验证并启用账号，选择 managed 通道并启动 API，使用 **8791/v1**。
- 网页通道需要已登录的专用浏览器保持运行，不要关闭对应标签页。
- 外部网页服务只是兼容选项，不要求用户另找翻译项目或个人脚本。

账号录入、接口示例、代理和升级方法见 **[快速开始](docs/QUICKSTART.md)**。

## 不掩盖故障，也不夸大能力

网络超时、TLS 错误、页面身份信息尚未加载，不等于账号失效。发送前故障应短暂退避；
明确身份不符、真实限额和“已提交但结果未知”分别处理。未知请求不能盲目重发或删除保护标记。

网页 Token 可能只是估算；请求成功不等于正文审核通过。**本测试版 API 不支持生图。**
Cookie 通道仍可能在网页正常时失败，网页自动化也可能受 UI 更新影响。
请先阅读 [发布范围与限制](docs/RELEASE_SCOPE.md)。

## 一起把它做好

欢迎提交 [Issue](https://github.com/parichu1dao-crypto/Gemini-WebAPI-Console/issues/new/choose)，
或联系 **[parichu1dao@gmail.com](mailto:parichu1dao@gmail.com)**。
请附版本、系统、通道、脱敏错误及是否已经提交；不要发送 Cookie、密码、密钥、浏览器目录、数据库或正文。

我们会优先推进 Gemini 4 及后续新模型在上游具备可验证条件时的适配，不承诺尚未验证的发布日期或无限额度。
如果项目对你有用，欢迎 Star；准确的故障反馈同样重要。

## 开发与来源

```bash
python -m unittest discover -p "test_*.py" -q
python tools/release_check.py
```

CI 只做离线测试，不登录 Google、不消耗账号生成额度。
保留 [HanaokaYuzu/Gemini-API](https://github.com/HanaokaYuzu/Gemini-API) 来源和 **GNU AGPL v3** 许可证。
详见 [贡献指南](CONTRIBUTING.md)、[安全说明](SECURITY.md)、[来源说明](ATTRIBUTION.md) 和 [LICENSE](LICENSE)。
