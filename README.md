# Obsidian Learning Agent

本仓库正在由企业文档 RAG 知识库演进为 Obsidian Learning Agent。
当前原有文档上传和 RAG 功能继续保留。

阶段 3 已增加独立的本地学习工作台：访问 `http://127.0.0.1:8000/learning`，可把学习 Session、显式目标与学习状态、上传文档与 Obsidian 来源、问答历史及用户主动生成的复盘组合为连续学习闭环。完整说明见 [`docs/LOCAL_LEARNING_WORKSPACE.md`](docs/LOCAL_LEARNING_WORKSPACE.md)。

阶段 4 已增加 Session Memory、用户手动偏好、统一 Context Bundle、RAG/Review Run History、只读 Tool Registry 与脱敏工具审计。阶段 5 在同一 Registry、Policy、Executor 与 Audit 之上增加官方 MCP SDK 的只读 Server/Client、stdio 真实传输和 Streamable HTTP Client 预留。访问 `http://127.0.0.1:8000/agent` 可使用 Agent 控制台；完整边界见 [`docs/MEMORY_CONTEXT_TOOL_FOUNDATION.md`](docs/MEMORY_CONTEXT_TOOL_FOUNDATION.md) 和 [`docs/READ_ONLY_MCP_INTEGRATION.md`](docs/READ_ONLY_MCP_INTEGRATION.md)。当前仍没有模型自主工具调用、MCP 写工具、自动 Lessons Memory 或后台清理。

阶段 6 增加用户手动登记的公开官方来源、受限抓取与 Snapshot、Source-Note Link、结构化 Evidence Proposal、MySQL 持久化 LangGraph 审批、写前 Hash stale 校验、Note Version、整批原子写入/回滚和单笔记 Reindex。访问 `http://127.0.0.1:8000/official` 管理；完整安全边界见 [`docs/OFFICIAL_SOURCE_AUDIT_AND_APPROVAL.md`](docs/OFFICIAL_SOURCE_AUDIT_AND_APPROVAL.md)。该能力不自动抓取、不支持部分批准，也不通过 MCP 暴露写工具。

## 1. 项目介绍

这是一个基于 FastAPI 的企业文档 RAG 知识库问答系统 MVP，支持上传 PDF、DOCX、TXT、CSV 文档，解析后切分为 chunk，使用本地 BAAI/bge-m3 生成 embedding，写入 Qdrant 向量库，并通过 DeepSeek API 基于检索结果生成带引用来源的答案。

后续演进以 [`docs/OBSIDIAN_LEARNING_AGENT_REUSE.md`](docs/OBSIDIAN_LEARNING_AGENT_REUSE.md) 为准；旧 RAG 项目的接口与迁移背景仍可参阅 [`docs/RAG_PROJECT_HANDOFF.md`](docs/RAG_PROJECT_HANDOFF.md)。

第一阶段目标链路：上传文档 -> 解析 chunk -> 写入 Qdrant -> 提问 -> 检索 -> DeepSeek 生成答案 -> 返回引用来源。

## 2. 技术栈

- FastAPI：后端 API
- SQLAlchemy：ORM
- MySQL：文档元数据、问答历史、学习目标与学习状态
- Qdrant：向量数据库
- Docling：PDF、DOCX 文档解析
- sentence-transformers：加载 BAAI/bge-m3 本地 embedding 模型
- OpenAI SDK：以兼容方式调用 DeepSeek API
- LangGraph：编排 RAG 状态、节点与条件路由
- Docker Compose：启动 API 与 Qdrant
- pytest：基础接口测试
- 官方 MCP Python SDK：项目内只读 Server、stdio Client 与 Streamable HTTP Client

## 3. 系统架构

```text
Client
  -> FastAPI Routers
  -> RagService compatibility facade
  -> LangGraph RAG workflow
     -> conditional query rewrite and dual retrieval
     -> context building and citation validation
  -> Services
     -> ParserService: Docling / plain text parsing
     -> ChunkService: text splitting
     -> EmbeddingService: BAAI/bge-m3 vectors
     -> VectorService: Qdrant upsert/search
     -> RerankService: optional BGE reranking with fallback
     -> DeepSeekClient: OpenAI-compatible LLM call
  -> MySQL: documents / qa_history / feedback / learning_goals / learning_status
  -> Qdrant: chunk vectors and payload
```

## 4. RAG 流程说明

1. 用户通过 `/documents/upload` 上传文件。
2. 系统校验后缀并保存到 `uploads/`。
3. PDF、DOCX 使用 Docling 解析，TXT、CSV 使用纯文本读取。
4. 文档内容按固定窗口切分为 chunk。
5. 使用 `BAAI/bge-m3` 为每个 chunk 生成 embedding。
6. chunk 向量和 payload 写入 Qdrant。
7. 用户通过 `/rag/ask` 或 `/rag/retrieve` 提交问题，LangGraph 先使用原始问题检索。
8. 工作流以结果数量、最高相似度、指代词和问题长度评估召回质量；仅在需要时调用 DeepSeek 改写查询。
9. 发生改写时合并原始查询与改写查询的双路召回，按 `chunk_id` 去重后统一进入可配置的 BGE Rerank。
10. Context Builder 限制来源数量和上下文长度，并分配稳定的 `[来源N]` 编号。
11. `/rag/retrieve` 到此返回且不生成答案、不写历史；`/rag/ask` 继续检查可回答性、生成答案并验证引用。
12. `/rag/ask` 返回 answer 和 sources，并向 `qa_history` 写入最多 300 字符的 Chunk 预览。

## 5. 学习目标、学习状态与重要指数

本项目是单用户系统，通过 `/goals` 管理求职、学习、项目和技能目标，通过 `/learning-status` 手动维护主题的学习状态、掌握等级、手动重要度、当前问题、关联目标和预留笔记引用。两个模块使用普通 FastAPI CRUD，不经过 LangGraph，也不会自动写入 RAG Prompt。

