# RAG 知识库项目交接与复用说明

> 对于 Obsidian Learning Agent 的后续演进，以 `OBSIDIAN_LEARNING_AGENT_REUSE.md` 为准。

## 1. 文档用途

本文档用于把当前 RAG 知识库能力交接给另一个项目或开发代理。阅读本文档后，应当能够判断：

- 当前系统已经完成哪些能力。
- 另一个项目应该如何复用这些能力。
- 每个接口需要输入什么、会返回什么。
- 文档如何入库、问题如何检索并生成答案。
- MySQL、Qdrant、embedding、rerank 和 DeepSeek 分别承担什么职责。
- 当前系统还存在哪些限制，哪些部分不能按生产级能力理解。

本文档不包含 API Key、数据库密码、完整本地绝对路径等敏感信息。

## 2. 推荐复用方式

### 2.1 推荐方案：作为独立 HTTP 服务调用

当前项目本质上是一个完整的 FastAPI RAG 服务，已经自行管理：

- 文档上传和源文件。
- MySQL 文档状态与问答历史。
- 文档解析和 chunk 切分。
- 本地 embedding 模型。
- Qdrant 向量索引。
- 可选 rerank 模型。
- DeepSeek query rewrite 和答案生成。

因此，另一个项目应通过 HTTP API 调用本服务，而不是复制整个 `app/` 目录，也不要通过 Windows 文件夹软链接直接导入本项目源码。

推荐拓扑：

```text
另一个项目的前端
        |
        v
另一个项目的后端
        |
        | HTTP，RAG_SERVICE_BASE_URL
        v
当前 rag-knowledge-base FastAPI 服务
        |                |
        v                v
      MySQL            Qdrant
        |
        v
   qa_history / documents
```

建议由“另一个项目的后端”调用 RAG 服务，不建议浏览器跨域直连。当前项目没有生产级身份认证，也没有专门配置跨域访问策略。

### 2.2 不建议的方式

- 不要复制整个项目到另一个项目中继续分别修改。这样会产生两套数据库迁移、两套模型缓存、两套 Qdrant collection 和两份逐渐分叉的业务逻辑。
- 不要让另一个项目直接读写本项目的 MySQL 表或 Qdrant collection。数据一致性、soft delete、purge、重建索引等规则都在本项目服务层中。
- 不要用文件夹软链接直接 `import app.services...`。当前项目包含全局配置、数据库 engine、FastAPI 路由和模型缓存，不是按可发布 Python SDK 设计的。

### 2.3 可以复制的内容

如果另一个项目需要保存少量集成代码，可以复制或重新实现以下内容：

- 一个很薄的 HTTP client，例如 `RAGClient`。
- 本文档中的请求和响应数据结构。
- 本项目运行时提供的 `/openapi.json`，用于生成客户端类型。

不要复制 parser、embedding、vector、rerank、LLM 等核心 service。

## 3. 当前项目定位和完成度

项目定位：企业文档 RAG 知识库问答系统 MVP，并已增加部分工程化和调试能力。

### 3.1 已完成

