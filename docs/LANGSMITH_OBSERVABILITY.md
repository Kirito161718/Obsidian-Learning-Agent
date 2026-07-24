# LangSmith 全链路观测

## 定位与事实来源

阶段 7A 把 LangSmith 作为可选的外部 Trace、调试和后续评估平台。MySQL `agent_runs`、`tool_executions`、Approval Audit、Note Version、本地错误日志和 MySQL Checkpointer 仍是本地运行事实与审计来源。关闭、误配或无法访问 LangSmith 不会跳过这些本地记录，也不会改变 RAG、Tool、MCP、审批或写回语义。

`agent_runs` 通过 Alembic `20260722_0008` 增加四个可空字段：

```text
observability_provider
external_trace_id
observability_status
observability_error_code
```

每个 RAG、官方 Fetch/Audit/Apply 顶层 Trace 的 `metadata.internal_run_id` 对应 `agent_runs.id`。本地 Run 可以保存安全的 LangSmith Trace UUID；不会保存 API Key、Workspace ID、Endpoint、Trace URL 或 Trace 正文。

## 适配层

业务模块只依赖 `app.observability`，LangSmith SDK 的直接 import 被限制在适配层：

```text
configuration.py  配置解析、兼容开关和安全前置条件
manager.py        同步/异步 Trace、父子上下文、Fail-open、冷却和有限 Flush
context.py        ContextVar 与 SpanHandle
metadata.py       Query Hash、RAG/Node/LLM 安全统计
sanitization.py   Metadata、Tag、错误与大小限制
decorators.py     LangGraph RAG/官方节点包装
testing.py        无网络 FakeCollector
errors.py         稳定 Observability 错误码
```

关闭时 `ObservabilityManager.trace()` 和 `trace_async()` 返回 No-op Span。启用时使用官方高层 `trace` Context Manager 与 `tracing_context`，不由业务代码手工构造 `RunTree`。ContextVar 负责同步和异步父子关系；Graph 执行期间抑制框架自动 Trace，只保留项目显式、安全、稳定命名的 Span，防止整个 LangGraph State 成为输入输出。

## 启用、关闭与配置

默认关闭：

```env
LANGSMITH_TRACING=false
LANGSMITH_OBSERVABILITY_ENABLED=false
LANGSMITH_API_KEY=
LANGSMITH_PROJECT=obsidian-learning-agent
LANGSMITH_WORKSPACE_ID=
LANGSMITH_ENDPOINT=https://api.smith.langchain.com

LANGSMITH_SAFE_MODE=true
LANGSMITH_HIDE_INPUTS=true
LANGSMITH_HIDE_OUTPUTS=true
LANGSMITH_TRACE_SCHEMA_VERSION=ola-trace-v1
LANGSMITH_ENVIRONMENT=local
LANGSMITH_FLUSH_TIMEOUT_SECONDS=3
LANGSMITH_MAX_METADATA_CHARS=10000
LANGSMITH_MAX_ERROR_PREVIEW_CHARS=500
LANGSMITH_FAILURE_COOLDOWN_SECONDS=60
```

启用时同时设置 `LANGSMITH_TRACING=true` 和 `LANGSMITH_OBSERVABILITY_ENABLED=true`。为兼容已有本地配置，`LANGSMITH_OBSERVABILITY_ENABLED` 未设置时会沿用 `LANGSMITH_TRACING`；显式任一开关为 false 都关闭外部 Trace。Key 缺失、Project 缺失或 Safe Mode/Hide Inputs/Hide Outputs 不满足时状态为未配置，应用仍可启动。

`LANGSMITH_WORKSPACE_ID` 仅在一个 API Key 关联多个 Workspace 时需要。状态 API 只返回 `workspace_configured` 布尔值，不返回 ID。项目使用 `langsmith==0.10.5`，与当前 Python 3.13.9、`langgraph==1.2.9` 和 `langchain-core==1.4.9` 环境共同验证；没有为阶段 7A 升级其他框架依赖。

## 双层隐私保护

第一层在 Settings 首次加载时启用 SDK 全局 `LANGSMITH_HIDE_INPUTS=true` 和 `LANGSMITH_HIDE_OUTPUTS=true`，生产 Client 同时显式配置 `hide_inputs=True`、`hide_outputs=True` 和 `omit_traced_runtime_info=True`。

第二层 Sanitizer 独立处理 Metadata、Tags 和错误：

- 原 Query 只转换为 `query_hash`、`query_char_count`、`query_language` 和 `has_reference_expression`。
- Note 只允许内部 ID、路径 Hash、Heading Hash、Chunk ID、分数和数量统计。
- Memory 只允许开关、消息数、字符数、截断和是否用于 Rewrite。
- LLM 只允许 Provider、Model、真实 Usage Token、Finish Reason、耗时、重试和错误码。
- Tool/MCP 只允许名称、来源、Provider、安全风险、Caller、字符数、状态、耗时、执行 ID 和错误码。
- Snapshot、Proposal、Diff、Version、Reindex 只允许 ID、Hash、数量、状态和耗时。
- Metadata 总字符数、嵌套深度、列表长度、Tag 数量和单 Tag 长度均受限；超限标记 `metadata_truncated=true`。
- 业务异常只记录稳定错误码、异常类型和原始错误字符数；不发送异常正文或 Python Traceback。

禁止发送完整 Query、Answer、Prompt、System Instruction、Session Memory、Chunk、Markdown、Snapshot、Proposal、Diff、Tool 输入输出、JSON-RPC、Checkpoint State、绝对路径、数据库 URL、API Key、Token、Cookie、Authorization Header、邮箱或手机号。

## 稳定 Trace 名称与 Graph 映射

