# Obsidian Vault 只读接入与结构化索引

## 安全边界

Obsidian Vault 是只读数据源。系统仅在用户显式调用扫描或索引接口时读取 `.md` 文件，不创建文件监听器、定时任务或后台队列，也不会修改、移动、复制或删除 Vault 中的任何文件。`.obsidian`、隐藏/系统目录、软链接和配置排除目录不会进入扫描范围。

所有对外路径均为使用 `/` 的 Vault 相对路径。Vault 绝对路径只存在于进程内配置和扫描器内部，不写入 Qdrant payload、日志或问答历史；状态接口也只返回 Vault 目录名。

## 配置

```dotenv
OBSIDIAN_ENABLED=false
OBSIDIAN_VAULT_PATH=
OBSIDIAN_INCLUDE_PATTERNS=*.md,**/*.md
OBSIDIAN_EXCLUDE_DIRS=.obsidian,.trash,Attachments,Templates,Archive,_ai_updates
OBSIDIAN_EXCLUDE_PATHS=Learning/_drafts
OBSIDIAN_MAX_FILE_BYTES=2097152
OBSIDIAN_CHUNK_MIN_CHARS=200
OBSIDIAN_CHUNK_TARGET_CHARS=1200
OBSIDIAN_CHUNK_MAX_CHARS=1800
OBSIDIAN_ATOMIC_BLOCK_HARD_MAX_CHARS=6000
```

`OBSIDIAN_VAULT_PATH` 必须是存在的绝对目录。默认关闭集成；启用后也不会自动扫描。

## 扫描状态

扫描器只读取 UTF-8 或 UTF-8 BOM Markdown，并为文件计算 SHA-256：

- `new`：MySQL 尚无对应相对路径。
- `changed`：当前文件哈希与 `indexed_hash` 不同。
- `unchanged`：当前文件哈希与 `indexed_hash` 相同。
- `missing`：MySQL 有记录，但 Vault 中已不存在。
- `skipped`：命中排除规则、过大、隐藏或链接不安全。
- `error`：单文件读取或解码失败；不会中断其他文件。

`POST /obsidian/scan` 是 dry-run，不修改 MySQL、Qdrant 或 Vault。

## Markdown 解析与结构化切分

解析器支持安全 YAML Frontmatter、H1～H6、段落、列表、围栏代码、表格、引用、Callout、WikiLink、嵌入、Frontmatter tags 和正文 tags。无效 Frontmatter 只产生 warning，正文仍继续解析。

切分优先级为标题路径、结构块、段落、句子和最终字符兜底。同一 chunk 不跨越 heading 边界。普通代码、列表、引用和 Callout 尽量保持原子；超出硬上限的代码按行拆分并重新闭合围栏；长表格按行拆分并重复表头。

每个 chunk 包含稳定 `chunk_id`、`parent_section_id`、`content_hash`、相对路径、标题路径、行号、块类型、tags 和 links。文件版本哈希参与 `chunk_id`，使变更版本可与旧版本区分。

## 增量一致性

- 新笔记：解析 → 切分 → Embedding → 写入新 Qdrant Point → 更新 MySQL。
- 未变笔记：不解析、不 Embedding、不写 Qdrant。
- 已变笔记：先完整写入新版本，再删除旧版本，最后更新 MySQL。
- 新版本处理中失败：尽力清理新版本，保留旧 `indexed_hash` 与旧向量，并记录错误。
- 缺失笔记：删除当前 Obsidian 向量、标记 `missing`，保留 MySQL 元数据记录。

MySQL `obsidian_notes` 不保存 Markdown 正文。Qdrant 继续使用现有 collection 和 Embedding 模型，不为 Obsidian 创建新数据库或新集合。

## API

```text
GET  /obsidian/status
POST /obsidian/scan
POST /obsidian/index
GET  /obsidian/notes?status=&path_prefix=&tag=
GET  /obsidian/notes/{note_id}
GET  /obsidian/notes/{note_id}/chunks
POST /obsidian/notes/{note_id}/reindex
```

`/admin` 提供以上只读查看和显式索引操作，不提供 Vault 文件编辑或删除操作。

## RAG 来源范围

`/rag/retrieve` 与 `/rag/ask` 可传：

```json
{
  "source_types": ["uploaded_document", "obsidian_note"],
  "source_path_prefixes": ["Knowledge/RAG"]
}
```

默认检索全部来源。`document_id` 只过滤上传文档；`source_path_prefixes` 只匹配 Obsidian 相对路径，并拒绝绝对路径与 `..`。旧上传 Point 没有 `source_type` 时按 `uploaded_document` 处理。

Obsidian Context 会显示“Obsidian 笔记”、标题、相对路径和可读标题路径。API sources 同时返回通用来源字段、heading path、section、行号和 tags；`document_id` 对 Obsidian 为 `null`。

## 当前不包含

本阶段不实现双向链接扩展、Vault 写回、自动扫描、后台任务、全文正文入库、学习状态自动更新、MCP、Skill 或新的 LangGraph 节点。`expand_links_placeholder` 仍保持占位，不执行链接扩展。