| 模块 | 当前状态 | 说明 |
|---|---|---|
| 健康检查 | 已完成 | `GET /health` |
| 文档上传 | 已完成 | PDF、DOCX、TXT、CSV |
| 上传去重 | 已完成 | 保存后计算 SHA256，按 `file_hash` 判断重复 |
| 后台处理 | 已完成 | FastAPI `BackgroundTasks`，上传立即返回 `processing` |
| 文档解析 | 已完成 | TXT/CSV 直接读取，PDF/DOCX 使用 Docling |
| chunk 切分 | 已完成 | 字符切分，默认 900 字符，重叠 120 字符 |
| embedding | 已完成 | `BAAI/bge-m3`，sentence-transformers，本地加载 |
| 向量存储 | 已完成 | Qdrant，collection 默认 `rag_chunks` |
| 向量检索 | 已完成 | cosine，相似度召回，支持 `document_id` filter |
| query rewrite | 已完成 | DeepSeek 改写，失败回退原问题 |
| rerank | 已完成，可选 | FlagEmbedding 优先，CrossEncoder 兜底，加载失败回退原排序 |
| RAG 问答 | 已完成 | DeepSeek 根据 chunks 生成带来源答案 |
| 纯检索调试 | 已完成 | `POST /rag/retrieve`，不生成答案 |
| 文档 chunks 调试 | 已完成 | `GET /documents/{id}/chunks` |
| 问答历史 | 已完成 | 列表、分页、筛选和详情 |
| 文档重建索引 | 已完成 | 保持原 `document_id` |
| soft delete | 已完成 | 删除向量，保留源文件和 MySQL 记录 |
| purge | 已完成 | 删除向量和源文件，MySQL 标记 `purged` |
| Alembic | 已配置 | 包含初始 migration；运行前需安装 requirements |
| 本地调试后台 | 已完成 | `/admin`，无生产级权限 |
| 检索评估脚本 | 已完成 | Hit Rate@K、MRR、keyword match，不生成答案 |
| 自动测试 | 已完成基础覆盖 | 当前测试重点使用 SQLite 和 mock，不等同于完整 MySQL/Qdrant/模型集成测试 |

### 3.2 明确未完成或不是生产级

- 没有登录、租户隔离、角色权限和接口鉴权。
- 后台任务使用进程内 `BackgroundTasks`，不是 Redis、RQ、Celery 等可靠任务队列。
- 没有 BM25 或 hybrid search。
- 没有 Ragas 最终答案质量评估。
- 没有会话记忆，多轮对话不会自动携带上下文。
- 没有面向公网的限流、审计、配额和网关策略。
- MySQL 与 Qdrant 的更新不是分布式事务，极端失败情况下需要通过状态和日志排查一致性。

## 4. 技术栈和服务职责

| 技术 | 作用 |
|---|---|
| FastAPI | HTTP API、Swagger、后台任务、admin 页面 |
| Pydantic | 请求和响应 schema 校验 |
| SQLAlchemy | MySQL ORM |
| Alembic | 数据库结构迁移 |
| MySQL | 文档元数据、问答历史、反馈表 |
| Qdrant | chunk embedding 和 payload 检索 |
| Docling | PDF、DOCX 文档转 Markdown 文本 |
| sentence-transformers | 加载 `BAAI/bge-m3` 并生成 embedding |
| FlagEmbedding / CrossEncoder | 可选 rerank |
| OpenAI Python SDK | 使用 OpenAI 兼容协议调用 DeepSeek |
| Jinja2 + 原生 JS/CSS | `/admin` 本地调试后台 |
| pytest | 单元和接口基础测试 |

## 5. 项目目录职责

```text
app/
  main.py                    FastAPI 入口、路由和静态文件注册
  core/config.py             环境变量、项目根目录和路径配置
  core/logging.py            日志配置
  db/database.py             engine、Session、Base 和兼容性初始化
  db/models.py               documents、qa_history、feedback ORM
  routers/documents.py       文档上传、查询、删除、purge、reindex、chunks
  routers/rag.py             /rag/ask 和 /rag/retrieve
  routers/qa.py              问答历史列表和详情
  routers/admin.py           /admin 和安全状态接口
  schemas/                   API 请求/响应模型
  services/parser_service.py 文档解析
  services/chunk_service.py  chunk 切分
  services/embedding_service.py embedding 模型
  services/vector_service.py Qdrant 写入、查询和删除
  services/rerank_service.py rerank 和 fallback
  services/llm_service.py    query rewrite、RAG prompt、DeepSeek 调用
  services/rag_service.py    RAG 编排和历史写入
  templates/admin.html       本地调试页面
  static/admin.js            admin 接口调用与交互
  static/admin.css           admin 样式
alembic/                     数据库迁移
scripts/evaluate_retrieval.py 检索评估脚本
tests/                       pytest 测试和评估问题示例
uploads/                     上传源文件，运行时生成
models/                      可选本地模型目录，不应提交或复制给调用方
```