所有顶层 Trace 至少含：

```text
trace_schema_version
app_name
environment
flow_type
internal_run_id
status
```

可选关联字段为 `session_id`、`qa_history_id`、`proposal_id`、`approval_request_id`、`official_source_id`、`snapshot_id`、`tool_execution_id` 和 `checkpoint_thread_id`。常用 Tags 为 `ola`、Flow、Endpoint/操作类型、风险等级和稳定 Span 名。

RAG 顶层：

```text
ola.rag.ask
ola.rag.retrieve
```

真实节点映射：

| LangGraph 节点 | 稳定 Span |
| --- | --- |
| `initialize_state`、`load_user_preferences`、`load_explicit_learning_context` | `ola.rag.load_context` |
| `load_session_memory` | `ola.memory.session.load` |
| `retrieve_original_query` | `ola.rag.retrieve.original` |
| `assess_retrieval_quality` | `ola.rag.rewrite.decide` |
| `rewrite_query` | `ola.rag.rewrite.generate` |
| `retrieve_rewritten_query` | `ola.rag.retrieve.rewritten` |
| `merge_results` | `ola.rag.retrieve.merge` |
| `expand_links_placeholder` | `ola.rag.links.expand` |
| `rerank_results` | `ola.rag.rerank` |
| `build_context` | `ola.rag.context.build` |
| `DeepSeekClient.generate_answer` | `ola.llm.deepseek.answer`，`run_type=llm` |
| `validate_citations` | `ola.rag.citations.validate` |
| `basic_evaluate` | `ola.rag.basic_evaluate` |
| `save_qa_history` | `ola.rag.persist` |

Tool 与 MCP：

```text
ola.tool.execute
ola.mcp.connect
ola.mcp.discover
ola.mcp.call
ola.mcp.disconnect
```

Tool Span 在本地 Audit 创建后开始并关联 `tool_execution_id`。MCP Discover 嵌套在 Connect；MCP Call 嵌套在 Tool Span，或在手动直接调用时成为独立 Trace。原 Tool Policy、`caller=user`、READ_ONLY/LOW_RISK/HIGH_RISK 和 Audit 语义不变。

官方资料：

```text
ola.official.fetch
ola.official.audit
ola.official.snapshot.compare
ola.official.proposal.build
ola.official.approval.interrupt
ola.official.approval.resume
ola.official.apply
ola.official.version.create
ola.official.reindex
```

Audit 与 Resume/Apply 可以是两个顶层 Trace，通过 `proposal_id`、`approval_request_id` 和 `checkpoint_thread_id` 关联。LangGraph Interrupt 记录为 pending，不要求跨进程保持一个根 Trace，也不发送 Checkpoint State。

## 失败隔离与关闭

Client 初始化、DNS、连接、认证、429、5xx、Workspace、后台上报和 Flush 错误都转换为稳定 Observability 错误码。错误只改变观测状态并增加失败计数，不覆盖业务错误、不回滚业务事务、不触发第二次 DeepSeek 调用、不重复执行 Tool、不重复写笔记。失败后使用短冷却避免持续创建失败请求；没有持久化队列或无限重试。

适配层会丢弃 `langsmith` SDK 自带的原始诊断日志，避免其输出 Endpoint、遮罩 Key 前后缀、Trace ID 或请求大小；应用日志只保留 `observability_*` 稳定错误码。

FastAPI Shutdown 在 MCP 断开后调用有限 Flush。超过 `LANGSMITH_FLUSH_TIMEOUT_SECONDS` 即继续退出，Flush 工作线程为 daemon，不阻塞进程结束。

## 状态 API 与定位运行

```http
GET /agent/observability/status
```

安全返回 Provider、Enabled、Configured、Safe Mode、Project、Workspace 是否配置、Schema Version、最近状态、最近稳定错误码、最近提交时间和失败计数。`/agent` 页面还显示每个 Run 的 Provider、只读 Trace ID 和 Submission Status。

在 LangSmith 对应 Project 的 Traces/Runs 页面，用 `metadata.internal_run_id=<agent_runs.id>` 过滤即可定位本地运行；也可以复制 Agent 页面显示的 Trace UUID 搜索。文档不保存或公开真实私有 Trace 链接。

## 当前范围

阶段 7A 建立生产观测基线。阶段 7B 在不改变上述 Safe Trace 契约的前提下增加固定本地数据集、离线 Retrieval/Answer Evaluation 和 LangSmith 安全子集。只有明确标记 `share_with_langsmith=true` 且已审阅、未 stale 的 Case 会同步；本地数据集与本地报告仍是事实来源。发布失败不会重跑 RAG Case。

阶段 7C/7D 的候选初筛与最终三轮复测不扩大上传范围。最终 Baseline、Chunk V2、Link V2 的 Retrieval-only 与 Full RAG 实验都使用 `--no-langsmith`，外部观测限流或失败不影响本地逐 Case 持久化、精确恢复和最终决策。完整说明见 [`RAG_EVALUATION.md`](RAG_EVALUATION.md)，最终决策见 [`RAG_OPTIMIZATION_DECISION.md`](RAG_OPTIMIZATION_DECISION.md)。

官方参考：

- [Tracing quickstart](https://docs.langchain.com/langsmith/observability-quickstart)
- [Custom instrumentation](https://docs.langchain.com/langsmith/annotate-code)
- [Prevent logging sensitive data](https://docs.langchain.com/langsmith/mask-inputs-outputs)
- [Conditional tracing](https://docs.langchain.com/langsmith/conditional-tracing)
- [LLM metadata parameters](https://docs.langchain.com/langsmith/ls-metadata-parameters)
