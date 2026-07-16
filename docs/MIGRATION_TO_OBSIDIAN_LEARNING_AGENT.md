# 迁移到 Obsidian Learning Agent

- 迁移日期：2026-07-16
- 旧项目：`C:\Users\example\Documents\New project\rag-knowledge-base`
- 新项目：`C:\Users\example\Documents\Obsidian-Learning-Agent`

## 已迁移内容

- FastAPI 应用、模板和静态资源：`app/`
- Alembic 配置和版本：`alembic/`、`alembic.ini`
- 测试与评估集：`tests/`
- 脚本：`scripts/`
- 交付文档：`docs/`、`README.md`、`已做流程说明.md`
- 构建和运行配置：`Dockerfile`、`docker-compose.yml`、`.dockerignore`、`.env.example`、`.env`、`requirements.txt`、`pytest.ini`
- 项目本地模型：`models/bge-m3` 与 `models/bge-reranker-v2-m3`

旧项目没有 `.git/`，因此没有可迁移的 Git 历史。新目录在迁移验证结束后初始化独立 Git 仓库，并按补充任务创建基线提交。

## 明确排除的内容

- pytest 临时目录和缓存：`.pytest_tmp/`、`pytest-cache-files-*/`、`test_tmp/`、`tmp/`
- 测试运行产物：`test_artifacts/`
- 旧检索评估输出：`reports/retrieval_eval_result.json`
- 旧上传目录中的活动文件没有放入新项目的 `uploads/`，而是隔离备份到 `data/migration_backup/uploads_legacy/`。备份包含除 `.gitkeep` 外的 58 个文件、共 6,389 字节，逐文件大小和 SHA256 校验均一致。这些文件不会自动进入当前知识库。
- 虚拟环境、Python 字节码、测试缓存和日志（源目录未发现需要迁移的有效内容）

## 数据库与 Qdrant

Docker Compose 中 MySQL 配置为 named volume `mysql_data`，Qdrant 配置为 named volume `qdrant_storage`，没有项目目录 bind mount 数据需要复制。Compose 顶层项目名固定为 `rag-knowledge-base`，从新目录运行时解析出的实际卷名分别为 `rag-knowledge-base_mysql_data` 和 `rag-knowledge-base_qdrant_storage`。

Docker 实际只存在旧 Qdrant volume `rag-knowledge-base_qdrant_storage`；没有发现 `rag-knowledge-base_mysql_data` 或其他 MySQL Docker volume。本机 `MySQL80` Windows 服务正在运行，当前本地应用通过 loopback 主机连接它。因此本次只启动并复用了既有 Qdrant 容器/volume，没有启动 Compose MySQL，也没有创建空 volume。迁移过程没有删除、移动或清空任何数据库、collection 或 volume。

## 模型与缓存

- embedding 模型：`models/bge-m3`（2,295,564,334 字节）
- rerank 模型：`models/bge-reranker-v2-m3`（2,293,242,108 字节）
- 两套模型从旧项目复制一次，不重新下载；本地 `.env` 的模型配置可解析到这些新项目路径。
- 后续可选下载缓存：`model_cache/huggingface`、`model_cache/huggingface/transformers`、`model_cache/sentence-transformers`，示例变量见 `.env.example`。
- 未移动或删除任何用户级 Hugging Face 缓存。

## 新项目运行时存储

- 日志：`runtime/logs/`
- 应用缓存：`runtime/cache/`
- 临时文件：`runtime/temp/`
- 上传：`uploads/`
- 遗留上传隔离备份：`data/migration_backup/uploads_legacy/`
- 评估报告：`reports/`
- 项目模型：`models/`
- 后续模型缓存：`model_cache/`
- MySQL：本机 `MySQL80` Windows 服务；Qdrant：Docker named volume。两者数据均不位于项目目录

这些运行时目录均已加入 `.gitignore`；保留的 `.gitkeep` 只用于维护目录结构。

## 旧目录安全删除条件

只有在新目录完成依赖安装、Python 导入、Alembic 状态、完整 pytest、FastAPI 启动、`/health`、`/admin`、MySQL、Qdrant、`/rag/retrieve` 与 `/rag/ask` 验证，并确认需要保留的旧上传文件后，才能由用户手动删除旧目录。Codex 迁移过程不删除旧目录。

## 验证结果（2026-07-16）