重要指数在读取学习状态时实时计算，不写入数据库：

```text
Importance = 0.45G + 0.30D + 0.15M + 0.10R

G = 活动关联目标中的最高优先级归一化值
D = 1 - mastery_level / 5
M = (manual_importance - 1) / 4
R = project_relevance ? 1 : 0
```

主要接口：

```text
POST   /goals
GET    /goals
GET    /goals/{goal_id}
PATCH  /goals/{goal_id}
DELETE /goals/{goal_id}

POST   /learning-status
GET    /learning-status
GET    /learning-status/{status_id}
PATCH  /learning-status/{status_id}
DELETE /learning-status/{status_id}
```

目标和学习状态只允许用户通过明确的 CRUD 操作修改。聊天、RAG、模型输出和问答历史都不会自动改变掌握程度或重要度。完整说明见 [`docs/GOALS_AND_LEARNING_STATUS.md`](docs/GOALS_AND_LEARNING_STATUS.md)。

## 6. 环境变量说明

复制 `.env.example` 为 `.env`，然后填写真实配置。`.env` 已被 `.gitignore` 忽略，不要提交真实密钥。

| 变量 | 说明 | 默认值 |
| --- | --- | --- |
| `APP_HOST` | API 监听地址 | `0.0.0.0` |
| `APP_PORT` | API 监听端口 | `8000` |
| `UPLOAD_DIR` | 上传目录 | `uploads` |
| `MAX_UPLOAD_SIZE_MB` | 单文件大小限制 | `50` |
| `MYSQL_HOST` | MySQL 地址 | `127.0.0.1` |
| `MYSQL_PORT` | MySQL 端口 | `3306` |
| `MYSQL_USER` | MySQL 用户 | `root` |
| `MYSQL_PASSWORD` | MySQL 密码 | 无 |
| `MYSQL_DATABASE` | MySQL 数据库 | `rag_knowledge_base` |
| `QDRANT_URL` | Qdrant 地址 | `http://localhost:6333` |
| `QDRANT_API_KEY` | Qdrant Cloud API Key | 空 |
| `QDRANT_COLLECTION` | Qdrant collection 名称 | `rag_chunks` |
| `EMBEDDING_MODEL_NAME` | Embedding 模型 | `BAAI/bge-m3` |
| `EMBEDDING_DEVICE` | 推理设备 | `cpu` |
| `DEEPSEEK_API_KEY` | DeepSeek API Key | 必填 |
| `DEEPSEEK_BASE_URL` | DeepSeek OpenAI 兼容地址 | `https://api.deepseek.com` |
| `DEEPSEEK_MODEL` | DeepSeek 模型 | `deepseek-v4-flash` |
| `RAG_REWRITE_ENABLED` | 是否允许按需查询改写 | `true` |
| `RAG_REWRITE_SCORE_THRESHOLD` | 触发改写与可回答性判断的相似度阈值 | `0.45` |
| `RAG_REWRITE_MIN_RESULTS` | 低于该召回数时触发改写 | `2` |
| `RAG_REWRITE_SHORT_QUERY_LENGTH` | 过短问题判定长度 | `4` |
| `RAG_CONTEXT_MAX_CHARS` | Prompt 上下文最大字符数 | `12000` |
| `RAG_MAX_CHUNKS_PER_SOURCE` | 每个来源最多进入上下文的 Chunk 数 | `3` |
| `LEARNING_SESSION_REVIEW_MAX_QA` | 主动生成 Session 复盘时最多读取的最近问答组数 | `20` |
| `SESSION_MEMORY_ENABLED` | 全局启用当前 Session 最近问答记忆 | `true` |
| `SESSION_MEMORY_MAX_TURNS` | Session Memory 最大轮数 | `6` |
| `SESSION_MEMORY_MAX_CHARS` | Session Memory 总字符预算 | `3000` |
| `RAG_TOTAL_PROMPT_MAX_CHARS` | 动态 Context Bundle 总字符预算 | `18000` |
| `RUN_HISTORY_RETENTION_DAYS` | 手动清理使用的默认保留天数 | `10` |
| `TOOL_DEFAULT_TIMEOUT_SECONDS` | 内部 Tool 默认 Timeout | `15` |
| `ENABLED_INTERNAL_TOOLS` | 明确启用的只读内部 Tool 名称 | 六个内置工具 |
| `MCP_ENABLED` | 是否启用只读 MCP 管理能力 | `true` |
| `MCP_CONFIG_PATH` | 可信本地 MCP 配置路径 | `config/mcp_servers.local.yaml` |
| `MCP_MAX_INPUT_CHARS` | MCP Tool 输入字符上限 | `10000` |
| `MCP_MAX_OUTPUT_CHARS` | MCP Tool 输出字符上限 | `20000` |
| `MCP_MAX_SCHEMA_CHARS` | 单 Tool Schema 字符上限 | `50000` |
| `MCP_MAX_TOOLS_PER_SERVER` | 单 Server 全局工具数上限 | `50` |
| `OFFICIAL_SOURCES_ENABLED` | 是否启用官方资料校验与审批 API | `true` |
| `OFFICIAL_FETCH_TIMEOUT_SECONDS` | 单次官方来源请求超时 | `20` |
| `OFFICIAL_FETCH_MAX_REDIRECTS` | 同 Host 重定向上限 | `3` |
| `OFFICIAL_FETCH_MAX_BYTES` | 官方来源最大响应字节数 | `10485760` |
| `OFFICIAL_SNAPSHOT_KEEP` | 每个来源默认保留的未引用快照数 | `5` |
| `NOTE_VERSION_KEEP` | 每篇笔记默认保留的未固定版本数 | `10` |
| `APPROVAL_EXPIRES_HOURS` | 高风险审批有效小时数 | `24` |
| `OFFICIAL_CHECKPOINT_ENABLED` | 是否启用 MySQL 审批 Checkpointer | `true` |
| `LANGSMITH_TRACING` | LangSmith Trace 总开关 | `false` |
| `LANGSMITH_OBSERVABILITY_ENABLED` | 项目 Observability 适配层开关；未设置时兼容 `LANGSMITH_TRACING` | `false` |
| `LANGSMITH_PROJECT` | Trace Project | `obsidian-learning-agent` |
| `LANGSMITH_WORKSPACE_ID` | 多 Workspace Key 的可选目标 Workspace | 空 |
| `LANGSMITH_SAFE_MODE` | 强制安全观测前置条件 | `true` |
| `LANGSMITH_HIDE_INPUTS` / `LANGSMITH_HIDE_OUTPUTS` | SDK 全局隐藏输入输出 | `true` |
| `LANGSMITH_TRACE_SCHEMA_VERSION` | 稳定 Metadata 契约版本 | `ola-trace-v1` |

