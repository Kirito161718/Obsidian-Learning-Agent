# 资料提醒与核心页面

## 提醒不是后台自动检查

资料提醒只读取 `official_sources` 中的时间和用户设置，计算哪些来源建议重新检查。打开首页、打开官方资料页或启动应用都不会访问官方站点，也不会生成 Proposal。

只有用户点击单个“检查更新”或“一键检查到期来源”后，浏览器才会调用现有的：

```http
POST /official/sources/{source_id}/audit
```

系统没有 APScheduler、后台抓取任务或应用启动时自动 Audit。

## 建议检查时间

每个来源默认启用提醒，默认间隔为 30 天，可手动设置为 1–365 天。建议时间不保存为额外字段，而是动态计算：

```text
next_suggested_check_at = last_checked_at + check_interval_days
```

状态包括：

- `disabled`：来源或提醒已关闭。
- `never_checked`：从未手动检查。
- `current`：尚未到建议检查时间。
- `due`：已到建议时间，但未超过逾期宽限期。
- `overdue`：超过建议时间和统一配置的宽限期。
- `error`：上次检查失败。

逾期宽限期由 `OFFICIAL_REMINDER_OVERDUE_GRACE_DAYS` 集中配置，默认 7 天。

## 一键检查到期来源

官方资料页会读取提醒汇总，并在用户点击后按页面显示顺序逐个调用单来源 Audit API。一次最多处理 10 个，单个来源失败不会中断后续来源，页面会显示进度和最终汇总。关闭页面后不要求任务继续。

批量操作没有新增后端抓取接口，不并发请求，也不会自动批准任何 Proposal。

## Audit、Proposal 与 Approval

Audit 复用阶段 6 的受控流程：

```text
Source → Fetch → Snapshot → Audit → Proposal → Approval
```

没有变化时不会创建重复 Snapshot。需要更新时只生成待审阅 Proposal；只有用户加载 Diff、明确确认高风险操作并通过 Approval 后，既有 Apply 流程才可能修改笔记。因此提醒和检查本身都不会自动写回 Vault。

## 首页和导航

首页 `/` 只显示面向用户的核心状态：当前 RAG Profile、已索引笔记数、待检查来源、待审批 Proposal、索引异常和最近运行状态。快捷入口可进入学习工作台、Vault 扫描、到期资料检查和 Proposal 审阅。

主导航固定显示“首页 / 学习工作台 / 官方资料”。Agent 控制台、RAG 评估、LangSmith、MCP、Tool Audit、Run History 和调试后台收纳在“高级功能”中。

## `rag-safe-v1` 激活与回滚

生产 Profile 必须通过现有 Profile Service 或 `/agent/rag-evaluation/profiles/{profile_id}/activate` 受控激活，不能直接修改数据库。激活要求评估完成并满足晋级条件，且记录回滚 Profile。需要回滚时，用相同受控激活能力重新激活 `rag-baseline-v1`，并提供准确的目标版本确认。
