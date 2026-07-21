# 记忆分层、上下文工程、工具注册与运行审计基础

## 五类状态与记忆

阶段 4 将数据边界固定为五层：

- **Session State**：单次 LangGraph 执行中的可序列化状态。它包含精简偏好、Session Memory、检索结果和统计字段，但不包含 SQLAlchemy Session、ORM、Tool 实例、完整 Prompt，也不长期保存完整 State。
- **Session Memory**：当前 Learning Session 最近若干轮问答，只用于代词消歧、连续对话和避免重复解释。
- **User Memory**：单用户通过 `/agent/preferences` 手动维护的回答偏好。聊天、RAG 和模型输出没有写入入口。
- **Knowledge Memory**：上传文档、只读 Obsidian Vault、Qdrant Chunk 与 MySQL 元数据。它是回答事实与 `[来源N]` 的唯一证据层。
- **Run History**：Ask、Retrieve、Session Review 和工具执行的紧凑运行指标与脱敏错误记录。

本阶段没有 Lessons Memory，也不会从日志、聊天或 Review 自动生成长期经验。自动 Reflection 与长期记忆提取需要单独的质量、隐私和撤销机制，不应被运行日志暗中替代。

## 用户偏好

`user_preferences` 只保存固定 ID `default`。数据库无记录时返回以下默认值，但 GET 不创建记录：

```text
preferred_language = zh-CN
answer_depth = standard
answer_style = structured
citation_mode = required
custom_instructions = null
```

只有显式 `PATCH /agent/preferences` 会首次创建或更新这一行。自定义说明默认最多 1000 字符，只控制回答语言、解释深度和结构；Prompt 明确规定它不能覆盖系统安全规则、No Answer、引用规则、工具策略或只读边界。`required` 也不会导致模型在无可靠知识上下文时伪造引用。

## Session Memory

Session Memory 由 `SessionMemoryService` 从当前 `session_id` 的 `qa_history` 构造：

- 只读取当前 Session，数据库倒序取最近记录后恢复为从旧到新。
- 默认最多 6 轮；每轮只包含问题、最多 500 字符的回答预览和无答案标记。
- 默认总长度不超过 3000 字符；超限先移除最早轮次，最后一轮仍超限时再截断回答预览。
- 不包含 Source Chunk、检索 Context、Prompt、Feedback、绝对路径或其他 Session 内容。
- 全局 `SESSION_MEMORY_ENABLED=false` 或 Session 的 `memory_enabled=false` 任一成立时不加载。
- 当前问题在回答保存前加载 Memory，因此不会作为自己的历史轮次进入 Prompt。

Rewrite 仍然先执行原始查询检索，再评估召回质量。只有触发 Rewrite 时，历史才可帮助把“它和 Rerank 有什么区别”改成独立查询；原始召回不会被替换，Rewrite 失败仍使用原始结果。历史不会变成向量检索结果，也不会产生 `[来源N]`。

## Context Bundle 与预算

`ContextBundle` 由以下分区组成：

```text
用户手动偏好
显式学习目标与状态
最近 Session Memory（不可信、不可引用）
可引用知识库上下文（唯一事实证据）
Context Manifest
```

Answer Prompt 另行固定系统安全规则、当前问题和输出要求。动态 Context 默认总预算 18000 字符。预算不足时按以下优先级保留：

```text
当前问题与系统安全规则
知识库上下文
显式学习上下文
用户偏好
最近 Session Memory
```

因此最先从旧轮次裁剪 Session Memory，不会为了保留聊天历史大量牺牲知识来源。Manifest 只保存每区是否使用、字符数、轮数或来源数、截断标记、总字符数和预算，不保存正文。

## Run History

`agent_runs` 支持：

```text
rag_ask
rag_retrieve
session_review
official_fetch
official_audit
official_apply
```

入口在执行前创建 `running` Run，并把其 ID 放入 Graph State。成功后记录状态、Rewrite、召回与来源数、模型/工具/重试次数、Context Manifest 和耗时；失败后记录稳定错误代码、脱敏且截断的错误预览和耗时。

Ask 保存 `qa_history.run_id`；Retrieve 创建 Run 但不生成答案、不写 QA；主动 Session Review 单独创建 Run，不保存 Review Prompt。运行表只保存 SHA-256、最多 200 字符的问题预览和最多 500 字符的错误预览，不保存完整回答、Prompt、知识上下文、Session Memory 或 LangGraph State。

