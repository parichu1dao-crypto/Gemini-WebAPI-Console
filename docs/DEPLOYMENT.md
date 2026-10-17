# Service deployment / 服务部署

## 中文

### 范围与当前接入条件

`service.py` 是现有 `openai_compatible_server` 的进程内 Cookie 启动入口，不是新生成客户端，也不是操作系统服务安装器。默认仅绑定 `127.0.0.1:8787`；无浏览器 fallback、无生成重试、无发布操作。依赖使用仓库现有 `requirements.txt`，入口本身只用标准库。

**默认配置唯一来源为项目 `data/settings.json`。** 不导入 `console.py`，不调用其自动生成/保存配置函数，不创建、改写或迁移私人设置。相对 `account_dir` 以所选 JSON 的所在目录解析，默认 `accounts`，所以默认配置对应 `data/accounts`。默认配置路径相对于入口文件，不依赖工作目录；显式相对 `--config` 则相对于调用者工作目录。

现有 `main(argv=None)` 已可作为调用接口，但默认三次启动尝试还要求下面的 `StartupError` 契约。缺失时会在启动之前报接口错误，不用 monkey-patch、全局 `sys.argv` 替换或子进程兜底。`retry.attempts=1` 可使用只支持 `main(argv=None)` 的后端，但不提供后端启动重试或修复其资源清理缺陷。

### 配置与运行

已有 console 设置时直接复用，不要用示例覆盖它。新安装可由操作者参考根目录 `config.example.json` 创建私有 `data/settings.json`，并准备 `data/accounts`。示例没有任何有效密钥，不能原样直接启动。

```text
python service.py --check
python service.py
python service.py --config data/service.json --check
python service.py --config data/service.json
python service.py --log-file data/server.log
python -B -m unittest test_service -v
```

以上启动命令供部署者执行，测试不会执行它们。`--check` 仅读配置、检查目录及 server 参数/接口，不等待代理、不启动 server、不读取账号 Cookie、不发送 HTTP/生成请求。它会导入 server 模块，因此 server 导入必须无网络副作用。

`data/service.json` 仅作为显式 `--config` 的兼容文件；不会自动查找或与 settings 合并。文件缺失直接失败，没有另一个默认源。配置按启动时快照读取，不热更新。

### Windows 计划任务日志

计划任务由部署者创建，本入口不注册或修改任务。将程序设为安装环境中的 Python 可执行文件，参数使用入口文件的绝对路径及 `--log-file data/server.log`，不传 API key。默认不重定向；显式启用日志后，将 Python stdout/stderr 追加写入同一个 UTF-8、行缓冲文件。相对日志路径以项目目录解析，不受计划任务工作目录影响；父目录须预先存在，打不开日志就失败且不启动 backend。重试提示和安全错误会入日志，不打印配置内容或密钥，退出时恢复原 stdout/stderr。第三方独立日志 sink、原生文件描述符输出不在 Python 重定向保证范围内，backend 仍需自行脱敏。不包含日志轮转，部署者负责保留策略。不要把任务配置成自动重放生成请求。

### 字段映射

|JSON 字段|默认值 / 行为|后端参数|
|---|---|---|
|`host`, `port`|`127.0.0.1`, `8787`；仅 IPv4 loopback，`localhost` 归一化为 `127.0.0.1`|`--host`, `--port`|
|`model`|`gemini-3-pro`；兼容 `default_model`，两者冲突报错|`--default-model`|
|`strict_pro`, `allow_free_pro`|`true`, `false`；strict 模式按 server 的模型归一化校验|`--strict-pro` 或 `--allow-non-pro`，`--allow-free-pro`|
|`account_dir`, `accounts`|`accounts`, `all`；也可账号名数组或逗号分隔字符串|`--account-dir`, `--accounts`|
|`proxy`, `ignore_proxy`|`null`, `false`；console 的空字符串等于 `null`；显式代理与 ignore 互斥|`--proxy`, `--ignore-proxy`|
|`init_timeout`, `request_timeout`, `queue_timeout`|`60`, `300`, `2` 秒|对应三个 timeout 参数|
|`account_failure_threshold`, `account_cooldown`|`1`, `900` 秒|同名连字符参数|
|`skip_verify`, `auto_refresh`, `quiet`|均 `false`|对应布尔开关|
|`api_key`|`null`；有效环境变量 `GEMINI_CONSOLE_API_KEY` 优先|仅进程环境，不生成 `--api-key`|
|`proxy_wait_seconds`|`60`；兼容 `proxy_wait_timeout`，冲突报错|不传后端|
|`proxy_poll_interval`, `proxy_connect_timeout`|`1`, `1` 秒|不传后端|
|`retry`|`{"attempts":3,"initial_seconds":1,"max_seconds":8}`|不传后端|
|`admin_key`, `browser_*`, `brower_*`, `managed_model`|允许现有 console 元信息，读取后丢弃|不传后端，不打印|
|`transport`|允许 `cookie` / `browser` / `managed` 元信息；此入口始终 Cookie|不传后端|

