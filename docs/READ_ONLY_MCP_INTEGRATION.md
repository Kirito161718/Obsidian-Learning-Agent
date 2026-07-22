# 只读 MCP 端到端接入

## 定位与架构

阶段 5 在已有工具系统外增加协议适配层，不建立第二套策略、执行器或审计：

```text
Agent 控制台 / 用户手动 API
  -> 现有 Tool Registry
  -> 现有 ToolPolicy
  -> 现有 ToolExecutor
  -> MCPToolAdapter
  -> MCPClientManager / 官方 ClientSession
  -> stdio（本阶段真实验收）或 Streamable HTTP Client
  -> 项目内只读 MCP Server
  -> 现有内部 Tool / Service
  -> MySQL / Qdrant
  -> 现有 Tool Audit
```

复用 `ToolExecutor` 的原因是让内部工具与 MCP 工具共享同一套 Caller、风险、输入输出校验、Timeout、错误映射和审计语义。MCP Tool 没有直连执行 API，仍使用：

```http
POST /agent/tools/{namespaced_tool_name}/execute
```

## 官方 SDK 与传输

核心协议使用官方 [Model Context Protocol Python SDK](https://github.com/modelcontextprotocol/python-sdk) 的 `mcp==1.28.1`，不使用第三方替代实现。该固定版本已在 Python 3.13 上验证。SDK 1.28.1 需要 Pydantic 2.11 以上，因此项目只把 Pydantic 固定更新到 `2.11.10`，没有升级 FastAPI、SQLAlchemy、LangGraph 或 Qdrant Client。

stdio 流程为：Client Manager 启动独立子进程，官方 `ClientSession` 完成 Initialize 握手，读取 Capabilities，再分页调用 `list_tools`。同一 Server 的 Session 和 SDK 上下文由专用异步任务持有，避免跨请求任务退出上下文；并发 Connect 使用单 Server Lock，因此不会重复启动进程。Disconnect 和 FastAPI Shutdown 会关闭 Session、stdin/stdout 与子进程，并在有限等待后取消清理。

stdout 只承载 MCP JSON-RPC。Server 普通错误日志写 stderr。Windows 下 SDK 要求 stderr 具有真实文件句柄，因此客户端仅在项目 `runtime/temp` 使用自动删除的临时句柄；页面和审计只允许读取脱敏、截断预览，不保存原始流。

当配置的 Embedding 指向已存在的本地模型且不是测试环境时，用户手动 Connect 启动 Server 后会在 stdio 事件循环开始前预热该模型，避免 Windows 下首次 Retrieve 的模型加载阻塞协议响应。预热不下载远程模型；失败只写稳定 stderr 错误并继续握手。Qdrant、数据库和 Reranker 不在握手阶段初始化，因此它们未就绪时不会让 Server 无法连接。

Streamable HTTP Client 已预留并使用官方 `streamable_http_client`。远端默认必须为 HTTPS；只有 localhost 可用 HTTP；Host 必须在本地 `allowed_hosts`；认证值仅从 `auth_env_var` 指定的环境变量读取；HTTP Client 禁止自动重定向，防止跳转到未允许 Host。本阶段不连接未知公网 Server。

## 项目内只读 Server

启动命令：

```powershell
python -m app.mcp.server
```

Server 使用官方低层 `Server`，只注册 Tools Handler，避免高级封装默认声明空 Resources 或 Prompts 能力。它只暴露：

```text
list_learning_goals
list_learning_status
search_obsidian_notes
get_session_messages
get_user_preferences
retrieve_knowledge
```

Server 不注册 Resources、Prompts、Sampling、Roots 或 Elicitation。六个 Handler 直接调用 `app.tools.internal.tools` 中现有只读能力：Goal、学习状态、Session 和偏好继续复用已有数据库语义；Obsidian 搜索继续使用 Vault 相对路径校验；`retrieve_knowledge` 继续复用 RAG Retrieve，不生成答案、不写 `qa_history`。Server 不接受任意 SQL、路径、文件、Shell、HTTP 或 Git 操作。

## 可信配置与默认拒绝

可提交示例为 `config/mcp_servers.example.yaml`；真实本机文件为 `config/mcp_servers.local.yaml`，已由 Git 忽略。前者不含机器绝对路径，后者可引用项目内虚拟环境。配置不能直接保存 Token、密码、API Key 或带凭据 URL，只能用环境变量名称引用认证值。

应用启动只加载并校验配置，不自动连接任何 Server。配置缺失或非法不会使主应用或 `/health` 失败。`MCP_ENABLED=false` 时核心服务和内部工具保持可用，MCP API 返回明确禁用状态。

API 和页面不能创建 Server，也不能修改 command、args、URL、Host 或 Allowlist。stdio 不使用 `shell=True`，command 与 args 分开传递；`cwd` 和显式命令路径经 `pathlib` 解析后必须仍在项目根目录内；子进程只继承基础运行环境和 `environment_allowlist` 明确列出的变量。

`allowed_tools` 默认空，即拒绝全部。远端 Tool 只有同时满足以下条件才注册：Server 启用且已连接、Tool 真实发现、名称位于本地 Allowlist、Schema 安全且未超限、Server Tool 数未超限。

## Tool Catalog 与安全边界

Catalog 保存 Server、远端名称、描述、输入 Schema、是否允许、连接状态和稳定 Schema Hash。注册名固定为：

```text
mcp.<server_name>.<remote_tool_name>
```

远端名称、描述、Annotations 和 Schema 都是不可信元数据。描述和 Schema 文本元数据会脱敏、限长；Schema 限制总字符数和嵌套深度，只允许存在目标的本地 `$defs` 引用，并拒绝递归或外部引用。刷新会重新计算 Hash，但不会扩大本地 Allowlist。

所有阶段 5 MCP Tool 的本地风险固定为 `READ_ONLY`。风险不读取 Server Annotation、Description、请求、模型或页面。MCP 的 LOW_RISK 和 HIGH_RISK 定义都会被现有 `ToolPolicy` 拒绝，HIGH_RISK Callable 不执行。

阶段 6 虽然在内部 Registry 增加官方来源/Proposal 的只读查询工具，并登记一个默认拒绝的 `apply_update_proposal` HIGH_RISK 定义，但项目内 MCP Server 的显式导出清单仍固定为上面的六个只读工具。官方更新只能由 `/official` 的一次性 Approval 与持久化 LangGraph Resume 执行，不会被 MCP Discover、Tool Call 或模型调用。

MCP Definition 只允许 `caller=user`；`model` 和 `system` 均被策略拒绝。执行 API 在服务端固定 Caller，输入中的 `caller_type` 或 `risk_level` 不能覆盖策略。当前 LangGraph 不发现、不选择也不执行 MCP Tool，MCP 输出不会自动进入 Prompt。

输入先受总字符限制，再按发现的 JSON Schema 验证；失败时不会调用 Server。输出只保留结构化结果或安全文本块，不保留完整协议包；经过递归脱敏后按字符上限安全截断并标记。稳定错误码以 `mcp_` 开头，前端不返回 Python Traceback。

## Audit

`tool_executions` 增加：

```text
tool_source          internal | mcp
provider_name        MCP Server 名称
remote_tool_name     原始 Tool 名称
protocol_request_id  安全截断的本地协议关联 ID
```

内部工具继续记录 `tool_source=internal`。MCP 成功、失败、Caller 拒绝和输入拒绝继续经过现有 Tool Audit；输入输出沿用敏感键、用户主目录和绝对路径脱敏，并受摘要上限约束。数据库不保存 Token、Authorization Header、完整 JSON-RPC、stdout 原始流、完整 Chunk、Prompt 或 LangGraph State。

## API 与控制台

```text
GET  /agent/mcp/servers
GET  /agent/mcp/servers/{server_name}
POST /agent/mcp/servers/{server_name}/connect
POST /agent/mcp/servers/{server_name}/disconnect
POST /agent/mcp/servers/{server_name}/refresh-tools
GET  /agent/mcp/tools
```

在 `/agent` 的 MCP Servers 区手动连接配置内 Server，再在 MCP Tools 区选择已允许且已注册的 READ_ONLY Tool 输入 JSON 测试。Tool Audit 会显示 internal/mcp、Provider 和 Remote Tool。页面不显示 Token、完整环境、项目或 Vault 绝对路径，也不允许编辑命令或 URL。

本地生命周期建议：

```text
Connect -> 检查六个 Tool -> 手动执行 -> Refresh Tools -> Disconnect
```

## 后续 GitHub MCP 接入边界

后续接入官方 GitHub MCP Server 时，应先把固定启动方式或 HTTPS Endpoint 写入可信本地配置，固定 Host 与环境变量名，并以空 Allowlist 起步；人工审查实际发现的 Tool Schema 后，只加入明确只读的查询 Tool。GitHub Token 只从环境读取，不进入 YAML、日志、页面或审计。当前 MCP 边界不允许 GitHub 写操作、任意仓库命令、模型自主调用或 HIGH_RISK 执行；阶段 6 的服务内审批不会放宽该边界。
