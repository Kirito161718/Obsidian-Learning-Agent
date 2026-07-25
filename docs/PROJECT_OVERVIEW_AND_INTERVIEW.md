# 项目总览与面试材料

## 项目解决的问题

Obsidian Learning Agent 把分散的本地笔记、上传文档、学习目标、会话记忆和用户明确登记的官方资料组织成一套可审计的学习工作台。它解决的重点不是“让模型知道更多”，而是让回答有本地证据、有引用、证据不足时拒答，且高风险写回必须经过用户审批。

## 核心架构

```text
Web / API
  -> FastAPI Router
  -> LangGraph RAG / Official Update Workflow
  -> Domain Services
     -> Embedding + Qdrant + BGE Reranker + DeepSeek
     -> Memory / Context Bundle / Tool Registry
     -> Proposal / Approval / Note Version
  -> MySQL + Qdrant + Obsidian Vault
  -> optional safe LangSmith traces
```

FastAPI 负责接口和页面；MySQL 保存业务状态与审计记录；Qdrant 保存 Chunk 向量；Vault 始终是 Markdown 内容事实源。Embedding、Reranker、Qdrant Client 和编译后的 RAG Graph 在进程内复用。

## LangGraph RAG 工作流

工作流依次初始化状态、加载用户偏好/学习上下文/当前 Session Memory、执行原始查询检索、判断是否需要改写、合并候选、有限链接扩展、Rerank、邻居补全、构建 Context、执行 Evidence Sufficiency Gate，再进入回答、引用校验与持久化。

`retrieve` 在 Context 后结束，不调用答案模型；`ask` 只有在证据门禁允许时调用 DeepSeek。无答案 Case 在 LLM 前拒答；冲突 Case 只能输出不确定结论，并必须引用当前 Context 中至少两个不同来源。

## Obsidian 增量索引

扫描器只处理允许的 Markdown，排除 `.obsidian`、归档、模板和受控更新目录。每篇笔记保存内容 Hash；Hash 未变则跳过，变更时只替换该笔记对应的 Qdrant 版本。Chunk 带稳定来源 ID、相对路径、标题路径、行号、结构与兄弟关系元数据。删除和重建按精确 note/version 范围执行，不重建生产 Collection。

## Memory 分层

- User Preferences：用户手动设置的语言、深度、风格和引用偏好；
- Explicit Learning Context：用户明确选择的目标和学习状态；
- Session Memory：当前 Session 的有限最近问答；
- Knowledge Context：本轮从 Qdrant 召回且可引用的唯一事实来源。

前三层只能影响表达或指代消解，不能充当事实证据，也不能产生 `[来源N]`。

## Tool Registry 与三级风险

工具必须先注册，再经过 Policy 和 Executor。READ_ONLY 可由用户显式调用；WRITE_REVERSIBLE 需要更强确认和审计；WRITE_HIGH_RISK 必须生成 Approval，并在执行前再次校验对象状态。模型目前不能自主挑选工具，避免把“工具存在”误当成“模型获准执行”。

## MCP

项目使用官方 MCP SDK 支持项目内只读 Server 和受控 Client。远端工具进入同一 Registry、Policy、Executor 与 Audit，不绕过本地风险规则。应用不自动连接 Server，不开放 MCP 写工具，并限制输入、输出、Schema、工具数量与超时。

## LangSmith

LangSmith 是可选外部 Trace，不是本地审计事实源。安全模式隐藏输入输出，并在 Metadata 层再次去除问题、答案、Context、绝对路径和 Secret。关闭、缺 Key、网络失败或 Flush 超时时业务均 fail-open。

## RAG 评估、消融与 Evidence Gate

固定数据集覆盖单笔记、多笔记、需要改写、无答案和冲突问题。实验使用隔离 Profile/Collection，比较 Recall、NDCG、MRR、引用、拒答、延迟和模型调用。消融用于判断 Query Rewrite、Rerank、Chunk v2、链接/邻居扩展的真实收益。

Evidence Sufficiency Gate 组合候选数、Top Score、Top-k Mean、来源覆盖、检索一致性和 Context 有效性，输出 `answer`、`abstain` 或 `answer_with_uncertainty`。它是确定性的，不让 LLM 自行决定证据是否足够。

## 官方资料 Proposal、审批与写回

官方页面只在用户点击后抓取受限公开来源。系统保存 Snapshot、生成结构化 Proposal 和 Diff，但不直接写 Vault。高风险 Apply 必须有未过期 Approval，并在写前重新核对所有目标笔记 Hash；整批写入失败时回滚。写前保存 Note Version，成功后只重建受影响笔记索引。

## 当前性能优化

阶段 9 保留 `BAAI/bge-reranker-v2-m3` 和 `rag-safe-v1` 的质量参数，只优化生命周期和设备：

- 模型、Qdrant Client、RAG Graph 单例复用；
- `RERANKER_DEVICE=auto`，CUDA 加载/推理失败自动回退 CPU；
- batch 8 在本机 8 GB 显存下峰值约 2.33 GB；
- 同候选 CPU/CUDA Top-5 排序 5/5 一致；
- CUDA 热 Rerank 较 CPU 快约 96.78%；
- 手动预热不接触用户正文、不调用 DeepSeek、不写业务数据；
- 未增加 Query Cache，避免失效、隐私和 Profile 过期问题。