## 6. 系统数据流

### 6.1 文档入库

```text
POST /documents/upload
-> 校验扩展名和大小
-> 保存到 UPLOAD_DIR
-> 计算 SHA256 file_hash
-> 查询是否存在未删除的相同文件
-> MySQL 创建 documents，status=processing
-> BackgroundTasks 调用 process_document
-> ParserService 解析
-> ChunkService 切分
-> bge-m3 生成 embedding
-> Qdrant 写入 vector + payload
-> MySQL status=completed，写入 chunk_count/processed_at
```

失败时 `status=failed`，错误摘要写入 `error_message`。

### 6.2 RAG 问答

```text
POST /rag/ask，AskRequest.question 为原始问题
-> DeepSeek query rewrite
-> rewritten_query embedding
-> Qdrant 初始召回 max(top_k * 5, 20)
-> 可选 rerank
-> 保留最终 top_k chunks
-> 原始 question + chunks 组装 RAG prompt
-> DeepSeek 生成 answer
-> 写入 qa_history
-> 返回 AskResponse(answer + sources)
```

注意：检索使用 `rewritten_query`；最终答案生成使用用户原始 `question`。rewrite 调用失败时会使用规范化后的原问题继续检索。

### 6.3 纯检索调试

```text
POST /rag/retrieve
-> rewrite
-> embedding
-> Qdrant
-> rerank
-> 返回 rewritten_query 和 chunks
```

它不会调用答案生成，也不会写入 `qa_history`。但由于当前 query rewrite 使用 DeepSeek，该接口仍可能产生一次 rewrite API 调用。

## 7. 文档解析和 chunk 规则

### 7.1 解析

- TXT、CSV：使用 UTF-8 读取，无法解码的字符会忽略，整体作为一个 `ParsedSection`，`page_no=1`。
- PDF、DOCX：使用 Docling 转换并导出 Markdown，目前整体作为一个 `ParsedSection`，`page_no=1`。

当前 PDF/DOCX 的精确页码和章节提取仍较粗，不应把 `page_no=1` 理解为真实分页结果。

### 7.2 切分

默认参数：

```text
chunk_size = 900 个 Python 字符
overlap = 120 个 Python 字符
```

切分前会去除空行并对每一行执行 `strip()`。当前是字符窗口切分，不是 tokenizer token 切分，也没有按标题、段落语义或表格结构自适应切分。

## 8. 数据结构

### 8.1 MySQL documents

| 字段 | 含义 |
|---|---|
| id | UUID 文档主键 |
| filename | 用户上传的原文件名 |
| file_type | pdf/docx/txt/csv |
| file_path | 服务内部保存路径；调用方不应依赖 |
| file_hash | SHA256，用于重复上传判断 |
| chunk_count | 已写入的 chunk 数量 |
| status | processing/completed/failed/deleted/purged |
| created_at | 创建时间 |
| updated_at | 更新时间 |
| processed_at | 最近处理成功时间 |
| deleted_at | soft delete 时间 |
| purged_at | purge 时间 |
| error_message | 后台处理失败摘要 |

### 8.2 MySQL qa_history

| 字段 | 含义 |
|---|---|
| id | UUID 问答记录主键 |
| question | 用户原始问题 |
| rewritten_query | 用于检索的改写问题 |
| answer | 最终答案 |
| source_chunks | JSON 引用来源 |
| created_at | 创建时间 |

### 8.3 MySQL feedback

表已存在，字段为 `id / qa_id / rating / comment / created_at`，当前没有公开 feedback API。

### 8.4 Qdrant point payload