## 官方资料校验与审批

在 `/official` 手动完成 Source 登记、Fetch、Note Link、Audit、完整 Diff 审阅与整批批准/拒绝。Apply 前会再次核对全部笔记 Hash，先创建 Before Version，再原子替换并单笔记 Reindex；任意 stale 会阻止整批写入。默认 HTTPS，公开来源内容按不可信数据处理。详情见 [`docs/OFFICIAL_SOURCE_AUDIT_AND_APPROVAL.md`](docs/OFFICIAL_SOURCE_AUDIT_AND_APPROVAL.md)。

## 只读 MCP

复制 `config/mcp_servers.example.yaml` 为被 Git 忽略的 `config/mcp_servers.local.yaml`，按本机虚拟环境调整项目内 Server 的 command。应用不会自动连接；在 `/agent` 中手动 Connect、Refresh 或 Disconnect。MCP Tool 固定为 READ_ONLY、只允许 `caller=user`，并继续通过原 `/agent/tools/{tool_name}/execute` 执行与审计。项目内 Server 也可单独运行：

```powershell
python -m app.mcp.server
```

## LangSmith 可观测性

阶段 7A 增加可选 LangSmith 全链路 Trace。MySQL `agent_runs` 和 `tool_executions` 仍是本地事实与审计来源；LangSmith 仅用于外部 Trace、调试和后续评估。RAG Ask/Retrieve、Rewrite、Retrieval、Rerank、Context、DeepSeek、Citation、Memory、Tool、MCP 和官方 Fetch/Audit/Approval/Apply/Reindex 均使用稳定 Span 名。

默认安全模式同时使用 SDK 全局隐藏输入输出与项目 Metadata Sanitizer。Trace 不发送完整 Query、Answer、Prompt、Chunk、Memory、Snapshot、Proposal、Diff、Tool/MCP 消息、绝对路径或密钥。LangSmith 关闭、缺 Key、断网或上报失败时业务继续，Shutdown 只做有限 Flush。

安全状态接口：

```http
GET /agent/observability/status
```

`/agent` 页面显示启用、配置、Project、Safe Mode、Schema Version、稳定错误码及 Run 的只读 Trace ID；不会显示 API Key 或完整 Workspace ID。完整契约见 [`docs/LANGSMITH_OBSERVABILITY.md`](docs/LANGSMITH_OBSERVABILITY.md)。

## RAG 固定评估

项目提供本地固定数据集、确定性检索/引用/拒答指标、隔离 Retrieval Profile、可恢复实验、LangSmith 安全子集和脱敏报告。高级页面为 `/agent/rag-evaluation`；真实数据集默认被 Git 忽略，评估不会写入 QA History 或最近会话。阶段 7B～7D 已按 Baseline → 隔离候选 → 三轮 Retrieval/Full RAG 门禁执行；Chunk V2 与 Link V2 均因主提升和 no-answer 安全门禁失败而未晋级，生产仍使用 `rag-baseline-v1`。

```powershell
python -m app.rag_evaluation.cli validate-dataset --file obsidian-real-v1.yaml
python -m app.rag_evaluation.cli run --dataset-file obsidian-real-v1.yaml --profile rag-baseline-v1 --mode retrieval
python -m app.rag_evaluation.cli compare <experiment-a> <experiment-b>
```

完整 Schema、指标公式、隐私边界、Baseline 和报告说明见 [`docs/RAG_EVALUATION.md`](docs/RAG_EVALUATION.md)，最终实验 ID、均值/标准差、候选拒绝理由与回滚条件见 [`docs/RAG_OPTIMIZATION_DECISION.md`](docs/RAG_OPTIMIZATION_DECISION.md)。

## 7. 本地启动步骤

先确保本地 MySQL 已启动，并创建数据库：

```sql
CREATE DATABASE IF NOT EXISTS rag_knowledge_base CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

启动 Qdrant：

```bash
docker compose up -d qdrant
```

创建虚拟环境并安装依赖：

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

启动 API：

```bash
uvicorn app.main:app --reload
```

访问健康检查：

```bash
curl http://localhost:8000/health
```

## 8. Docker Compose 启动步骤

当前 compose 使用本地 MySQL，API 容器通过 `host.docker.internal:3306` 访问你电脑上的 MySQL，Qdrant 由 Docker 启动。

```bash
docker compose up -d --build
```

如果你希望 MySQL 也放入 Docker，可以在 `docker-compose.yml` 中追加 mysql service，并把 `MYSQL_HOST` 改为 `mysql`。

## 9. API 示例

上传文档：

```bash
curl -X POST http://localhost:8000/documents/upload \
  -F "file=@./example.pdf"
```

返回：

```json
{
  "document_id": "...",
  "filename": "example.pdf",
  "chunk_count": 12
}
```

提问：

```bash
curl -X POST http://localhost:8000/rag/ask \
  -H "Content-Type: application/json" \
  -d "{\"question\":\"这份文档讲了什么？\",\"top_k\":5}"
