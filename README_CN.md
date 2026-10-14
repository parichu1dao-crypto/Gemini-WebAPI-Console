# Gemini WebAPI Console

![Gemini WebAPI Console — 本机管理与可选 Pro 校验](web/banner.svg)

### 网页会话，本地接口。让每一次请求都有迹可循。

[English](README.md) · [安全说明](SECURITY.md) · [开源许可证](LICENSE)

基于 Gemini 网页客户端的自托管控制台：**OpenAI 兼容接口、账号池、可选的严格 Pro 校验**，同时提供网页管理界面与 Windows 启动器。

不是只会不断重试的黑盒，而是让你看清请求在哪里排队、在哪里中断、恢复是否成功。

> 基于 [HanaokaYuzu/Gemini-API](https://github.com/HanaokaYuzu/Gemini-API) 的社区衍生项目，非 Google 官方产品。依赖网页服务的可用性、账号授权和额度，不提供无限额度或绕过权限的能力。

## 做了哪些优化？

| 优化 | 价值 |
| --- | --- |
| 网页控制台 + Windows 启动器 | 两处都可切换严格 Pro 校验，共享同一配置 |
| 严格 Pro 默认开启 | 按后端可获得的模型信息与账号资格检查，用户可自行关闭 |
| 单账号请求串行化 | 同账号排队，减少请求相互干扰；不同账号可并行 |
| 请求全生命周期诊断 | 记录排队、取消、流式结束和恢复过程，便于定位故障 |
| 超时取消与有条件恢复 | 取消超时工作，限制不满足条件的对话恢复 |
| 独立配置与随机密钥 | 不附带个人账号、Cookie、浏览器档案或生产配置 |

**严格校验不等于模型身份的独立证明。** 它依赖上游返回的元数据，不能证明 Google 实际运行的底层权重。关闭校验只会放宽检查，不会解锁 Pro、增加额度，也不保证始终使用指定模型。

## Windows 快速开始

安装 Python 3.10 或更新版本（需包含 Tkinter），在项目目录运行：

```powershell
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe launcher.py
```

以后双击 **Start-Windows.bat**。启动器会启动本地管理服务；关闭启动器窗口不会自动关闭管理服务。点击 **Stop** 停止它管理的 API 工作进程，正在执行的请求会被中断。

只使用网页界面，可运行 `.venv\Scripts\python.exe console.py`，然后打开 **http://127.0.0.1:8790/**。

首次运行生成 `data/settings.json`：`admin_key` 用于登录控制台，`api_key` 用于请求接口，两者不同，均不可公开。

### 导入账号

每个账号的 Cookie 导出保存为 `data/accounts/<账号名>.json`，仅使用你拥有或获授权的账号。结构如下：

```json
{"cookies": [{"domain": ".google.com", "name": "__Secure-1PSID", "value": "替换为你的私密Cookie"}]}
```

如果有当前有效的 `__Secure-1PSIDTS`，一并加入 cookies 数组。浏览器档案目录不能直接当作 Cookie JSON。存在账号文件不代表会话有效，须观察启动后的初始化日志。不要将真实 Cookie 发给维护者或提交仓库。

### 切换校验策略

1. 先停止 API 服务。
2. 在网页或 Windows 启动器勾选/取消 **Strict Pro**，保存。
3. 启动 API，检查 `data/server.log` 中的账号初始化结果。

默认接口地址为 `http://127.0.0.1:8787/v1`。界面显示进程运行不等于已通过真实生成测试。模型选项是当前客户端的映射，并非 Google 官方实时模型目录。请求示例见 [英文说明](README.md#api-example)。

代理可填写 `http://127.0.0.1:7890` 等本机实际地址；先启动 VPN，再启动服务。留空会向客户端传入忽略代理参数，但不保证绕过所有依赖或操作系统的网络配置。本项目不会修改其他软件代理，也不会自动安装开机计划任务。

## 遇到错误时

- 登录失效：重新登录并更新自己的 Cookie。
- 连接被拒绝：检查服务是否启动、代理端口是否正确。
- 额度或权限错误：在官方网页核实可用模型与剩余额度，不能只看订阅名称。
- 响应中断：查看根目录下 `openai_request_diagnostics.jsonl`、`openai_account_attempts.jsonl` 和 `data/server.log`。
- 运行中不允许修改设置：先停止、保存，再启动，避免新策略干扰在途任务。

分享日志前应删除账号标识、正文与所有密钥。离线回归测试命令：`python -m unittest discover -v`，通过不代表线上账号一定可用。

## Gemini 4 适配承诺

**我们承诺：上游网页服务开放 Gemini 4 且具备可验证的接入条件后，将优先推进适配，尽快发布兼容版本。** 这是后续维护计划，不表示当前已支持 Gemini 4，也不承诺尚无法验证的发布日期；实际适配取决于上游权限、协议与测试结果。

## 一起改进

欢迎提交 Issue、改进建议或可复现的故障报告。反馈邮箱：**[parichu1dao@gmail.com](mailto:parichu1dao@gmail.com)**。请附版本、系统、脱敏报错与复现步骤，勿发送登录凭据或私人聊天记录。如果它帮到了你，欢迎 Star，让更多人找到这个项目。

## 开源与致谢

使用 **GNU AGPL v3**，保留上游许可证；来源与修改范围见 [ATTRIBUTION.md](ATTRIBUTION.md)。对外提供修改后的网络服务时，请遵守相应源码提供义务。请遵守上游服务条款，不绕过账号限制。
