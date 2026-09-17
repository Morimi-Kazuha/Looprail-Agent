# Onboarding 故障排查

## `configured memory plugin is unavailable`

Looprail 当前配置选择了一个未安装的外部 Memory Backend。

先检查 Plugin 和配置状态：

```bash
uv run --frozen looprail plugins
uv run --frozen looprail doctor --json
```

如果你不需要外部 Memory，重新进入向导并显式关闭：

```bash
uv run --frozen looprail onboard --skip-memory --reset
```

执行 `--reset` 前先记录现有 Provider、Sandbox 和渠道选择。

## 安装器无法完成安装

确认 Python 3.12、`uv` 和 Node.js 22 可用，并从可信的 Looprail 源码检出根目录运行
对应安装器：

```bash
./install.sh
```

Windows PowerShell：

```powershell
.\install.ps1
```

如果维护者提供固定 wheel，可以设置 `LOOPRAIL_WHEEL_URL`。不要把 Token 写入远程
URL、仓库文件、命令截图或日志，也不要从未知地址下载制品。

## Provider 预检通过，但第一条 Turn 失败

`GET /v1/models` 成功只证明凭证和网络的一部分。真实 Turn 还会检查模型 ID、账户额度、
Runtime 和当前工具环境。

```bash
uv run --frozen looprail provider test <provider-name>
uv run --frozen looprail doctor --probe
```

检查默认模型是否属于当前 Provider，以及代理或 VPN 是否能访问对应 Endpoint。`--skip-test`
只跳过向导中的测试调用，不应被记录为 Provider 已验证。

## `looprail` 无法打开原生 TUI

先检查版本和打包后的 TUI：

```bash
uv run --frozen looprail --version
uv run --frozen looprail --check
```

原生 TUI 需要 Node.js 22。开发检出中可以重新安装依赖并构建：

```bash
npm ci --prefix ui-tui
npm run build --prefix ui-tui
```

然后再次运行 `looprail --check`。

## 飞书收不到消息

按顺序检查：

1. 应用是企业自建应用，并且已经启用机器人能力。
2. 事件模式是 WebSocket 长连接，并已添加 `im.message.receive_v1`。
3. 包含新权限和新事件的应用版本已经发布。
4. `looprail channels get feishu` 显示已启用，App ID 正确，密钥已设置。
5. `looprail gateway --workspace <workspace> --verbose` 仍在运行。
6. 群聊中已经 @ 机器人；默认 `group-policy=mention`。
7. `allow-from` 包含当前用户 `open_id`，或仅在受控调试期间使用 `['*']`。

配置保存成功不等于真实收发成功。最终验证需要真实用户发送入站消息，并在同一会话
中收到 Looprail 回复。

## Gateway 正在运行，但操作了错误仓库

一个 Gateway 进程只服务一个固定 Workspace。停止旧进程，再显式指定目标路径重启：

```bash
uv run --frozen looprail gateway --workspace "<workspace>" --verbose
```

不要根据启动终端当前目录推测 Gateway 的 Workspace，以启动参数和日志为准。

## 向导检测到已有配置

默认情况下，Looprail 会保护已有配置。自动化场景可以使用 `--yes` 复用；只有明确
需要重做配置时才使用 `--reset`：

```bash
uv run --frozen looprail onboard --skip-memory --yes
uv run --frozen looprail onboard --skip-memory --reset
```

如果不确定现有配置属于谁，先运行 `looprail doctor --json` 和
`looprail channels list`，不要直接重置。
