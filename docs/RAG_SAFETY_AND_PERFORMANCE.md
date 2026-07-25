# RAG 拒答安全与性能硬化

## 范围

阶段 7E 只处理证据不足误答、冲突资料的过度确定回答、引用失效和 RAG 延迟诊断。它不引入新的检索技术、Chunk、Embedding、Reranker 或外部搜索服务，也不修改生产 Qdrant Collection。

生产基线 `rag-baseline-v1` 保留为回滚目标。安全候选依次为：

- S0：现有 Baseline；
- S1：Evidence Gate；
- S2：Evidence Gate + `rag-answer-prompt-v2-safety`；
- S3：Evidence Gate + Safety Prompt + Post Citation Gate。

候选按 S1 → S2 → S3 逐级评估。S3 的 Post Citation Gate 若导致 False Refusal 超过门禁，则回退到已通过门禁的最后一个候选。最终候选通过独立 holdout 的安全、检索、引用、性能和模型调用门禁后，系统才创建 `rag-safe-v1`。创建不等于激活；激活仍需用户在评估页明确确认。

## 数据集与校准

`obsidian-real-v2` 是本地私有固定数据集，包含 43 个 Case，并分成 calibration 与 holdout。两组都包含正常回答、拒答和冲突不确定性问题。真实数据文件位于被 Git 忽略的运行目录，所有 v2 Case 均禁止同步到 LangSmith。

Calibration 只用于有限阈值搜索。每个候选由候选存在性、Top Score、Top-k Mean Score、来源覆盖、Context 有效性、检索一致性和来源范围共同决定，不能单独依赖 Top Score。

选定结果保存数据集 Hash、配置 Hash、特征版本和阈值。Holdout 只做一次最终验证，不能反向参与搜索。晋级逻辑还明确拒绝“全部拒答”的候选。

## 三种回答策略

`answer` 保留原有生成流程；`abstain` 在生成前返回统一安全模板，不调用 DeepSeek，不保留引用，也不写入 QA History；`answer_with_uncertainty` 允许一次正常生成，但 Prompt 要求明确指出冲突或不足，并分别引用冲突来源。

Evidence Gate 是确定性的，只读取检索结果与 Context 统计，不读取模型生成答案。安全决策的公开输出包括 Policy、置信区间、原因码、校准版本和特征值。

## 引用门

生成后检查引用数量、格式、来源是否存在、来源是否属于当前 Context、无支持引用数以及答案是否包含实质内容。启用 Post Citation Gate 后：

- 正常答案没有有效引用会降级为安全拒答；
- 引用当前 Context 外的来源会降级；
- 不确定性答案没有覆盖至少两个来源会降级；
- 降级答案不会写入 QA History；
- 不额外调用 LLM 修复。

这个门只验证引用归属和当前上下文约束，不声称执行了完整的逐事实语义蕴含验证。

## 性能诊断

每个 Case 记录冷/热标记和分阶段时间：`embedding_initialization_ms`、`reranker_initialization_ms`、`query_embedding_ms`、`qdrant_original_ms`、`rewrite_llm_ms`、`qdrant_rewritten_ms`、`merge_ms`、`rerank_ms`、`link_expansion_ms`、`context_build_ms`、`answer_llm_ms`、`citation_validation_ms`、`persist_ms`、`total_ms`。

报告分别计算 cold 与 warm 的 p50/p95、阶段均值和最慢 10 个 Case ID，避免把一次模型加载混入稳定运行延迟。Embedding、Reranker、Qdrant Client 和编译后的 RAG Workflow 均在进程内复用。

可选预热配置：

```env
MODEL_PREWARM_ENABLED=true
MODEL_PREWARM_ON_STARTUP=false
RERANKER_DEVICE=auto
RERANKER_BATCH_SIZE=8
```

默认不在启动时预热。显式预热只加载本地模型并运行无用户正文的最小向量/排序探针，不调用 DeepSeek，不读完整 Vault，不写 QA History 或 Qdrant；失败为 fail-open，可从 `/health/rag`、`/system/status` 或评估 API 查看状态。旧的 `RAG_MODEL_PREWARM_*` 环境变量继续兼容。短时 Query Cache 未启用，因为只有在实验显示收益且能正确处理 Profile、Collection、Filter 与重建失效时才允许启用。

## API 与操作

- `POST /agent/rag-evaluation/safety/calibrate`
- `POST /agent/rag-evaluation/safety/validate`
- `GET /agent/rag-evaluation/safety/status`
- `GET /agent/rag-evaluation/performance/profile?experiment_id=...`
- `POST /agent/rag-evaluation/performance/prewarm`
- `GET /agent/rag-evaluation/performance/prewarm/status`
- `POST /system/models/prewarm`
- `GET /system/status`

评估页只显示拆分数量、策略配置、安全指标、冷/热延迟、阶段耗时和门禁状态。它不显示完整问题、答案、Context、Note 路径，也不允许手填未校准阈值。

## 门禁与回滚

核心 holdout 门禁是 No-answer Accuracy ≥ 0.75、False Answer Rate ≤ 0.25、Answerability Accuracy ≥ 0.85、回答型问题 False Refusal Rate ≤ 0.10。检索和引用质量下降不得超过约定限值，Warm Full RAG p95 增幅不得超过 10%，模型调用次数不得增加。