普通 Run 与 Tool Audit 默认保留 10 天，但阶段 4 不增加启动清理、后台任务或定时器。只能显式调用：

```http
POST /agent/maintenance/cleanup-history
```

默认 Dry Run。Apply 会先把关联的 `qa_history.run_id` 置空，再删除过期 Run 与普通 Tool Audit；不会删除 QA、Session、Review、目标、学习状态、Obsidian 元数据或 Qdrant Point。

## Tool Registry、风险与执行策略

代码注册表为每个 Tool 固定名称、描述、风险等级、输入输出 Schema、启用状态、幂等性、Timeout 和允许 Caller。请求、数据库和模型都不能覆盖风险等级、启用状态或 Caller 策略；注册表也不按字符串动态 import 任意函数。

风险等级：

- **READ_ONLY**：仅在已注册、已启用、Caller 合法且输入 Schema 和作用域校验通过时执行。
- **LOW_RISK**：枚举和策略框架已存在，但阶段 4 没有启用任何写工具，执行请求会被拒绝。
- **HIGH_RISK**：普通 `/agent/tools/{tool_name}/execute` 仍统一返回 `approval_required`，Callable 不执行。阶段 6 仅为官方更新提供独立的持久化 Approval + LangGraph Interrupt/Resume 服务；它绑定 Proposal payload hash、要求整批确认并一次性消费，不能被通用 Tool API 或模型绕过。

只读内部工具：

```text
list_learning_goals
list_learning_status
search_obsidian_notes
get_session_messages
get_user_preferences
retrieve_knowledge
list_official_sources
get_update_proposal
preview_update_diff
```

前三个阶段 6 工具仍为 READ_ONLY，只读取来源、Proposal 元数据和安全 diff。`apply_update_proposal` 作为代码注册表中的 HIGH_RISK 定义用于验证默认拒绝策略，不通过通用执行 API、MCP 或模型直接执行；真正 Apply 只能在 `/official` 的审批服务校验后发生。

`retrieve_knowledge` 复用现有 RAG Retrieve，不生成回答、不写 `qa_history`，也不修改 Session、目标或学习状态。单次 Tool 不无限重试，支持同步/异步 Callable 和 Timeout；异常会转换为稳定错误，不向前端返回 Python Traceback。

## 工具审计与脱敏

每次工具执行或拒绝都写入 `tool_executions`。审计器递归识别以下敏感字段并替换为 `***`：

```text
password, passwd, api_key, token, secret, authorization,
cookie, database_url, dsn
```

此外会移除 Windows 用户主目录与绝对路径，对输入输出摘要截断，并计算稳定输入 Hash。审计不保存完整 Chunk 列表、完整 QA、完整 Markdown、Token、Authorization Header、数据库 URL 或 Vault 绝对路径。工具返回值被视为外部不可信数据；阶段 4 不会自动把它交给模型。

## API 与页面

Agent API 统一使用 `/agent`：

```text
GET/PATCH /agent/preferences
POST      /agent/context/preview
GET       /agent/runs
GET       /agent/runs/{run_id}
GET       /agent/tools
GET       /agent/tools/{tool_name}
POST      /agent/tools/{tool_name}/execute
GET       /agent/tool-executions
GET       /agent/tool-executions/{execution_id}
POST      /agent/maintenance/cleanup-history
```

`GET /agent` 提供 Jinja2 + 原生 JavaScript 的 Agent 控制台。页面可显式保存偏好、预览 Context 统计、查看 Run 与 Tool Audit、手动测试只读 Tool，并要求先 Dry Run 后确认清理。`/admin` 和 `/learning` 都提供入口。

阶段 5 已在本基础之上增加只读 MCP Server/Client。允许的 MCP Tool 仍复用本章的 Registry、Policy、Executor、Audit 和脱敏语义；审计额外区分 `tool_source=internal|mcp`、Provider、Remote Tool 与安全协议关联 ID。阶段 6 的 Human-in-the-loop 仅用于官方更新审批，不改变 MCP 只读边界。完整说明见 [`READ_ONLY_MCP_INTEGRATION.md`](READ_ONLY_MCP_INTEGRATION.md) 与 [`OFFICIAL_SOURCE_AUDIT_AND_APPROVAL.md`](OFFICIAL_SOURCE_AUDIT_AND_APPROVAL.md)。当前仍没有模型自主 Function Calling 或 Planner/Executor 多智能体。