## 工程取舍

- 不做 Hybrid/MMR/Parent-Child：当前瓶颈是 CPU Reranker，不应以新算法扩大验证矩阵。
- 不自动迁移数据库：一键启动优先保证可预测和可恢复。
- 不自动抓取或后台调度：官方资料更新必须由用户触发。
- 不把所有状态塞入 Prompt：Memory 分层并设置字符预算。
- 不为速度更换模型或缩小质量参数：设备优化必须保持排序质量。
- 不新增持久化 Query Cache：隐私、失效和跨 Profile 一致性成本高于当前收益。
- 不额外接入 Skill：现有固定 Prompt 与文档已清楚，静态 Skill 没有足够收益。

## 可展示的技术难点

1. 把“证据不足”从 Prompt 愿望变成生成前的确定性门禁。
2. 让冲突回答既表达不确定性，又只能引用当前 Context。
3. 在 Qdrant、MySQL、Vault 三份状态之间实现精确增量更新和失败回滚。
4. 将 MCP 工具纳入统一风险策略，而不是直接暴露给模型。
5. 让 CUDA 优化 fail-open：OOM 或加载失败只降速，不让 RAG 宕机。
6. Windows PID 管理只停止本项目进程，不误杀端口占用者。
7. 在可观测性中保留调试价值，同时禁止正文和 Secret 外发。

## 面试问答

### 1. 为什么用 LangGraph，而不是在 Router 里顺序调用？

RAG 包含改写条件、拒答条件、冲突策略、引用门和持久化条件。LangGraph 把状态与分支显式化，便于逐节点测试和观测；Router 只处理协议与错误映射。

### 2. 如何避免模型在无答案时“编一个合理答案”？

生成前先运行 Evidence Gate。候选为空、得分/均值不足、来源范围不匹配或 Context 无效时直接 `abstain`，不调用 DeepSeek，也不写 QA History。

### 3. 引用 `[来源N]` 为什么可信？

编号由 Context Builder 根据本轮选中的 Chunk 生成。生成后只接受当前 `context_sources` 中存在的编号；冲突答案还要求至少两个不同来源。校验失败会降级，不会补造引用。

### 4. CUDA OOM 会怎样？

Reranker 捕获 CUDA 加载或评分异常，释放 CUDA 引用并在同一请求内重载 CPU。若 CPU 也失败，则保留 Qdrant 原始排序。应用启动和 RAG 服务不因 CUDA 失败而中断。

### 5. 为什么不用 Query Cache？

答案受 Active Profile、Collection、过滤范围、Vault 版本、Session Context 和用户偏好影响。正确失效成本高，还会增加隐私面；当前瓶颈已由 Reranker 设备优化直接解决。

### 6. 官方资料为什么不直接自动写回 Obsidian？

外部页面是不可信输入，模型 Diff 也可能错误。Proposal、Approval、写前 Hash、Note Version 和整批回滚把高风险写入变成用户可审阅、可拒绝、可恢复的过程。

### 7. 如何证明优化没有降低质量？

CPU/CUDA 使用相同问题、相同 20 个候选、相同 batch 和 Top-5。5 个 Case 的排序完全一致，CUDA 无 OOM，热耗时降低 96.78%；模型和 `rag-safe-v1` 质量参数均未改变。

## 2～5 分钟项目介绍

这是一个面向个人本地知识库的 Obsidian Learning Agent。它的核心目标不是做一个普通聊天机器人，而是把本地 Markdown、上传文档、学习目标和官方资料变成一套有证据、可引用、可审计、可恢复的学习系统。

后端使用 FastAPI，业务数据和审计放在 MySQL，Chunk 向量放在 Qdrant，Obsidian Vault 保持为内容事实源。RAG 用 LangGraph 编排：先加载有限的用户偏好、学习上下文和 Session Memory，再做原始查询检索；召回不足时才调用 Query Rewrite，随后合并候选、Rerank、构建带 `[来源N]` 的 Context。生成前还有确定性的 Evidence Sufficiency Gate：证据不足直接拒答，不调用大模型；来源冲突时只能给不确定回答，并分别引用当前 Context。

我还把工具系统做成 Registry、Policy、Executor 和 Audit 四层，并把 MCP 工具纳入同一套只读风险策略。官方资料更新走另一条受控 LangGraph：用户手动抓取，系统生成 Proposal 和 Diff，只有审批通过且写前 Hash 未变化才原子写回 Vault，并保存版本用于恢复。

项目后期重点解决工程化。Embedding、Reranker、Qdrant Client 和编译后的 Graph 都按进程复用；Reranker 支持 `auto/cpu/cuda`，CUDA 加载或运行失败自动回退 CPU。本机同候选实测 CUDA 热 Rerank 比 CPU 快约 96.78%，Top-5 排序完全一致。Windows 脚本可以诊断、一键启动、查看状态和精确停止，只管理本项目 PID，不启动 Docker MySQL，也不会误杀其他进程。整个设计的主线是：模型能力可以 fail-open，但数据边界、引用真实性和高风险写入不能靠运气。