- Python：3.13.9；使用 `uv 0.11.26` 创建项目内 `.venv`，依赖缓存位于 `runtime/cache/uv`。
- 依赖：`uv pip install -r requirements.txt` 成功；为支持现有 MySQL 8 `caching_sha2_password` 认证，将同版本依赖改为 `pymysql[rsa]==1.1.1`。
- 导入：FastAPI、SQLAlchemy、Alembic、Qdrant client、sentence-transformers、pytest、httpx 与 `app.main` 均可导入。
- 路径：业务代码、`.env`、`.env.example`、Compose、Alembic、测试和脚本中的旧绝对路径匹配为 0；旧路径只保留在迁移历史文档中。
- Compose：`docker compose config --quiet` 通过；服务为 `mysql`、`qdrant`、`api`；唯一 bind mount 是新目录 `uploads/` 到 `/app/uploads`。
- 模型：源/目标均为 21 个文件、4,588,806,442 字节，相对路径完全一致；主权重、配置、tokenizer 与 Sentence Transformers 关键文件 SHA256 全部一致。
- 模型实载：本地离线 embedding 成功生成 1024 维向量；本地 rerank 使用 CrossEncoder 成功为两条候选评分；没有重新下载模型。
- Qdrant：既有 `rag-qdrant` 容器和 `rag-knowledge-base_qdrant_storage` volume 可用；健康检查通过；现有 1 个 collection，配置的 collection 存在。容器历史标签仍记录 `C:\Users\example\Desktop\阶段三\rag-knowledge-base\docker-compose.yml`，但当前没有指向该目录的 bind mount；新项目 Compose 配置本身不引用该路径。
- Alembic 1045 根因：FastAPI 将 `Settings.database_url` 的 SQLAlchemy `URL` 对象直接传给 `create_engine`，保留了真实密码；旧 Alembic 代码先调用 `str(settings.database_url)`，SQLAlchemy 会把密码遮蔽为字面量 `***`，随后 Alembic 使用错误密码连接，因此返回 1045。该问题不是 `.env` 路径、内容或环境变量覆盖造成的。
- 配置来源：FastAPI 与 Alembic 现在都通过 `app/core/config.py` 的 `get_settings()` 加载 `BASE_DIR / ".env"`，并直接复用同一个 `database_url` URL 对象。Pydantic 保持标准优先级：进程环境变量高于项目 `.env`，以便 Docker Compose 覆盖 `MYSQL_HOST`；验证时未发现进程级、用户级或机器级 `DATABASE_URL`/`MYSQL_*` 覆盖。
- 脱敏连接：driver=`mysql+pymysql`、username=`root`、host=`127.0.0.1`、port=`3306`、database=`rag_knowledge_base`、password=`***`。SQLAlchemy 直接连接、FastAPI engine 和 Alembic URL 三者均成功执行 `SELECT 1` 并指向同一数据库。
- Migration 状态：初次成功连接时 revision 为空，`alembic_version` 表不存在。只读结构比对确认三张业务表、全部字段、主键和外键与 `20260613_0001` 一致，但缺少 migration 定义的非唯一索引 `ix_documents_file_hash`；数据库有 21 条 documents 记录。补建该索引后 Alembic metadata diff 为 0，再执行 `stamp 20260613_0001`。没有执行 `upgrade` 或 `downgrade`。
- Alembic 最终结果：`heads` 和 `current` 均为 `20260613_0001 (head)`，history 正常。
- pytest：`60 passed in 1.15s`，0 失败，0 跳过。
- FastAPI：新目录 uvicorn 可启动；`/health`、`/docs`、`/admin`、`/documents`、`/qa/history` 均返回 200。
- 数据读取：文档列表返回 3 条，问答历史验证前为 11 条。
- RAG：`/rag/retrieve` 返回 200、3 个 chunk；移除与固定 qdrant-client 版本不兼容的 `check_compatibility` 可选参数后通过。
- 完整问答：在允许外网的桌面用户环境中 `/rag/ask` 返回 200，含答案与 3 个来源，问答历史从 11 增至 12。
- 数据库配置修复后回归：新目录 uvicorn 的 `/health`、`/documents`、`/qa/history` 均返回 200，分别返回 3 个活动文档和 12 条问答历史；未再次调用真实 DeepSeek。
- Git：新项目已初始化独立仓库并创建 `chore: migrate RAG baseline to Obsidian Learning Agent` 基线提交；`.env`、`.venv`、模型、缓存、运行时目录、上传、迁移备份和数据库数据均未跟踪。

## 当前删除判定

`SAFE_TO_DELETE_OLD_PROJECT=true`

删除条件已经满足：数据库配置已统一，三种连接验证通过，Alembic 已处于 head，pytest 和基础 API 回归通过，Qdrant 没有旧目录 bind mount，模型及遗留 uploads 已迁移或隔离备份，且旧绝对路径仅存在于迁移历史文档。Codex 不自动删除旧目录；最终删除仍由用户手动执行。
