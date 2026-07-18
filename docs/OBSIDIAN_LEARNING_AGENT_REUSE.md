# Obsidian Learning Agent 演进与复用说明

## 当前定位

本仓库已从独立企业文档 RAG 项目直接演进为 Obsidian Learning Agent 的代码基线。后续能力在同一仓库内继续建设，不再把这里作为一个需要由另一套后端通过 HTTP 调用的独立 RAG 服务。

现有 FastAPI 接口继续保留，用于本地调试、兼容已有调用和后续 Agent 能力的服务入口。文档上传、解析、切分、Embedding、Qdrant 索引、可选 Rerank、DeepSeek 回答、历史记录与文档生命周期功能也继续保留。

## 已完成的 RAG 基础工作流

RAG 主链已经由 LangGraph 编排。`RagService` 作为 API 兼容门面，底层工作流负责 State、节点顺序和条件路由；Embedding、Qdrant、Rerank、DeepSeek 与数据库读写仍由原有 Service 实现。

当前流程具备：

- 原始查询优先检索，并依据召回数量、最高相似度、指代词和查询长度决定是否改写。
- 改写发生时合并原始查询和改写查询的双路召回，按 `chunk_id` 去重并标注命中来源。
- 可配置的 BGE Rerank，以及关闭或执行失败时保持 Qdrant 顺序的回退。
- 独立 Context Builder，限制总字符数和单来源 Chunk 数，并生成稳定的 `[来源N]`。
- Ask/Retrieve 两条路径：Retrieve 不生成答案、不写问答历史；Ask 执行可回答性判断、答案生成、引用验证和精简历史保存。
- `expand_links_placeholder` 节点已预留，但当前只原样传递结果，不执行真实链接扩展。
- 本阶段不使用 LangGraph Checkpointer，也不把完整 State 落盘。

阶段 1 已增加单用户学习目标、学习状态和确定性重要指数。这些数据保存在 MySQL 中，只能由用户通过 CRUD 接口或本地管理页面修改，当前不会注入 RAG Context，也不会由模型自动更新。

## 已接入的 Obsidian Note 数据结构

内部统一使用 `RetrievedChunk`，字段包含 `source_id`、`source_type`、`source_name`、章节、页码、Chunk 序号、文本和两类相关性分数。当前上传文档映射为：

```text
source_id = document_id
source_type = uploaded_document
source_name = filename
```

旧 Qdrant Point 缺少 `source_type` 时会兼容映射为 `uploaded_document`；Obsidian Point 使用 `source_type=obsidian_note`，并携带 Vault 相对路径、标题路径、行号、块类型、tags 和 links。RAG Graph 拓扑不变，来源范围通过 State 传入检索节点。

阶段 2 已完成显式、只读的 Vault 扫描和结构化 Markdown 增量索引。MySQL 仅保存笔记元数据与版本哈希，Qdrant 保存检索所需 chunk；Vault 不接受任何写操作。扫描、索引和单笔记重建只能由用户通过 API 或 `/admin` 主动触发。

## 当前尚未实现

以下内容不属于现阶段完成范围：

- 自动目标分解、截止日期和父子目标。
- 学习进度历史与自动掌握度推断。
- 文件监听、自动扫描与后台同步。
- 修改、移动或删除 Vault Markdown。
- Obsidian 内部链接的真实检索和扩展。
- Session/User Memory 与 LangGraph Checkpointer。
- Hybrid Search、BM25、任务队列、MCP 和多智能体编排。

旧项目背景、已有 HTTP 契约和历史部署说明仍保留在 `RAG_PROJECT_HANDOFF.md`，但若其中的独立服务复用建议与本文冲突，以本文为准。