```

返回：

```json
{
  "answer": "基于知识库生成的答案 [来源1]",
  "sources": [
    {
      "document_id": "...",
      "filename": "example.pdf",
      "page_no": 1,
      "chunk_index": 0,
      "score": 0.82,
      "text": "原始 chunk 片段"
    }
  ]
}
```

## 10. 后续可扩展方向

- hybrid search：结合 BM25 和向量检索
- Obsidian Vault 扫描、Markdown 语义切分与内部链接扩展
- Redis 缓存：缓存 embedding、热点问答、会话状态
- Agent tool 对接：把知识库检索作为工具接入 Agent
- RAGAS 评估：构建自动化 RAG 质量评估集
## 本地调试后台页面

访问地址：

```text
http://127.0.0.1:8000/admin
```

页面用途：

该页面用于本地开发、维护学习目标和学习状态、查看重要指数、上传文档、测试 RAG 问答、执行 RAG retrieve 检索调试并查看 sources。它是轻量级调试页面，不是生产级管理后台。

注意事项：

- 该页面不包含权限系统，不应直接暴露到公网。
- 页面不会展示 API Key、数据库密码或完整本地绝对路径。
- 可以通过 `.env` 中的 `ADMIN_ENABLED=true` 开启页面；设置为 `false` 时访问 `/admin` 会返回 404。

如何测试：

1. 启动后端：

```powershell
cd "<项目根目录>"
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

2. 打开：

```text
http://127.0.0.1:8000/admin
```

3. 在页面中依次测试：

- 检查 `/health` 状态。
- 上传一个 PDF、DOCX、TXT 或 CSV 测试文档。
- 刷新文档列表。
- 点击“View Detail”查看文档处理状态。
- 点击“Chunks”或文档详情中的“查看 chunks”，检查文档解析和 chunk 切分质量。
- 在“RAG 检索调试”区域输入问题，调用 `/rag/retrieve` 查看 `rewritten_query`、命中的 chunks、`score` 和 `rerank_score`。
- 输入问题，调用 `/rag/ask` 测试问答。
- 查看 answer 和 sources。

## MySQL 启动说明

如果启动 FastAPI 时看到类似下面的错误：

```text
Can't connect to MySQL server on '127.0.0.1'
```

说明当前 `MYSQL_HOST` 和 `MYSQL_PORT` 指向的位置没有 MySQL 服务在监听。当前项目支持两种本地方式：

1. 使用本机 MySQL

确认 MySQL 已启动，并创建数据库：

```sql
CREATE DATABASE IF NOT EXISTS rag_knowledge_base CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

`.env` 中保持：

```text
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=your_mysql_password
MYSQL_DATABASE=rag_knowledge_base
```

2. 使用 Docker Compose MySQL

确保 Docker 服务已启动后运行：

```powershell
cd "<项目根目录>"
docker compose up -d mysql qdrant
```

再启动 API：

```powershell
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

说明：

- `DATABASE_REQUIRED_ON_STARTUP=false` 时，即使数据库暂时不可用，`/health` 和 `/admin` 也可以启动，方便排查环境问题。
- 文档上传、文档列表、文档详情和 RAG 问答仍然需要 MySQL 正常可用。
- 如果希望数据库不可用时 FastAPI 直接启动失败，可以在 `.env` 中设置 `DATABASE_REQUIRED_ON_STARTUP=true`。

## 文档生命周期接口

### 删除文档

```http
DELETE /documents/{document_id}
```

作用：

- 根据 `document_id` 查询 MySQL `documents` 表。
- 如果文档不存在，返回 404。
- 删除 Qdrant 中 `payload.document_id` 等于该 `document_id` 的所有 points。
- 保留上传目录中的原始上传文件。
- 将 MySQL 文档状态更新为 `deleted`，并写入 `deleted_at`。

返回示例：

```json
{
  "document_id": "...",
  "status": "deleted",
  "message": "document soft deleted successfully"
}
```

### 重建索引

```http
POST /documents/{document_id}/reindex
```

作用：

- 根据 `document_id` 查询已有文档。
- 如果文档不存在，返回 404。
- 如果文档状态是 `deleted`，返回 400。
- 先删除 Qdrant 中该文档的旧 chunks。
- 复用现有文档处理逻辑，重新读取原始文件、解析、切分、生成 embedding，并写入 Qdrant。
- 不生成新的 `document_id`，而是在原 `document_id` 上重建索引。

返回示例：

```json
{
  "document_id": "...",
  "status": "processing",
  "message": "document reindex started"
}
```

### 文档状态

- `processing`：文档正在处理或重建索引。
- `completed`：文档处理完成，chunks 已写入 Qdrant。
- `failed`：文档处理失败，`error_message` 会记录失败原因。
- `deleted`：文档已软删除，Qdrant 中对应向量已按 `document_id` 清理，上传目录中的原始上传文件默认保留。
- `purged`：文档已彻底清理，源文件已尝试删除，数据库记录保留为 `purged` 状态。

### documents 表新增字段

当前模型新增了：

- `updated_at`
- `processed_at`
- `deleted_at`
- `purged_at`
- `file_hash`
- `error_message`

应用启动时会尽量自动补齐这些字段。如果需要手动同步 MySQL 表结构，可以执行：

```sql
ALTER TABLE documents ADD COLUMN updated_at DATETIME NULL;
ALTER TABLE documents ADD COLUMN processed_at DATETIME NULL;
ALTER TABLE documents ADD COLUMN error_message TEXT NULL;
```

如果字段已存在，不要重复执行对应语句。

## 本地调试后台页面补充

`/admin` 现在除了上传文档、查看文档列表、查看详情、测试 RAG 问答和查看 sources，还支持：