console 负责通道分发：仅 Cookie worker 应调用此入口。`transport=browser/managed` 在共享配置中可以保留，但直接运行 `service.py` **仍是 Cookie**，不会启动浏览器、读取 browser key 或使用 managed model。不应把它当作自动通道切换器。

兼容旧平铺字段 `startup_attempts`、`startup_backoff_initial`、`startup_backoff_max`，但与 `retry` 同时提供不同值会报错。未知非元信息字段、重复 JSON 字段、错误类型、非有限数值和非法范围均拒绝。布尔值必须是 JSON boolean，不接受字符串或整数。

验证范围：端口 1–65535；init timeout 0.1–3600 秒；request timeout 0.1–86400 秒；queue timeout 0.01–3600 秒；failure threshold 1–100；cooldown 0–86400 秒；代理等待 0–900 秒；代理轮询 0.01–60 秒；连接 timeout 0.01–30 秒；总尝试次数 1–10；初始退避 0.01–60 秒；退避上限 0.01–300 秒且不小于初始值。账号名不能有路径分隔符、重复项、空项或 `.` / `..`；入口只检查账号目录存在，不读取 Cookie 内容或认证账号。

### 代理晚启动与重试边界

仅对**显式配置的代理**检查 TCP 端口。支持 `http`、`https`、`socks5`、`socks5h`，禁止 URL 用户名/密码、查询串及业务路径。省略端口分别为 80 / 443 / 1080 / 1080。启用等待时只接受 IP 或 `localhost`，避免系统 DNS 超时不受控；域名代理须显式设 `proxy_wait_seconds=0` 跳过等待。

TCP 连接成功不证明代理可用、认证有效或上游畅通，不会发送 HTTP/TLS/生成数据。`proxy=null` 保留现有 backend 的账号代理/环境代理行为；入口无法预等候私人账号内的代理。若需要确定性等待，应显式提供不含凭据的代理 URL。`ignore_proxy=true` 完全交由 backend 的对应开关执行。

代理超时与符合契约的启动异常使用有限指数退避：第 n 次失败后等待 `min(initial_seconds * 2**(n-1), max_seconds)`，`attempts` **包括首次尝试**。默认最多三次，间隔 1、2 秒；每次都重新执行有限 TCP 等待。代理等待总预算最多为 `attempts * proxy_wait_seconds + 退避之和`，不含 backend 初始化；初始化时间由 backend 负责，可能随账号数增加。

普通异常、认证/配置失败、非零返回码、启动完成后的错误均不得重启。入口没有生成请求重试循环，不替代 backend 的提交不确定性处理。生成超时不代表未提交；禁止外层服务管理器自动重放请求。

Ctrl+C 在 TCP 等待、退避、正常 backend 调用中可打断；入口捕获 `KeyboardInterrupt` 返回 130。server 若自行处理 SIGINT 后正常返回，则沿用其返回码。退出码：0 正常/检查通过，1 失败或尝试耗尽，2 配置/接口不兼容，130 中断；server 主动返回其他整数则原样返回且不重启。

### 给 server 维护者的必要接口