```json
{
  "chunk_id": "<document_id>_<chunk_index>",
  "document_id": "uuid",
  "filename": "demo.txt",
  "file_type": "txt",
  "page_no": 1,
  "section_title": null,
  "chunk_index": 0,
  "text": "chunk 原文"
}
```

Qdrant point ID 使用 `uuid5(NAMESPACE_URL, chunk_id)` 生成。删除和过滤都基于 payload 中的 `document_id`。

## 9. API 总览

服务启动后可读取：

```text
GET /docs
GET /openapi.json
```

另一个项目可以使用 `/openapi.json` 生成强类型客户端。

### 9.1 健康检查

```http
GET /health
```

```json
{"status":"ok"}
```

### 9.2 上传文档

```http
POST /documents/upload
Content-Type: multipart/form-data
file=<PDF|DOCX|TXT|CSV>
```

新文档响应：

```json
{
  "document_id": "uuid",
  "filename": "demo.txt",
  "status": "processing",
  "chunk_count": 0,
  "file_hash": "sha256",
  "message": null
}
```

重复文件响应仍为 HTTP 200：

```json
{
  "document_id": "existing-uuid",
  "filename": "demo.txt",
  "status": "duplicate",
  "chunk_count": 5,
  "file_hash": "sha256",
  "message": "same file already exists"
}
```

调用方必须把 `duplicate` 视为“已有可复用文档”，不要继续重复上传。

### 9.3 文档列表和状态

```http
GET /documents?status=completed&skip=0&limit=20
GET /documents/{document_id}
```

`GET /documents` 默认排除 `deleted` 和 `purged`，可以显式传入 `status=deleted` 或 `status=purged`。

文档状态含义：

| status | 调用方处理方式 |
|---|---|
| processing | 继续轮询详情 |
| completed | 可以问答和查看 chunks |
| failed | 展示 `error_message`，可考虑 reindex |
| duplicate | 上传响应专用，使用返回的已有 `document_id` |
| deleted | 已从 Qdrant 移除，源文件保留，不允许 reindex |
| purged | 向量和源文件已清理，MySQL 仅保留记录 |

注意：`DocumentOut` 当前包含内部 `file_path`。另一个项目不应展示或依赖该字段；如果未来对公网开放，应在 RAG 服务中移除或改成只返回安全文件名。

### 9.4 查看文档 chunks

```http
GET /documents/{document_id}/chunks
```

返回按 `chunk_index` 升序排列的 `chunk_id / page_no / section_title / text`。deleted/purged 文档返回 400。

### 9.5 重建索引

```http
POST /documents/{document_id}/reindex
```

响应 `status=processing`。它会清理旧向量并使用同一个 `document_id` 重新解析、切分、embedding 和写入 Qdrant。deleted/purged 文档不能 reindex。

### 9.6 soft delete

```http
DELETE /documents/{document_id}
```

- 删除 Qdrant chunks。
- MySQL 标记 `deleted` 并写入 `deleted_at`。
- 保留源文件和数据库记录。

### 9.7 purge

```http
DELETE /documents/{document_id}/purge
```

- 删除 Qdrant chunks。
- 仅允许删除 `UPLOAD_DIR` 内的源文件。
- MySQL 标记 `purged` 并写入 `purged_at`，不物理删除数据库行。

### 9.8 完整问答

```http
POST /rag/ask
Content-Type: application/json
```

请求：

```json
{
  "question": "这个项目支持哪些文件格式？",
  "document_id": "可选文档 UUID",
  "top_k": 5
}
```

- `question` 必填，不能只包含空格。
- `document_id` 可选；传入后只检索该文档。
- `top_k` 默认 5，范围 1 到 20。

响应：

```json
{
  "answer": "系统支持 PDF、DOCX、TXT 和 CSV。[来源1]",
  "sources": [
    {
      "chunk_id": "uuid_0",
      "document_id": "uuid",
      "filename": "demo.txt",
      "page_no": 1,
      "chunk_index": 0,
      "score": 0.4922,
      "rerank_score": 0.0742,
      "text": "chunk 原文"
    }
  ]
}
```