- 在文档列表中删除文档。
- 在文档列表中重建索引。
- 在文档列表中查看文档 chunks。
- 在文档详情区删除当前文档。
- 在文档详情区重建当前文档索引。
- 在文档详情区查看当前文档 chunks。
- 在 RAG 检索调试区调用 `/rag/retrieve`，只执行检索，不调用 DeepSeek answer generation。
- 显示 `status`、`processed_at`、`updated_at` 和 `error_message`。

删除操作会先弹出二次确认；重建索引会立即返回 `processing`，页面不会阻塞等待处理完成，可以通过“刷新详情”查看状态变化。

检索调试区会展示原始 question、`rewritten_query`、`retrieved_count` 和检索到的 chunks。文档 chunks 查看用于检查文档解析和切分质量。`score` 是 Qdrant 原始相似度分数，`rerank_score` 是 reranker 分数，二者不能直接比较。

该页面仍然仅用于本地开发和演示，不包含权限系统，不应暴露到公网。

## Rerank 检索增强

Rerank 用于在 Qdrant 初始召回后，对候选 chunks 进行二次排序，从而提升最终交给 LLM 的上下文质量。

当前流程：

```text
question
-> original query embedding
-> Qdrant 原始查询初始召回 retrieve_top_k
-> 按召回质量决定是否 query rewrite
-> 可选 rewritten query embedding + Qdrant 二路召回
-> 合并并按 chunk_id 去重
-> rerank top_k
-> Context Builder + [来源N]
-> DeepSeek answer
-> citation validation
```

推荐默认配置：

```env
RERANK_ENABLED=false
RERANK_MODEL=BAAI/bge-reranker-v2-m3
RERANK_TOP_N=5
```

说明：
- `RERANK_ENABLED=false` 时不启用真实 rerank，系统使用 Qdrant 原始排序，并取前 `top_k` 个 chunks。
- `RERANK_ENABLED=true` 时会尝试加载 `RERANK_MODEL` 指定的 reranker。
- 优先尝试 `FlagEmbedding` 的 `FlagReranker`，失败后尝试 `sentence-transformers` 的 `CrossEncoder`。
- 如果依赖缺失、模型不存在或加载失败，系统会记录 warning，并自动 fallback 到 Qdrant 原始排序，不影响 `/rag/ask` 基本运行。
- Qdrant 初始召回数量为 `max(top_k * 5, 20)`，最终交给 DeepSeek 的 chunks 数量仍受请求中的 `top_k` 控制。
- `/rag/ask` 返回的 `sources` 会保留原始 `score`；如果真实 rerank 成功，还会兼容式增加 `rerank_score`。
- `/admin` 的系统状态区会展示 `RERANK_ENABLED`、`RERANK_MODEL` 和 `RERANK_TOP_N` 这类非敏感配置，不展示 API Key、数据库密码或完整本地绝对路径。

## RAG 检索评估脚本

`scripts/evaluate_retrieval.py` 用于批量评估 RAG 的 retrieval 效果。它只评估检索结果，不调用 DeepSeek 生成答案，也不评估最终回答质量。

`/admin` 适合手动上传文档、查看文档状态、测试单次 RAG 问答和查看 sources；`scripts/evaluate_retrieval.py` 适合批量检索评估。二者用途不同，评估脚本不在 `/admin` 页面中执行长时间任务。

评估集文件：

```text
tests/eval_questions.json
```

示例格式：

```json
[
  {
    "question": "文档中主要讲了什么？",
    "expected_document_id": "替换为真实 document_id",
    "expected_keywords": ["关键词1", "关键词2"]
  }
]
```

填写方法：
- 先通过 `/admin` 上传文档。
- 在文档列表或文档详情中复制真实 `document_id`。
- 把 `expected_document_id` 从 `替换为真实 document_id` 改成真实值。
- `expected_keywords` 填入你期望在检索 chunk 文本里命中的关键词。

运行命令：

```powershell
python scripts/evaluate_retrieval.py --top-k 5
```

脚本会在控制台输出每个问题的结果，并写入：

```text
reports/retrieval_eval_result.json
```

指标含义：
- `Hit Rate@K`：前 K 个检索结果里是否包含期望文档，多个问题时取平均。
- `MRR`：期望文档出现得越靠前分数越高；第 1 位为 `1`，第 2 位为 `0.5`，没有命中为 `0`。
- `keyword_match_rate`：期望关键词在返回 chunk 文本中的命中比例。

注意：
- 如果 `tests/eval_questions.json` 不存在或为空，脚本会给出提示。
- 如果 `expected_document_id` 仍是占位符，脚本会跳过该问题并提示替换为真实 `document_id`，不会让整个评估崩溃。
- 为避免调用 DeepSeek，评估脚本不会使用 LLM 生成答案，也不会使用 LLM query rewrite；它复用项目中的 embedding、Qdrant 检索和 rerank 逻辑。

## 工程稳定性与排查说明

### 文档生命周期接口

文档状态含义：
- `processing`：文档已创建记录，正在后台解析、切分、向量化和写入 Qdrant。
- `completed`：文档处理完成，chunk 已写入 Qdrant。
- `failed`：文档处理失败，`error_message` 会记录失败原因。
- `deleted`：文档已软删除，Qdrant 中对应向量已按 `document_id` 清理，上传目录中的源文件默认保留。
- `purged`：文档已彻底清理，源文件已尝试删除，数据库记录保留为 `purged` 状态。

接口：
- `GET /documents`：查看文档列表，默认不显示 `deleted` 和 `purged` 文档。
- `GET /documents/{document_id}`：查看文档详情和处理状态。
- `DELETE /documents/{document_id}`：删除文档、清理向量并标记为 `deleted`。
- `DELETE /documents/{document_id}/purge`：清理向量、删除源文件并标记为 `purged`。
- `POST /documents/{document_id}/reindex`：在原 `document_id` 上重建索引，不生成新的文档 ID。