1. `build_parser()` 无副作用；支持上述参数，尤其 `--queue-timeout`；在**构建 parser 时**读取 `GEMINI_CONSOLE_API_KEY` 为 API key 默认值。
2. `main(argv=None)` 内用 `build_parser().parse_args(argv)`；接受进程内参数列表，不能依赖替换 `sys.argv`。
3. 导出 `STRICT_PRO_MODEL` 与 `normalize_model_name(model, default_model)`，供严格模型验证。
4. 导出 `StartupError(Exception)`；异常实例只有在**尚未处理任何请求、启动资源已经全部回收、故障确定可重试**时才同时带 `startup_only=True`、`cleanup_complete=True`、`retryable=True`。标记必须是布尔值，缺失即禁止重试。认证、凭据、配置、模型资格及端口被占等不可通过重复调用修复的故障标记 `retryable=False`。
5. `ServerState.start()`、HTTP bind、信号注册等均纳入异常清理。清理所有已创建客户端、事件循环/线程、HTTP socket；还原信号处理器和全局 `STATE` 后才设 `cleanup_complete=True`。清理不完全、初始化结果不明或 KeyboardInterrupt 不包装成可重试错误。启动成功后直到停止，**绝不抛可重试 StartupError**。
6. server 自身也须脱敏日志/异常；入口只保证不打印其捕获的原始异常，不能替已有 backend stdout 脱敏。

概念接口（不是对现有 server 的已实施修改）：

```python
class StartupError(RuntimeError):
    def __init__(self, *, retryable, cleanup_complete):
        super().__init__("Startup failed")
        self.startup_only = True
        self.retryable = retryable
        self.cleanup_complete = cleanup_complete

def main(argv=None):
    args = build_parser().parse_args(argv)
    # Initialize without serving requests; clean up before classifying failures.
    # Only then raise StartupError for a verified pre-serving failure.
    # Once serving begins, never classify an error as retryable StartupError.
```

console 的 `worker_args` 可只传 Python、`service.py` 的路径以及可选 `--config`；不要把密钥放进命令行。继续使用 console 自己的进程日志和生命周期管理，不在这里增加第二套守护进程。

### 安全与验收

优先由部署环境安全注入 `GEMINI_CONSOLE_API_KEY`，或使用有访问控制的私人 JSON。环境中显式存在的空 key 不回退文件，而是报错；key 只接受无空白的可打印 ASCII。入口在本进程临时设置环境，退出后还原；它不是线程安全的嵌入式启动 API，应由独立进程调用。环境变量也不是抵御本机管理员读取的安全边界。不要分享 settings、账号、环境转储或日志。公共示例无可用密钥，不包含本机绝对路径。

`test_service.py` 仅用临时测试文件、fake server、mock socket/clock；不导入真实 backend、不认证、不生成、不重启服务。真实部署前仍须由维护者完成并核验上面的资源清理/异常契约；mock 通过不等于正式服务已修复或已上线。

## English

### Single configuration source and execution

This is an in-process **Cookie-only** entry point for `openai_compatible_server`, not a new generation client, browser fallback, daemon installer, or deployment action. It binds only IPv4 loopback (default `127.0.0.1:8787`). Use the repository's existing dependencies.

The sole default is `data/settings.json` relative to `service.py`. Existing console settings are read without importing console, generating credentials, or writing configuration. Never overwrite existing settings with the example. For a fresh installation, an operator can adapt `config.example.json` into private settings and provision `data/accounts`. The example contains no usable key.

Commands are shown above. `--check` validates JSON, paths, argument mapping and backend interfaces without opening sockets or starting the server. Importing the backend must itself be side-effect-free. `--config data/service.json` is explicit legacy compatibility, not another default or a merged overlay. Relative explicit config paths use the working directory; relative `account_dir` uses the config file's directory. Settings are a startup snapshot, not hot-reloaded.

For Windows Task Scheduler, point the task at the installed Python executable and pass the absolute entry-point path with `--log-file data/server.log`; never include keys in task arguments. No task is installed by this code. Logging is opt-in (no redirect by default), UTF-8, append-only and line-buffered for Python stdout/stderr. Relative log paths resolve against the project directory, independently of scheduler cwd. The parent directory must exist; opening failure prevents backend startup. The wrapper does not dump config or keys, restores streams on exit, and does not rotate logs. Native descriptor output and independently configured third-party log sinks are outside Python stream-redirection guarantees; backend sanitization remains necessary.