无检索结果时不会生成答案，直接返回“根据当前知识库资料，无法确定”，同时 `sources=[]`。

### 9.9 纯检索

```http
POST /rag/retrieve
```

请求字段与 `/rag/ask` 相同。响应包含：

- 原始 `question`。
- `rewritten_query`。
- `retrieved_count`。
- 完整 chunks。
- Qdrant `score`。
- 可选 `rerank_score`。

`score` 和 `rerank_score` 来源不同，不能直接比较绝对值，只能分别观察排序。

### 9.10 问答历史

```http
GET /qa/history?page=1&page_size=20&keyword=&document_id=
GET /qa/history/{qa_id}
```

- 列表默认按 `created_at` 倒序。
- `page_size` 最大 100。
- `keyword` 搜索 question 或 answer。
- `document_id` 根据历史 source JSON 过滤。
- 详情返回完整 answer 和 source chunks。

### 9.11 admin

```http
GET /admin
```

用于本地上传、文档状态、chunks、retrieve、ask、sources、历史问答、soft delete 和 purge 调试。它没有权限系统，不应暴露到公网。

## 10. 另一个项目的调用流程

### 10.1 环境变量

另一个项目只需要保存 RAG 服务地址，不需要知道 MySQL、Qdrant、DeepSeek 或模型路径：

```env
RAG_SERVICE_BASE_URL=http://127.0.0.1:8000
RAG_SERVICE_TIMEOUT_SECONDS=120
```

生产或容器环境应替换为内部服务名，例如 `http://rag-api:8000`。

### 10.2 Python httpx 客户端示例

```python
from pathlib import Path
import time

import httpx


class RAGClient:
    def __init__(self, base_url: str, timeout: float = 120.0) -> None:
        self.client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)

    def health(self) -> dict:
        response = self.client.get("/health")
        response.raise_for_status()
        return response.json()

    def upload(self, file_path: Path) -> dict:
        with file_path.open("rb") as file:
            response = self.client.post(
                "/documents/upload",
                files={"file": (file_path.name, file)},
            )
        response.raise_for_status()
        return response.json()

    def get_document(self, document_id: str) -> dict:
        response = self.client.get(f"/documents/{document_id}")
        response.raise_for_status()
        return response.json()

    def wait_until_ready(self, document_id: str, timeout_seconds: int = 600) -> dict:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            document = self.get_document(document_id)
            if document["status"] == "completed":
                return document
            if document["status"] == "failed":
                raise RuntimeError(document.get("error_message") or "document processing failed")
            if document["status"] in {"deleted", "purged"}:
                raise RuntimeError(f"document is {document['status']}")
            time.sleep(2)
        raise TimeoutError("document processing timed out")

    def ask(self, question: str, document_id: str | None = None, top_k: int = 5) -> dict:
        payload = {"question": question, "top_k": top_k}
        if document_id:
            payload["document_id"] = document_id
        response = self.client.post("/rag/ask", json=payload)
        response.raise_for_status()
        return response.json()

    def retrieve(self, question: str, document_id: str | None = None, top_k: int = 5) -> dict:
        payload = {"question": question, "top_k": top_k}
        if document_id:
            payload["document_id"] = document_id
        response = self.client.post("/rag/retrieve", json=payload)
        response.raise_for_status()
        return response.json()
```

### 10.3 推荐业务流程

上传并问答：

```text
upload(file)
-> status=duplicate：直接使用返回的 document_id
-> status=processing：轮询 GET /documents/{id}
-> completed：允许提问
-> failed：展示 error_message
-> POST /rag/ask
-> 展示 answer 和 sources
```

只需要全知识库问答时，可以不传 `document_id`。需要把答案限定在某一份文档时，必须传 `document_id`。