### Rerank 配置说明

配置项：

```env
RERANK_ENABLED=false
RERANK_MODEL=BAAI/bge-reranker-v2-m3
RERANK_TOP_N=5
```

说明：
- `RERANK_ENABLED=false` 时使用 Qdrant 原始排序。
- `RERANK_ENABLED=true` 时会尝试加载 `RERANK_MODEL`。
- 优先尝试 `FlagEmbedding`，失败后尝试 `sentence-transformers` 的 `CrossEncoder`。
- 依赖缺失、模型缺失或加载失败时会记录 warning，并自动 fallback 到 Qdrant 原始排序。

### RAG 检索评估脚本

评估集：

```text
tests/eval_questions.json
```

运行：

```powershell
python scripts/evaluate_retrieval.py --top-k 5
```

指标：
- `Hit Rate@K`：前 K 个检索结果中是否包含期望文档。
- `MRR`：期望文档出现得越靠前分数越高，第 1 位为 `1`，第 2 位为 `0.5`。
- `keyword_match_rate`：期望关键词在检索 chunk 文本中的命中比例。

该脚本只评估 retrieval，不调用 DeepSeek 生成答案，也不评估最终答案质量。

### 本地调试后台页面

访问地址：

```text
http://127.0.0.1:8000/admin
```

用途：
- `/admin` 用于本地开发、上传文档、查看处理状态、测试单次 RAG 问答、执行 RAG retrieve 检索调试、查看文档 chunks 和查看 sources。
- `scripts/evaluate_retrieval.py` 用于批量检索评估。
- 二者用途不同，后台页面不会直接执行长时间评估任务。
- retrieve 调试只调用 `/rag/retrieve`，用于查看 `rewritten_query`、检索 chunks、Qdrant `score` 和 `rerank_score`，不调用 DeepSeek answer generation。
- 文档 chunks 查看调用 `GET /documents/{document_id}/chunks`，用于检查文档解析和切分质量。
- `score` 是 Qdrant 原始相似度分数，`rerank_score` 是 reranker 分数，二者不能直接比较。

配置：

```env
ADMIN_ENABLED=true
```

`ADMIN_ENABLED=true` 时允许访问 `/admin`；设置为 `false` 时访问 `/admin` 返回 404。该页面不包含生产级权限系统，不应直接暴露到公网。

安全说明：
- 页面不会展示 `DEEPSEEK_API_KEY`。
- 页面不会展示数据库密码。
- 页面不会展示完整 `DATABASE_URL`。
- 页面不会展示完整本地绝对路径。

常见问题：
- 页面打开空白：确认后端已启动，并访问 `http://127.0.0.1:8000/admin`。
- `admin.js` 加载失败：确认 `main.py` 已挂载 `/static`，并检查浏览器 Network 中 `/static/admin.js` 是否返回 200。
- 调接口返回 404：确认接口路径和 HTTP 方法是否正确，例如上传使用 `POST /documents/upload`。
- 上传后一直 `processing`：查看文档详情中的 `error_message`，并检查后端日志、MySQL、Qdrant 和模型加载状态。

### 常见问题排查

MySQL 连接失败：
- 确认 MySQL 已启动。
- 检查 `.env` 中 `MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_USER`、`MYSQL_PASSWORD`、`MYSQL_DATABASE`。
- 如果使用 `DATABASE_URL`，确认用户名、密码、主机、端口和数据库名正确。

Qdrant 连接失败：
- 确认 Qdrant 已启动。
- 本地常用命令：`docker compose up -d qdrant`。
- 当前项目优先使用 gRPC，需确认 `6334` 端口可访问。

DeepSeek API Key 未配置：
- `.env` 中需要设置 `DEEPSEEK_API_KEY`。
- 后端不会在日志或页面中输出 API Key。
- DeepSeek 调用失败时 API 返回友好错误，真实错误原因只写入日志。

Embedding 模型首次下载较慢：
- `BAAI/bge-m3` 首次加载需要下载或读取本地模型文件。
- 后续请求会复用内存中的模型，通常明显更快。
- 可将模型放在项目内 `models/` 目录，并通过 `EMBEDDING_MODEL` 或 `EMBEDDING_MODEL_NAME` 指向相对路径。

文档解析失败：
- 文档状态会变为 `failed`。
- `documents.error_message` 会记录失败原因。
- 后台日志会记录 `document_id`、`filename`、parser 类型和简短错误信息。

上传后一直 `processing`：
- 检查后端日志是否正在下载模型或连接 Qdrant。
- 查看文档详情中的 `error_message`。
- 确认 Qdrant 和 MySQL 正常可用。

Windows 路径问题：
- `.env` 中 `UPLOAD_DIR` 建议使用相对路径 `uploads`。
- 不要在代码中写死本机路径。
- 文件路径由项目根目录动态计算。

### 项目移动路径后如何运行

本项目不依赖硬编码绝对路径。上传目录通过 `UPLOAD_DIR` 配置，模板、静态文件和上传目录都基于项目根目录动态计算。

移动项目后：
1. 进入新的项目根目录。
2. 检查 `.env` 中数据库、Qdrant、模型相对路径是否仍然有效。
3. 确认 `UPLOAD_DIR=uploads` 或其他相对路径存在。
4. 重新启动后端。

## RAG 调试接口

本项目提供两个轻量调试接口，用于区分“检索问题”和“生成问题”。这两个接口不会生成最终答案，也不会写入问答历史。

### POST /rag/retrieve

作用：只执行 LangGraph 检索路径，返回原始查询优先、必要时双路召回后的查询信息、Qdrant 原始召回分数和 rerank 分数，不调用 DeepSeek answer generation。

请求示例：

```json
{
  "question": "这个项目支持哪些文件格式？",
  "document_id": "可选 document_id",
  "top_k": 5
}
```

返回示例：

