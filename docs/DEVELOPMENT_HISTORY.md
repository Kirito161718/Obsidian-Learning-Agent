# 开发历程

Obsidian Learning Agent 不是一次性生成的演示项目。它从一个企业文档 RAG MVP 开始，按“每个阶段只解决一组问题、保留回滚路径、通过测试后再合并”的方式逐步演进。

本仓库保留了阶段分支、Feature commit、Merge commit、提交日期和提交信息。即使公开版本对个人路径和提交邮箱做了匿名化处理，提交拓扑和工程演进顺序仍保持不变。

## 演进原则

- 每个阶段从独立 Feature Branch 开始，经定向测试和全量回归后合并；
- 数据安全、引用真实性和人工审批优先于功能数量；
- 新能力优先复用已有 Service、Registry、Policy 和 LangGraph；
- 评估候选与生产 Profile 隔离，不用一次实验直接覆盖生产配置；
- 设备、模型和外部服务失败允许降级，但不能突破数据边界；
- Vault、真实评测集、密钥、运行数据和模型文件不进入 Git。

## 阶段记录

| 日期 | 阶段 / 分支 | 主要成果 | 主要验证 |
| --- | --- | --- | --- |
| 2026-07-16 | 基线迁移 | 将原企业文档 RAG 迁移为独立的 Obsidian Learning Agent 工程，统一数据库配置和 Alembic | Python 导入、数据库连接、基础 API 与 pytest |
| 2026-07-17 | `feature/langgraph-rag-foundation` | 将检索、条件查询改写、双路召回、Rerank、Context 和答案生成编排为 LangGraph | 节点路由、改写失败回退、检索兼容性 |
| 2026-07-18 | `feature/obsidian-vault-integration` / PR #1 | Vault 只读扫描、Markdown 结构化解析、增量索引和来源过滤 | 路径边界、Dry Run、Hash 增量、Qdrant 稳定性 |
| 2026-07-19 | `feature/local-learning-workspace` / PR #2 | 学习目标、状态、Session、上下文选择、连续问答和主动复盘 | CRUD、上下文隔离、Session 行为和前端交互 |
| 2026-07-20 | `feature/memory-context-tool-foundation` / PR #3 | User/Session/Run Memory、Context Bundle、Tool Registry、Policy、Executor 和 Audit | 风险分级、脱敏、只读执行和审计记录 |
| 2026-07-21 | `feature/read-only-mcp-integration` / PR #4 | 官方 MCP SDK、stdio Server/Client、HTTP 预留与统一工具策略 | 真实传输、配置校验、断连恢复和只读边界 |
| 2026-07-22 | `feature/official-source-audit-approval` / PR #5 | 官方来源 Snapshot、Evidence Proposal、持久化审批、版本和原子写回 | SSRF 防护、Diff、stale Hash、回滚和写入原子性 |
| 2026-07-23 | `feature/langsmith-observability` / PR #6 | RAG、学习、工具、MCP 与资料审计的全链路观测 | 脱敏、Safe Mode、Trace 关联和观测失败降级 |
| 2026-07-24 | `feature/rag-evaluation-optimization` | 固定数据集、检索指标、实验运行器、Profile 隔离和消融 | 数据集 Hash、重复实验、指标门禁和生产零污染 |
| 2026-07-24 | `feature/rag-safety-performance-hardening` / PR #7 | Evidence Sufficiency Gate、安全 Prompt、引用门和性能分阶段计时 | Calibration/Holdout、拒答率、误答率、引用与延迟 |
| 2026-07-25 | `codex/fix-agent-run-type-500` / PR #8 | 修复 RAG Profile 激活运行记录的枚举序列化问题 | API 定向回归和数据库序列化 |
| 2026-07-25 | `feature/reminders-and-ux` / PR #9 | 激活 `rag-safe-v1`，增加资料到期提醒、一键检查、首页和统一导航 | 只提示不抓取、显式触发、页面兼容和 Profile 验收 |
| 2026-07-26 | `feature/productization-finalization` / PR #10 | CUDA→CPU fail-open、模型与 Client 复用、安全预热、系统诊断和 Windows 一键运行 | CPU/CUDA 对比、PID 安全、接口脱敏、Dry Run 和全量回归 |

## 为什么采用这些阶段

### 先做可追踪，再做自动化

早期阶段先建立来源、Chunk、QA History、Run 和 Tool Audit。没有这些基础，后续的自动化越强，错误越难定位。

### 先隔离评估，再激活优化

RAG 候选 Profile 在独立数据集与运行记录中评估。只有通过 Calibration 与独立 Holdout，才允许创建候选；创建之后仍需用户显式激活，生产基线始终可回滚。

### 把“不会做什么”写进系统

项目明确拒绝模型自主写 Vault、后台自动抓取、无审批高风险工具、伪造引用和证据不足时强行回答。这些限制由代码、Schema、Policy 和测试共同实现，不只写在 Prompt 中。

### 产品化阶段不再扩张范围

最后阶段没有引入新的检索算法或模型，而是处理真实性能瓶颈、Windows 运维、状态诊断、资源生命周期和文档。这样可以在不扩大实验矩阵的前提下，让项目真正可运行、可演示、可排障。

## 如何阅读提交历史

建议按 `master` 的 first-parent 顺序查看：

```powershell
git log master --first-parent --reverse --oneline
```

查看包含 Feature Branch 的完整拓扑：

```powershell
git log master --graph --oneline --decorate --all
```

匿名化公开会改变 Commit SHA，但不会改变提交顺序、作者显示名、日期、提交信息、父子关系或 Merge commit。开发过程因此仍可通过提交图和本页完整复盘。

## 相关设计文档

- [Obsidian Vault 接入](OBSIDIAN_VAULT_INTEGRATION.md)
- [学习工作台](LOCAL_LEARNING_WORKSPACE.md)
- [Memory、Context 与工具基础](MEMORY_CONTEXT_TOOL_FOUNDATION.md)
- [只读 MCP 集成](READ_ONLY_MCP_INTEGRATION.md)
- [官方资料审计与审批](OFFICIAL_SOURCE_AUDIT_AND_APPROVAL.md)
- [LangSmith 可观测性](LANGSMITH_OBSERVABILITY.md)
- [RAG 评估](RAG_EVALUATION.md)
- [RAG 安全与性能](RAG_SAFETY_AND_PERFORMANCE.md)
- [本地运行与运维](LOCAL_OPERATIONS.md)
