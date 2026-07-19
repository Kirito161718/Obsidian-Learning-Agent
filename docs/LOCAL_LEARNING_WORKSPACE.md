# 本地学习工作台

## 作用与边界

`GET /learning` 提供独立的本地学习工作台。学习 Session 将显式选择的目标、学习状态、来源类型和 Obsidian 相对路径范围，与后续问答历史关联起来。页面默认按 `last_activity_at desc` 显示最近 10 个活动 Session；更早的数据不会自动删除，可通过 API 分页查看。

本阶段不实现自动 Memory、自动 Reflection、自动状态更新、定时任务或 Vault 写回。问答、检索和复盘都不会自动修改目标状态、掌握等级或 Markdown 文件。

## 显式学习上下文

用户在 Session 设置中明确选择一个目标和一个学习状态。Ask 加载的目标字段仅包括名称、类型、优先级、相关主题和有限描述；学习状态字段仅包括主题、状态、掌握等级、手动重要度、当前问题和项目相关性。一次请求显式传入的 ID 可临时覆盖 Session 选择，但不会改写 Session 设置。

学习上下文只调整解释深度与回答对当前目标的贴合程度，不是知识来源。Prompt 明确禁止把它作为 `[来源N]` 引用；事实结论仍只能来自检索到的知识库资料。上下文是可序列化的精简字典，总长度受 1200 字符上限约束，不包含 ORM 对象或完整数据库记录。

## 来源范围与两种操作

Session 可保存三种范围：全部来源、仅上传文档、仅 Obsidian。仅 Obsidian 时可以附加安全的 Vault 相对路径前缀；接口拒绝绝对路径、盘符、UNC 路径和 `..`。

- “问答”调用 `/rag/ask`，生成答案并写入 `qa_history`，同时保存 `session_id`、更新最后活动时间。
- “仅检索”调用 `/rag/retrieve`，可复用 Session 来源范围，但不生成答案、不写历史，也不需要把学习上下文送入模型。

## 引用与 Obsidian

答案中的 `[来源N]` 可定位到右侧 Source Card。Obsidian 来源只显示笔记标题、Vault 相对路径、标题路径、行号、Tags、Chunk 文本和检索分数。页面用 Vault 名称与相对路径构造 URL Encode 后的：

```text
obsidian://open?vault=<vault_name>&file=<encoded_relative_path>
```

上传文档不显示此按钮。页面和 API 不返回 Vault 绝对路径，也不会修改 Markdown。

## 手动状态与笔记关联

工作台的状态、掌握等级、手动重要度、当前问题和项目相关性编辑区只有在点击“保存学习状态”时才调用现有 `PATCH /learning-status/{id}`。

“将本次引用笔记关联到当前学习状态”会收集当前 Sources 中 `obsidian_note` 的相对路径，去重后与 `related_note_refs` 合并，再由用户点击触发 PATCH。该操作不写入 Markdown，也不会在 Ask 后自动执行。

## 主动学习复盘

用户点击“生成本次学习复盘”才调用：

```text
POST /learning/sessions/{session_id}/review
GET  /learning/sessions/{session_id}/review
```

复盘最多使用 `LEARNING_SESSION_REVIEW_MAX_QA`（默认 20）组最近问答，并只向模型提供问题、答案、来源名称与标题层级、无答案标记，以及当前显式目标和状态。它不包含完整 Chunk、完整 Prompt、绝对路径或完整 State。

输出由 Pydantic 验证为知识总结、已覆盖主题、待解决问题和下一步行动。每个 Session 只有一份当前复盘，再次生成执行更新；复盘只保存到 MySQL，不进入 Qdrant，不产生 Markdown，也不修改学习状态。