```json
{
  "question": "这个项目支持哪些文件格式？",
  "rewritten_query": "这个项目支持哪些文件格式？",
  "document_id": null,
  "top_k": 5,
  "retrieved_count": 1,
  "chunks": [
    {
      "chunk_id": "document-id_0",
      "document_id": "document-id",
      "filename": "demo.txt",
      "file_type": "txt",
      "page_no": 1,
      "section_title": null,
      "chunk_index": 0,
      "score": 0.4922,
      "rerank_score": 0.0742,
      "text": "chunk 原文内容"
    }
  ]
}
```

说明：
- `question` 不能为空。
- `document_id` 可选；传入后只检索该文档。
- 初始召回数量为 `max(top_k * 5, 20)`，最终返回 `top_k` 个 chunk。
- 如果没有检索结果，返回 `chunks=[]`。
- query rewrite 仅在召回质量不足、明显指代或问题过短时触发；失败后保留原始召回继续执行。
- 该接口不调用 DeepSeek 生成答案，不写入 `qa_history`。

### GET /documents/{document_id}/chunks

作用：查看某个文档已经写入 Qdrant 的 chunk 内容，用于检查文档解析和切分质量。

返回示例：

```json
{
  "document_id": "document-id",
  "filename": "demo.txt",
  "status": "completed",
  "chunk_count": 2,
  "chunks": [
    {
      "chunk_id": "document-id_0",
      "chunk_index": 0,
      "page_no": 1,
      "section_title": "项目背景",
      "text": "chunk 原文内容"
    }
  ]
}
```

说明：
- 文档不存在返回 404。
- 文档状态为 `deleted` 时返回 400。
- 接口从 Qdrant 按 `payload.document_id` 查询，并尽量按 `chunk_index` 升序返回。
- 返回内容不会包含完整本地 `file_path`。

### /rag/retrieve 与 /rag/ask 的区别

- `/rag/retrieve`：检索调试接口，只返回 chunks，不生成答案，不写入 `qa_history`。
- `/rag/ask`：完整 RAG 问答接口，会检索 chunks，然后调用 DeepSeek 生成 `answer`，并返回 `answer + sources`。

### score 与 rerank_score

- `score` 是 Qdrant 原始向量检索相似度分数。
- `rerank_score` 是 reranker 对 query 和 chunk 相关性的二次排序分数。
- 两个分数来自不同模型或算法，不能直接横向比较，只适合分别观察排序变化。

## 历史问答记录

`POST /rag/ask` 完整问答接口会在生成答案后写入 `qa_history`，用于本地调试、项目演示和排查回答来源。

当前保存内容包括：
- `question`：用户原始问题。
- `rewritten_query`：用于检索的改写问题。
- `answer`：DeepSeek 生成的答案；无检索结果时保存“根据当前知识库资料，无法确定”。
- `source_chunks`：本次回答引用的 sources，包含 `chunk_id`、`document_id`、`filename`、`page_no`、`chunk_index`、`score`、`rerank_score` 和 `text` 等非敏感字段。
- `created_at`：问答创建时间。

### GET /qa/history

分页查看历史问答列表。

请求示例：

```text
GET /qa/history?page=1&page_size=20&keyword=上传&document_id=可选文档ID
```

参数说明：
- `page`：页码，最小为 1。
- `page_size`：每页数量，默认 20，最大 100。
- `keyword`：可选，按 question 或 answer 模糊搜索。
- `document_id`：可选，按历史 sources 中的 `document_id` 过滤。

返回示例：

```json
{
  "total": 1,
  "page": 1,
  "page_size": 20,
  "items": [
    {
      "id": "qa-id",
      "question": "这个项目支持哪些文件格式？",
      "rewritten_query": "项目支持文件格式",
      "answer_preview": "系统支持 PDF、DOCX、TXT、CSV 文件...",
      "created_at": "2026-06-13T12:00:00",
      "source_count": 2
    }
  ]
}
```

列表接口不会返回完整 `source_chunks` 原文，避免历史较多时页面卡顿。

### GET /qa/history/{qa_id}

查看单条历史问答详情。

返回示例：

```json
{
  "id": "qa-id",
  "question": "这个项目支持哪些文件格式？",
  "rewritten_query": "项目支持文件格式",
  "answer": "系统支持 PDF、DOCX、TXT、CSV 文件 [来源1]。",
  "source_chunks": [
    {
      "chunk_id": "document-id_0",
      "document_id": "document-id",
      "filename": "demo.txt",
      "page_no": 1,
      "chunk_index": 0,
      "score": 0.4922,
      "rerank_score": 0.0742,
      "text": "chunk 原文内容"
    }
  ],
  "created_at": "2026-06-13T12:00:00"
}
```

`qa_id` 不存在时返回 404。历史问答中的 sources 可用于排查回答引用了哪些文档片段。接口不会返回 API Key、数据库密码或完整本地绝对路径。

### /admin 历史问答

`/admin` 页面支持查看历史问答列表和详情：
- 点击“刷新历史”加载 `GET /qa/history`。
- 可用 keyword 搜索 question/answer。
- 可用 document_id 过滤与某个文档相关的历史问答。
- 点击“详情”查看 question、rewritten_query、answer 和 source_chunks。
- source 中的 `score` 是 Qdrant 原始相似度分数，`rerank_score` 是 reranker 分数，二者不能直接比较。

## Alembic 数据库迁移

项目已引入 Alembic 管理数据库结构。Alembic 连接信息从现有配置读取，优先使用 `.env` 中的 `DATABASE_URL`，否则使用 `MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_USER`、`MYSQL_PASSWORD` 和 `MYSQL_DATABASE` 组合生成连接，不在迁移配置中写死本地路径或密码。

首次在新数据库执行迁移：

```powershell
alembic upgrade head
```

生成新的迁移文件：

```powershell
alembic revision --autogenerate -m "describe change"
```