### 10.4 错误处理约定

| HTTP 状态 | 常见含义 |
|---|---|
| 200 | 请求成功；上传时仍需检查业务 `status` 是否为 duplicate |
| 400 | deleted/purged 文档不可查看 chunks 或 reindex |
| 404 | 文档或问答历史不存在；admin 可能已禁用 |
| 413 | 上传文件超过 `MAX_UPLOAD_SIZE_MB` |
| 422 | question 空、top_k/page 参数越界等 schema 校验失败 |
| 503 | MySQL、Qdrant、模型依赖或 DeepSeek 暂时不可用 |

调用方应记录 HTTP 状态和后端 `detail`，但不要把内部异常堆栈直接展示给最终用户。

## 11. 当前服务运行方式

### 11.1 配置

从 `.env.example` 创建 `.env`，至少检查：

```env
UPLOAD_DIR=uploads
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=<设置密码>
MYSQL_DATABASE=rag_knowledge_base
QDRANT_URL=http://127.0.0.1:6333
QDRANT_COLLECTION=rag_chunks
EMBEDDING_MODEL=BAAI/bge-m3
RERANK_ENABLED=false
DEEPSEEK_API_KEY=<设置密钥>
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=<可用模型名>
```

不要把真实 `.env` 提交或复制给另一个项目。

### 11.2 安装和迁移

```powershell
pip install -r requirements.txt
alembic upgrade head
```

如果数据库早已由旧版本 `create_all` 或手动 SQL 建好，并且结构已经与当前 model 一致，应先备份，再使用：

```powershell
alembic stamp head
```

### 11.3 启动依赖和 API

可以使用 Docker Compose 启动 MySQL、Qdrant 和 API：

```powershell
docker compose up -d
```

也可以只启动 Qdrant，并在本机启动 FastAPI：

