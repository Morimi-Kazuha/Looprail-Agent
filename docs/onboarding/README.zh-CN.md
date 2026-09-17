# Looprail 首次使用

这份指南帮助你在选定的 Git 仓库里安装 Looprail、配置 Provider，并完成第一条
真实任务。Looprail 会把工作区作为一个明确的 Workspace；开始前请确认路径和权限。

## 准备环境

Looprail 目前需要 Python 3.12。原生 TUI 使用 Node.js 22，安装器会在系统缺少合适版本
时准备一个用户目录下的私有 Node Runtime。

从项目托管平台取得源码检出后，在仓库根目录运行对应安装器：

```bash
./install.sh
```

Windows PowerShell：

```powershell
.\install.ps1
```

如果你只需要源码开发环境，也可以直接执行：

```bash
uv sync --frozen --extra dev --dev
uv run --frozen looprail --version
```

本页不硬编码尚未确定的最终公开仓库地址，也不建议从未知来源下载 wheel。

## 在目标仓库中启动向导

先进入 Looprail 实际要工作的 Git 仓库：

```bash
cd "<workspace>"
uv run --frozen looprail onboard --skip-memory
```

当没有安装外部 Memory 实现时，`--skip-memory` 是当前受支持的配置。它只关闭外部
长期记忆，不会关闭 Local Skills、Session、Context 或 Tool。向导仍会配置 Provider、
运行位置和可选消息渠道。

向导大致经过以下四步：

```mermaid
flowchart LR
    A[连接 Provider] --> B[配置 Memory 与第一条 Turn]
    B --> C[选择运行位置]
    C --> D[配置可选消息渠道]
```

第一条真实 Turn 可能产生 Provider 费用。使用 `--skip-test` 可以跳过这次调用，但这
只能证明配置已经写入，不能证明模型连接成功。

## 非交互配置

自动化场景可以使用已获得明确授权的参数：

```bash
uv run --frozen looprail onboard \
  --non-interactive \
  --provider openai \
  --api-key "$OPENAI_API_KEY" \
  --skip-memory \
  --skip-channel \
  --yes
```

命令行参数可能出现在 Shell 历史或本机进程列表中。默认优先使用交互式向导输入密钥，
不要自动添加 `--reset`；已有 Provider、渠道、Sandbox 和 Workspace 选择属于用户。

## 只读验证

在目标仓库中运行：

```bash
uv run --frozen looprail --version
uv run --frozen looprail plugins
uv run --frozen looprail channels list
uv run --frozen looprail doctor --json
```

检查结果应满足：

- `looprail --version` 返回已安装版本；
- `doctor --json` 输出合法 JSON；
- Memory 明确处于关闭状态，或指向已安装且可用的实现；
- 输出中没有 API Key、App Secret 或 Token。

允许一次真实 Provider 调用时，可以运行：

```bash
uv run --frozen looprail run -m "用三句话说明这个仓库做什么"
```

只有收到模型回复，才能把 Provider 标记为已验证。静态诊断或跳过测试不等同于真实
模型结果。

## 常用启动方式

```bash
uv run --frozen looprail
uv run --frozen looprail run -m "总结当前仓库"
uv run --frozen looprail gateway --workspace "$PWD" --verbose
```

- `looprail` 启动原生 TUI；
- `looprail run` 执行一次 CLI Turn；
- `looprail gateway` 为当前 Workspace 服务已启用的消息渠道。

## 继续阅读

- [飞书机器人](feishu.zh-CN.md)
- [Memory 配置边界](memory.zh-CN.md)
- [故障排查](troubleshooting.md)
- [给自动化 Agent 的安装契约](agent-install.md)