已有本地数据库已经手动建过表时，不要直接对生产或重要数据执行破坏性操作。常见处理方式：

- 开发环境可以先备份数据，再重建数据库后执行 `alembic upgrade head`。
- 如果当前数据库结构已经和最新 model 一致，可以使用：

```powershell
alembic stamp head
```

`stamp head` 只标记当前数据库已经处于最新迁移版本，不会实际创建或修改表。使用前要确认现有表结构已经包含 README 和 migration 中列出的字段。

## file_hash 去重

上传文档时系统会在保存临时上传文件后计算 SHA256，并写入 `documents.file_hash`。如果发现相同 `file_hash` 的文档已经存在，且状态不是 `deleted` 或 `purged`，上传接口会返回已有文档，不会重复解析、embedding 或写入 Qdrant。

重复上传返回示例：

```json
{
  "document_id": "existing-document-id",
  "filename": "demo.txt",
  "status": "duplicate",
  "chunk_count": 5,
  "file_hash": "sha256...",
  "message": "same file already exists"
}
```

当前策略：

- `completed`、`processing`、`failed` 等非删除状态的相同文件会被视为重复。
- `deleted` 或 `purged` 的旧记录不阻止重新上传同一文件。
- 日志只记录文件名、document_id 和 hash 判断结果，不记录完整文件内容。

## soft delete 与 purge

### DELETE /documents/{document_id}

该接口现在是软删除：

```text
DELETE /documents/{document_id}
-> 删除 Qdrant 中 payload.document_id 对应的 chunks
-> MySQL documents.status = deleted
-> 写入 deleted_at
-> 保留 uploads/ 下源文件
-> 保留 documents 数据库记录
```

软删除后：

- `GET /documents` 默认不返回 `deleted` 和 `purged` 文档。
- `GET /documents?status=deleted` 可以查看软删除文档。
- `GET /documents/{document_id}` 仍可查看 deleted 状态详情。
- `POST /documents/{document_id}/reindex` 对 deleted 文档返回 400。

### DELETE /documents/{document_id}/purge

该接口用于彻底清理：

```text
DELETE /documents/{document_id}/purge
-> 删除 Qdrant 中 payload.document_id 对应的 chunks
-> 删除 uploads/ 下源文件，如果文件不存在则记录 warning 但不崩溃
-> MySQL documents.status = purged
-> 写入 purged_at
-> 保留 documents 数据库记录，避免误删审计信息
```

返回示例：

```json
{
  "document_id": "document-id",
  "status": "purged",
  "message": "document purged successfully"
}
```

`/admin` 中原“删除”按钮现在表示软删除，会提示“从向量库移除文档 chunks，但保留源文件和数据库记录”。`Purge` 是危险操作，会二次确认，并尝试物理删除源文件。

## documents 新增字段

- `file_hash`：上传文件 SHA256，用于重复上传判断。
- `deleted_at`：软删除时间。
- `purged_at`：彻底清理时间。

## Obsidian Vault 只读接入（阶段 2）

项目现在可以显式扫描一个本地 Obsidian Vault，将 Markdown 按标题和块结构切分后增量写入现有 Qdrant 集合。Vault 始终是只读数据源：系统不会修改、移动、复制或删除 Markdown，也不会写入 `.obsidian`，且没有文件监听器或自动扫描任务。

在 `.env` 中启用并配置绝对路径：

```dotenv
OBSIDIAN_ENABLED=true
OBSIDIAN_VAULT_PATH=C:\path\to\your\vault
OBSIDIAN_INCLUDE_PATTERNS=*.md,**/*.md
OBSIDIAN_EXCLUDE_DIRS=.obsidian,.trash,Attachments,Templates,Archive,_ai_updates
OBSIDIAN_EXCLUDE_PATHS=Learning/_drafts
OBSIDIAN_MAX_FILE_BYTES=2097152
OBSIDIAN_CHUNK_MIN_CHARS=200
OBSIDIAN_CHUNK_TARGET_CHARS=1200
OBSIDIAN_CHUNK_MAX_CHARS=1800
OBSIDIAN_ATOMIC_BLOCK_HARD_MAX_CHARS=6000
```

主要接口：

- `GET /obsidian/status`：返回安全状态摘要，不暴露 Vault 绝对路径。
- `POST /obsidian/scan`：只读 dry-run，返回 new/changed/unchanged/missing/skipped/error，不写 MySQL、Qdrant 或 Vault。
- `POST /obsidian/index`：执行显式增量索引。
- `GET /obsidian/notes`、`GET /obsidian/notes/{id}`：查看 MySQL 中的结构化元数据。
- `GET /obsidian/notes/{id}/chunks`：查看当前 Qdrant chunks。
- `POST /obsidian/notes/{id}/reindex`：显式重新索引一篇笔记。

`/rag/retrieve` 与 `/rag/ask` 新增可选 `source_types` 和 `source_path_prefixes`：

```json
{
  "question": "LangGraph 的 State 如何设计？",
  "top_k": 5,
  "source_types": ["obsidian_note"],
  "source_path_prefixes": ["Knowledge/LangGraph"]
}
```

默认仍同时检索上传文档与 Obsidian。`document_id` 只适用于上传文档；路径前缀必须是安全的 Vault 相对路径。旧 Qdrant Point 缺少 `source_type` 时继续视为 `uploaded_document`，无需重新生成历史 Embedding。

Markdown 解析支持 YAML Frontmatter、H1～H6、段落、列表、围栏代码、表格、引用、Callout、WikiLink/嵌入和标签。切分遵循标题边界；代码和表格使用结构安全拆分。MySQL `obsidian_notes` 只保存相对路径、哈希、标签、链接和索引状态等元数据，不保存 Markdown 正文；正文仅存在于原始 Vault 与 Qdrant chunk payload。完整说明见 `docs/OBSIDIAN_VAULT_INTEGRATION.md`。