```powershell
docker compose up -d qdrant
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

建议从项目根目录运行。开发时上传目录位于项目内，不建议用 `uvicorn --reload` 测试较大文件上传，避免文件变化触发重载。

## 12. 模型说明

### 12.1 embedding

- 默认：`BAAI/bge-m3`。
- 首次远程加载可能下载约数 GB 文件并明显变慢。
- `get_embedding_service()` 使用进程级缓存，第一次请求慢，之后复用内存中的模型。
- 可把完整模型放在项目 `models/bge-m3`，然后从项目根目录运行并配置 `EMBEDDING_MODEL=models/bge-m3`。

### 12.2 rerank

- 默认关闭：`RERANK_ENABLED=false`。
- 默认模型名：`BAAI/bge-reranker-v2-m3`。
- 开启后优先尝试 FlagEmbedding，失败再尝试 CrossEncoder。
- 依赖或模型加载失败时回退 Qdrant 原始排序，不让问答流程崩溃。

### 12.3 DeepSeek

当前有两类调用：

1. `rewrite_query`：把原问题改写为检索短语。
2. `generate_answer`：用原问题和检索 chunks 生成最终答案。

`/rag/ask` 通常最多涉及 rewrite 和 answer generation 两次调用；`/rag/retrieve` 不生成答案，但可能调用 rewrite。

## 13. 数据生命周期

### 13.1 重复上传

- SHA256 相同且已有记录状态不是 deleted/purged：返回 `duplicate` 和已有 `document_id`。
- 旧记录已经 soft deleted 或 purged：允许重新上传，生成新的 `document_id`。
- 当前 `file_hash` 是普通索引，不是唯一约束；极高并发下仍可能发生并发重复写入。

### 13.2 soft delete

- 清理 Qdrant。
- 保留源文件。
- 保留 MySQL。
- 不能 reindex。

### 13.3 purge

- 清理 Qdrant。
- 删除 `UPLOAD_DIR` 内源文件。
- 保留 MySQL 审计记录，状态为 purged。

## 14. 安全边界

- `/admin` 仅用于本地调试，不包含登录和权限系统。
- API 当前没有调用方认证，不应直接暴露公网。
- `/admin/status` 只返回非敏感 rerank 配置。
- admin 页面会把 `file_path` 缩减为文件名显示，但 `GET /documents` 和详情 schema 当前仍包含 `file_path`。
- 日志不应记录 API Key、数据库密码和完整用户文档内容。
- purge 只允许删除解析后仍位于 `UPLOAD_DIR` 下的文件，避免任意路径删除。

## 15. 测试和验收现状

运行：

```powershell
pytest -q
```

测试覆盖包括：

- health。
- 上传、去重和后台处理。
- 文档列表、详情、chunks、soft delete、purge、reindex。
- ask、retrieve、query rewrite fallback。
- rerank disabled 和加载失败 fallback。
- Qdrant filter/delete/scroll 的 mock 测试。
- 问答历史分页、筛选、详情和坏 JSON fallback。
- admin HTML 和静态资源。
- Alembic 配置与 migration 文件存在性。
- 检索评估指标。

大部分测试使用 SQLite 和 mock，不会下载真实模型，也不会调用真实 DeepSeek。上线前仍需要在目标环境执行 MySQL + Qdrant + 本地模型 + DeepSeek 的完整集成测试。

## 16. 已知限制和风险

1. FastAPI `BackgroundTasks` 不具备任务持久化。进程重启可能让 processing 文档停留在处理中。
2. PDF/DOCX 当前只导出整体 Markdown，真实页码和章节 metadata 提取不足。
3. chunk 是字符窗口，不是 token 或语义切分。
4. query rewrite 依赖 DeepSeek；失败会回退，但会增加正常请求的 API 延迟和费用。
5. rerank 默认关闭；开启后本地 CPU 推理可能较慢。
6. MySQL 与 Qdrant 之间没有事务一致性。
7. `file_hash` 去重不是数据库唯一约束，无法完全避免并发竞态。
8. 问答历史 `document_id` 过滤基于 source JSON 文本匹配，不适合超大规模历史数据。
9. 没有 API 鉴权和租户隔离。
10. Docker API 容器会加载较大的 embedding/rerank 模型，镜像和启动策略尚未按生产环境优化。

## 17. 给另一个项目或开发代理的接入清单

在另一个项目开始开发前，确认以下事项：

- [ ] 把本服务作为独立进程或容器运行。
- [ ] 只配置 `RAG_SERVICE_BASE_URL`，不复制本服务 `.env`。
- [ ] 先调用 `/health` 判断服务是否可用。
- [ ] 上传后处理 `processing / completed / failed / duplicate`。
- [ ] 上传后通过详情接口轮询，不在 HTTP 上传请求上等待解析完成。
- [ ] 业务问答使用 `/rag/ask`。
- [ ] 检索排查使用 `/rag/retrieve`。
- [ ] 限定文档问答时传 `document_id`。
- [ ] 给用户展示答案时同时展示 sources。
- [ ] 不展示或依赖 `file_path`。
- [ ] 不直接读写 MySQL 和 Qdrant。
- [ ] 不把 `/admin` 暴露到公网。
- [ ] 对 400/404/413/422/503 做明确错误处理。
- [ ] 在生产接入前完成真实 MySQL、Qdrant、模型和 DeepSeek 集成测试。

## 18. 最终复用结论

当前项目已经不是一个适合复制若干 service 文件的代码片段，而是一个有独立数据生命周期的 RAG 后端服务。另一个项目最稳妥的复用方式是：

```text
保留当前项目独立运行
+ 另一个项目新增轻量 HTTP client
+ 通过 /openapi.json 对齐接口类型
+ 通过 document_id 管理文档归属
```

只有在未来明确要把它发布成 Python package 时，才应抽取纯领域模块并设计稳定 SDK；当前版本不建议通过源码复制或文件夹链接复用。