`model` maps to `--default-model`; legacy `default_model` also works, but conflicting aliases fail. Empty console `proxy` means null. `admin_key`, `browser_*`, the compatibility spelling `brower_*`, and `managed_model` are discarded and never forwarded. Known `transport` metadata is accepted, but **this executable always uses Cookie**, even if the shared settings say browser/managed. Console must dispatch only its Cookie worker here; browser and managed credentials/models are never used.

The table above is the full field/default mapping. Optional `queue_timeout` defaults to 2 seconds. `proxy_wait_seconds` aliases `proxy_wait_timeout`. `retry` accepts `attempts`, `initial_seconds`, `max_seconds` (defaults 3/1/8); equivalent legacy flat fields remain supported with conflict detection. Unknown non-metadata fields, duplicate JSON fields, non-finite numbers, invalid paths/names, boolean-as-number values and out-of-range settings fail closed. IPv6 listeners are intentionally rejected because the existing HTTP server uses IPv4.

### Secrets, proxy readiness and bounded retries

`GEMINI_CONSOLE_API_KEY` overrides `api_key` in JSON. An explicitly empty environment value fails instead of silently using the file. API keys never enter backend argv, subprocess command lines, or this wrapper's diagnostic messages. They are passed through a temporary process environment and restored on exit. Use a dedicated process, not concurrent embedded invocations. Local administrators can still inspect process environments. Backend logs must independently avoid printing secrets.

Explicit proxy URLs support HTTP/HTTPS/SOCKS5/SOCKS5H without embedded credentials, query strings or application paths. With waiting enabled, only IP literals or localhost are accepted to avoid unbounded system DNS resolution; set `proxy_wait_seconds=0` for a hostname proxy. Readiness is TCP-only, with per-connection timeout, polling interval and overall deadline. No HTTP, TLS, authentication or generation payload is sent. TCP success is not proof of upstream readiness. No explicit proxy means no preflight; existing per-account/environment proxy behavior remains with the backend.

Attempts include the first call. Backoff is bounded exponential; default failures wait 1 then 2 seconds, with at most three attempts. Each attempt has its own TCP wait budget. Total proxy waiting is bounded by attempts × wait timeout plus backoffs; backend initialization has its own timeouts and may scale with account count.

Only proxy-wait failure or an explicitly marked, fully cleaned-up **pre-serving** startup failure may retry. Ordinary runtime exceptions, permanent/authentication/configuration failures and any backend return code are not restarted. There is no request replay; a generation timeout or uncertain submission must be reconciled, never blindly retried. Ctrl+C interrupts waits/backoff and propagates through backend execution; the wrapper returns 130 for KeyboardInterrupt, or preserves the backend's normal shutdown result. Other exit codes are 0 success, 1 failure/exhaustion, 2 invalid config/interface; explicit backend return codes are preserved.

### Required backend contract and integration status

The backend maintainer must provide:

1. Side-effect-free `build_parser()` supporting the mapped switches, including `--queue-timeout`, and reading the API key environment default when the parser is built.
2. `main(argv=None)` using `parse_args(argv)`, plus `STRICT_PRO_MODEL` and `normalize_model_name(model, default_model)`.
3. An exported `StartupError(Exception)`. Retry requires all three instance attributes to be exactly true: `startup_only`, `cleanup_complete`, `retryable`. Never classify an error this way after any request could have been served.
4. Cleanup of clients, HTTP sockets, event-loop threads, signal handlers and global state across **all startup failures**, including partial initialization and bind errors. Set cleanup_complete only after cleanup actually succeeds. Never wrap KeyboardInterrupt or uncertain initialization as safely retryable. Authentication/configuration/eligibility errors and occupied ports should not be marked retryable.
5. Backend-side log sanitization; catching exceptions here cannot sanitize text the backend already printed.

The conceptual exception interface is shown in the Chinese section. With more than one attempt configured, missing StartupError support fails before server startup. One attempt permits an argv-capable backend without this exception class, but does not fix its cleanup implementation. Console's worker command should pass only the interpreter, entry-point path and optional config path; no key argument, no second default source, and no additional watchdog/request-retry layer.

The offline tests mock backend import, sockets, timers and environment and never contact Gemini or restart a service. Passing them proves wrapper behavior, **not** that the live service has been repaired or deployed. Complete the backend contract before production activation; no backend/private settings are modified by this deliverable.
