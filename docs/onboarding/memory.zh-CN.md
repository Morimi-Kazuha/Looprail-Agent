# Memory 配置边界

Looprail 保留 Memory Backend 协议和可选适配接口，但当前源码检出不包含可直接安装的
外部 Memory 实现。不要根据源码中的 Adapter 名称猜测下载地址或制品来源。

## 当前受支持的配置

首次配置时显式关闭外部 Memory：

```bash
uv run --frozen looprail onboard --skip-memory
```

有效配置是 `memory.backend = null`。这只关闭外部长期记忆，不会关闭 Local Skills、
Session、Context、Tool 或其他 Looprail Runtime 能力。

向导仍会通过完整 Runtime 执行第一条 Turn。Memory 关闭不应阻止 Provider、工具或
Session 正常工作。

## 为什么必须显式关闭

Looprail 不会把缺失的 Memory Plugin 当作可用状态。如果配置选择了未安装的 Backend，
启动与诊断会 fail closed，避免用户误以为 Recall 已经生效。

检查当前状态：

```bash
uv run --frozen looprail plugins
uv run --frozen looprail doctor --json
```

如果已有配置指向未安装的 Backend，重新进入向导并明确关闭：

```bash
uv run --frozen looprail onboard --skip-memory --reset
```

执行 `--reset` 前先确认现有 Provider、Sandbox 和渠道配置可以被重新选择。

## 外部 Memory 的信任边界

源码中的 Adapter 名称、Plugin identity 校验和兼容测试，只能证明 Looprail 预留了集成
边界，不能证明用户已经获得可安装制品。外部 Memory 发布前：

- 不要根据源码名称猜测仓库、包名或下载地址；
- 不要用开发 Checkout 或未固定版本的包替代正式制品；
- 不要把 Plugin 被发现写成 Backend 已经健康；
- 不要把静态测试写成真实 Recall 或任务效果。

未来接入外部 Memory 时，验证应分别覆盖 Plugin 发现、Backend 启动、仓库绑定、写入、
Recall 和真实 Turn 注入。任一层没有证据，都应标记为未验证。
