# 学习目标、学习状态与重要指数

## 1. 范围与边界

当前模块服务于单用户本地系统，不建立用户表、登录、权限或租户字段。目标和学习状态只接受用户通过 `/goals`、`/learning-status` 或本地 `/admin` 页面的明确 CRUD 操作。

以下来源禁止自动修改学习状态：

- RAG 问答与模型输出。
- Query Rewrite、引用验证和评估结果。
- 问答历史、文档阅读或上传记录。
- 后台推断、定时任务或自动学习计划。

当前数据不会加入 RAG Prompt，也不会改变现有 LangGraph 工作流。

## 2. 学习目标

数据表：`learning_goals`。

| 字段 | 含义 |
| --- | --- |
| `id` | UUID 字符串 |
| `name` | 1～200 字符的非空名称 |
| `goal_type` | `job`、`learning`、`project`、`skill` |
| `description` | 可选纯文本描述 |
| `priority` | 1～5，默认 3 |
| `status` | `active`、`paused`、`completed` |
| `related_topics` | JSON 字符串数组 |
| `created_at` / `updated_at` | 创建和更新时间 |

接口：

```text
POST   /goals
GET    /goals?goal_type=learning&status=active
GET    /goals/{goal_id}
PATCH  /goals/{goal_id}
DELETE /goals/{goal_id}
```

暂停、恢复、完成和优先级修改都通过 `PATCH` 完成。删除是物理删除，并在同一数据库事务中从所有学习状态的 `related_goal_ids` 中清除该 ID。

## 3. 学习状态

数据表：`learning_status`。

| 字段 | 含义 |
| --- | --- |
| `id` | UUID 字符串 |
| `topic` | 1～200 字符的非空主题 |
| `status` | `not_started`、`learning`、`practicing`、`mastered`、`paused` |
| `mastery_level` | 0～5，默认 0 |
| `manual_importance` | 1～5，默认 3 |
| `current_problem` | 用户手动填写的可选文本 |
| `related_goal_ids` | 已存在目标 ID 的 JSON 数组 |
| `related_note_refs` | 预留 Obsidian 笔记路径的 JSON 数组，当前不验证存在性 |
| `project_relevance` | 是否与当前项目直接相关 |
| `created_at` / `updated_at` | 创建和更新时间 |

掌握等级含义：

```text
0 未接触
1 知道基本概念
2 能看懂简单示例
3 能独立实现基础功能
4 能解决项目级问题
5 能设计、优化并解释方案取舍
```

接口：

```text
POST   /learning-status
GET    /learning-status?status=learning&min_mastery=1&max_mastery=4&related_goal_id=<id>
GET    /learning-status/{status_id}
PATCH  /learning-status/{status_id}
DELETE /learning-status/{status_id}
```

创建或更新 `related_goal_ids` 时会验证所有目标真实存在。学习状态删除为物理删除。

## 4. 重要指数

重要指数不持久化，在列表和详情查询时根据最新目标和学习状态实时计算：

```text
Importance = 0.45G + 0.30D + 0.15M + 0.10R
```

分量归一化规则：

```text
G = 活动关联目标最高优先级的 (priority - 1) / 4；无活动目标为 0
D = 1 - mastery_level / 5
M = (manual_importance - 1) / 4
R = project_relevance 为 true 时取 1，否则取 0
```

暂停和已完成目标不贡献 `G`。多个活动目标只取最高优先级。

返回字段：

```json
{
  "importance_score": 0.7675,
  "importance_stars": 4,
  "importance_breakdown": {
    "goal_relevance": 0.75,
    "mastery_gap": 0.6,
    "manual_importance": 1.0,
    "project_relevance": 1.0
  }
}
```

星级边界采用左开右闭的确定性区间，0 分仍至少显示 1 星：

```text
0.00～0.20  → 1 星
>0.20～0.40 → 2 星
>0.40～0.60 → 3 星
>0.60～0.80 → 4 星
>0.80～1.00 → 5 星
```

目标优先级或状态变化后，下一次查询会立即反映新分数，不维护冗余分数字段或评分历史。

## 5. 本地管理页面

`/admin` 包含“学习目标”和“学习状态”区域：

- 目标支持新增、编辑、删除、暂停、恢复、完成、优先级和关联主题维护。
- 学习状态支持新增、编辑、删除、目标多选、掌握等级、手动重要度、当前问题、项目相关性和笔记引用维护。
- 学习状态列表显示实时分数、星级，并可展开查看四个评分分量。

页面沿用 Jinja2、原生 JavaScript 和现有 CSS，不包含生产级权限控制。

## 6. 后续 Obsidian 关联

`related_note_refs` 已允许保存类似 `Knowledge/RAG/Query Rewrite.md` 的字符串。后续接入 Vault 时，可以把这些引用解析为真实 Note 来源，并基于笔记主题建立人工确认的关联。

当前不会扫描 Vault、验证文件是否存在、移动 Markdown 文件或将目标写入笔记。后续即使接入 Obsidian，也应保持学习状态由用户确认后修改，避免模型把推断当作真实掌握程度。