回滚只需在评估页重新激活 `rag-baseline-v1`。不删除候选、不重建 Collection、不修改 Vault，也不回写实验问答。

## 本次实测结果

数据集 `obsidian-real-v2` 共 43 个 Case，Calibration 32 个（18 answer、8 abstain、6 uncertainty），Holdout 11 个（6 answer、3 abstain、2 uncertainty），Hash 为 `3a16cde3875ac433a9b13e7b6aabe41c2f0a810970621fec5c5b877396fcb8a6`。所有 Case 均设置 `share_with_langsmith=false`，本地文件受 Git 忽略。

S0 的无答案类 No-answer Accuracy 为 0、False Answer Rate 为 1。有限 Calibration 搜索得到的 S1/S2 均达到 No-answer Accuracy 1、False Answer Rate 0、False Refusal Rate 0、Answerability Accuracy 1；S2 使用温度 0 的安全 Prompt。S3 虽保持 No-answer Accuracy 1 和 False Answer Rate 0，但回答型问题 False Refusal Rate 为 0.2917，超过 0.10 门禁，因此拒绝启用 Post Citation Gate。

最终 S2 Holdout 的 No-answer Accuracy、Answerability Accuracy 均为 1，False Answer Rate、False Refusal Rate 均为 0，Answer Rate 为 0.7273，不存在全部拒答。检索与引用质量相对同一 Holdout 基线的差值均为 0。模型调用均值从 1.0909 降到 0.8182；无答案 Case 在生成前拒答，不调用答案模型。

最终安全 Profile 是 `rag-safe-v1`，配置为 Evidence Gate + Safety Prompt，`post_citation_gate_enabled=false`；阶段 8 已经人工验收并激活。阶段 9 进一步让所有 `answer_with_uncertainty` 回答都执行当前 Context 多来源引用门，即使通用 Post Citation Gate 未开启也不例外；失败时输出不伪造来源的安全不确定说明。

单轮实测的主要性能瓶颈是 CPU CrossEncoder Rerank：S0 平均约 52.35 秒，答案模型约 6.57 秒，Query Embedding 约 0.41 秒，Qdrant 原始查询约 0.18 秒。最终 Holdout 的 Full RAG p50/p95 为约 15.52/35.26 秒；同一门禁计算中的 warm p95 从基线约 128.79 秒降到候选约 35.22 秒。实现了 Embedding、Reranker、Qdrant Client 和编译 Workflow 的进程内复用，以及可选 fail-open 本地模型预热；未启用证据不足的 Query Cache，也未为追求速度缩小候选集。

按用户要求，为缩短收尾时间，额外的三轮全量复验在基线完成 62 个 Case Run 时终止，实验状态为 `cancelled`。阶段门禁采用已完成的 S0、32-Case Calibration、11-Case 独立 Holdout、自动化测试和数据稳定性核对结果。

稳定性核对前后完全一致：Vault 81 个 Markdown、聚合 Hash `fcefb363cbe870b7544244d61d60add94db94361d1eff45f5c6365c2b3a41bae`；数据库 81 条笔记、24 条 QA History、3 个 Learning Session；生产 `rag_chunks` 仍为 3696 个 Point。

## 阶段 9 设备实测

阶段 9 使用 `obsidian-real-v2` 中 4 个可回答 Case 和 1 个无答案 Case，只执行一次 Embedding/Qdrant 召回，并把每个 Case 相同的 20 个候选分别交给 CPU 与 CUDA Reranker；模型、batch 8 和最终 Top-5 均相同，不调用 DeepSeek，不写业务数据。

- Embedding 冷初始化约 16.50 秒，Query Embedding 均值约 405 毫秒，Qdrant 均值约 130 毫秒；
- CPU 初始化约 3.32 秒，三轮热 Rerank 均值约 12.253 秒/Case；
- CUDA 初始化约 5.63 秒，三轮热 Rerank 均值约 395 毫秒/Case；
- CUDA 热耗时降低 96.78%，峰值显存约 2.33 GB，无 OOM；
- 5/5 Case 的 Top-5 排序完全一致；
- batch 8 因而保留，不盲目增大；模型与 `rag-safe-v1` 质量参数不变。

硬件和 CUDA 构建满足条件时，`RERANKER_DEVICE=auto` 选择 CUDA；加载或评分失败会自动重载 CPU。当前项目 `.venv` 若安装的是 CPU-only Torch，`auto` 会安全选择 CPU，不会因检测到物理显卡而错误启用 CUDA。完整操作见 `docs/LOCAL_OPERATIONS.md`。

标准 `.venv`（CPU 回退）还执行了 3 个可回答问题和 1 个无答案问题的最小 Full RAG 验证。首个冷 Case 总耗时约 37.33 秒，其中 Embedding 初始化约 15.75 秒、Reranker 初始化约 1.93 秒、Query Embedding 约 0.44 秒、Qdrant 约 0.08 秒、热外 Rerank 约 10.58 秒、DeepSeek 约 7.34 秒。预热后的三轮共 12 次运行平均总耗时约 15.06 秒，Query Embedding/Qdrant/Rerank/DeepSeek 均值分别约 0.19/0.07/9.32/5.47 秒。无答案 Case 三轮均在 LLM 前 `abstain`；测试使用 `evaluation_mode` 且关闭 LangSmith 外发，QA 数和 Qdrant points 前后不变。
