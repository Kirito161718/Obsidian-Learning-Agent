# RAG 固定评估与实验

> 阶段 7E 在现有评估框架上增加 `obsidian-real-v2`、calibration/holdout、
> Answer/Abstain/Uncertainty 策略及冷/热性能剖析。详细设计和门禁见
> [RAG_SAFETY_AND_PERFORMANCE.md](RAG_SAFETY_AND_PERFORMANCE.md)。

## 事实来源与隐私边界

本地 YAML 数据集是评估事实来源。MySQL 只保存数据集版本、相对路径、Hash、计数、实验配置、聚合指标和 Case ID，不保存完整问题、答案、Prompt、Context 或 Chunk。真实数据集位于 `data/rag_evaluation/datasets/`，默认被 Git 忽略；仓库只提交 Schema、示例和测试 Fixture。

LangSmith 不是唯一数据源。只有 `share_with_langsmith: true`、`review_status: reviewed` 且未 stale 的 Case 才会同步，并且必须同时启用 LangSmith Safe Mode、输入隐藏和输出隐藏。

同步内容限于 Case ID、经审阅的公开技术问题、`should_answer`、简短参考要点和来源 ID 的稳定 Hash。Vault 路径、笔记正文、完整 Context、Session Memory、Secret 和未经审阅的 Case 不会上传，也不会创建公开分享链接。远端发布失败时，本地实验继续完成且不会重跑 RAG Case。

当前安全子集使用远端数据集名 `obsidian-learning-agent-rag-eval-v1`。已验证 Retrieval Baseline 实验可在 LangSmith 中查看；文档不保存其私有链接或输入输出正文。

## 数据集 Schema

数据集使用 `schema_version: rag-eval-v1`。每个 Case 至少包含：

```yaml
id: rag-case-001
category: single_note
question: "..."
should_answer: true
requires_rewrite: false
source_scope:
  type: obsidian
  note_ids: []
relevance_judgments:
  - note_id: "..."
    heading_path: ["...", "..."]
    relevance_grade: 3
expected_answer_points: ["..."]
acceptable_citation_note_ids: ["..."]
forbidden_citation_note_ids: []
tags: ["single-note"]
share_with_langsmith: false
review_status: reviewed
```

无答案 Case 必须使用 `should_answer: false`，且不得声明正相关来源、参考答案点或可接受引用。需要改写的 Case 必须提供 `conversation_context`。相关性主键是 `note_id + heading_path`，Chunk ID 仅作调试字段。

Validator 检查唯一 Case ID、Schema、Grade 范围、No-answer 契约、Note 是否存在或 missing、Heading 是否仍存在、同步审阅状态和稳定内容 Hash。任何 stale Case 都不会静默参加实验。

## 固定真实数据集

当前本地版本为 `obsidian-real-v1`，共 29 个已审阅 Case：

| 类别 | 数量 |
| --- | ---: |
| 单笔记事实 | 6 |
| 多笔记综合 | 5 |
| 多轮指代/Rewrite | 5 |
| 内部链接 | 5 |
| 无答案/拒答 | 4 |
| 冲突或不确定 | 4 |

校验结果为 0 stale、4 个 LangSmith 安全 Case，固定内容 Hash 为 `d3b42e195b8c4720493553a08ce38cf014d5c580f473585dce636d3d86bac4d0`。真实数据集及其问题、答案要点和笔记引用不得提交 Git，也不得写入最终公开报告。

## 指标定义

检索指标在去重后的 `note_id + heading_path` 排名上计算：

- `Hit Rate@k`：前 k 条是否至少命中一个正相关判断。
- `Recall@k`：前 k 条命中的唯一正相关判断数 / 全部正相关判断数。
- `Precision@k`：前 k 条已返回结果中，首次匹配正相关判断的结果数 / 实际返回数。
- `MRR`：第一个正相关结果排名的倒数。
- `NDCG@k`：用 `relevance_grade` 计算 DCG，再除以同一 Ground Truth 的理想 DCG。

无 Ground Truth 时，上述检索指标返回 unavailable，而不是伪造 0 分。空结果的 Precision 为 0；Note 级 Ground Truth 只记一次，不会因同一 Note 的多个 Chunk 重复得分。

Rewrite 记录期望/实际触发、决策正确性、原查询命中、改写查询命中、双路收益和延迟。Rerank 记录前后 Hit、NDCG、相关排名变化和延迟。

引用指标：

- `citation_format_validity`：引用序号是否与答案中的 `[来源N]` 一致。
- `citation_source_validity`：引用是否来自本次检索结果且不在禁止来源中。
- `citation_precision`：已引用来源中，被标注为可接受来源的比例。
- `citation_recall`：可接受来源中被实际引用的比例。
- `unsupported_citation_count`：缺少检索支持或 Ground Truth 支持的引用数。

拒答指标包括 `answerability_accuracy`、`no_answer_accuracy`、`false_answer_rate` 和 `false_refusal_rate`。性能记录总耗时、检索、Rewrite、Rerank、LLM 耗时、p50、p95、模型调用数和 Rewrite 调用数。Token 不可用时写 `unavailable`，不以字符数替代。

## Runner 与隔离

Runner 直接调用生产 `RagWorkflow`，不复制第二套 RAG。`evaluation_mode` 会绕过 QA History 和 Learning Session 写入；评估用对话上下文仅存在于当前 Case。默认串行，支持 Retrieval-only、Full RAG、Category、Case ID、固定种子、重复运行、取消和失败后继续。

中断实验可按实验 ID 恢复。成功 Case 不重跑，环境故障导致的失败 Case会原位重试，不新增重复记录。

```powershell
python -m app.rag_evaluation.cli validate-dataset --file obsidian-real-v1.yaml
python -m app.rag_evaluation.cli run --dataset-file obsidian-real-v1.yaml --profile rag-baseline-v1 --mode retrieval
python -m app.rag_evaluation.cli run --dataset-file obsidian-real-v1.yaml --profile rag-baseline-v1 --mode full
python -m app.rag_evaluation.cli compare <experiment-a> <experiment-b>
```

## Baseline Profile

`rag-baseline-v1` 是优化前生产快照，不会被实验覆盖：

| 参数 | 当前值 |
| --- | --- |
| Chunk | `markdown-structural-v1` |
| Collection | `rag_chunks` |
| Retrieval | dense |
| 原查询 / 改写查询召回 | 25 / 25 |
| 最终 Top K | 5 |
| Rewrite / 双路召回 | 开 / 开 |
| Rerank | 开，Top 5 |
| 单 Note Chunk 上限 | 3 |
| 内部链接扩展 | 关（生产节点仍为 placeholder） |
| Context 上限 | 12000 字符 |
| No-answer 分数阈值 | 0.45 |

基线建立时生产 Collection 共 3696 Points，其中 Obsidian 3685 Points。Obsidian Chunk 字符数为：min 3、mean 208.45、median 143、p95 576.60、max 3568。该分布与基线失败分析中的短 Chunk、边界丢失问题一起决定后续候选，不预先证明任何优化有效。

## 消融 Profile

固定消融包括 `no-rewrite`、`original-only`、`no-rerank`、`no-link-expansion` 和 `no-memory-rewrite`。每个 Profile 都保存完整配置和 SHA-256，且不修改 Active Profile。

当前生产内部链接扩展本来就是关闭状态，因此 `no-link-expansion` 与 Baseline 配置 Hash 相同；这一消融用于明确记录“当前组件未启用”，不得把运行波动误报为链接扩展收益。

首轮 Retrieval-only 结果如下；这些单次运行用于组件诊断，不作为候选晋级的最终重复实验：

| Variant | Recall@10 | NDCG@10 | p95 ms | 结论 |
| --- | ---: | ---: | ---: | --- |
| Baseline（校正记录） | 0.340 | 0.299 | 35133 | 7B 检索基线 |
| No Rewrite | 0.360 | 0.327 | 35025 | 未显示 Rewrite 的稳定收益 |
| Original Only | 0.360 | 0.327 | 35437 | 与 No Rewrite 同结果，双路收益需谨慎解释 |
| No Rerank | 0.360 | 0.305 | 2371 | 质量未下降且延迟显著降低，Rerank 存在反转案例 |
| No Link Expansion | 0.440 | 0.371 | 36323 | 配置与 Baseline 相同，差异属于运行波动，不能视为链接收益 |
| No Memory Rewrite | 0.340 | 0.293 | 42897 | 未显示评估对话 Memory 的稳定收益 |

基线失败分类包括 15 个 `retrieval_miss`、9 个 `chunk_boundary_loss`、15 个 `chunk_too_small`、5 个 `rerank_inversion` 和 3 个 `missing_link_context`。后续候选必须由这些失败类型驱动，并在隔离 Collection/Profile 中重新评估。

## 7C 候选与隔离索引

7B 的失败证据包括 15 个 `chunk_too_small`、9 个 `chunk_boundary_loss`、3 个 `missing_link_context` 和 5 个 `rerank_inversion`。因此 7C 实现以下候选：

- `markdown-structural-v2`：保留 Heading Path、结构位置、Chunking Version、稳定 Parent/Structural/Sibling ID；代码围栏合法闭合，拆表时重复表头，长列表按完整列表项拆分；只有两个相邻、同级、同一 H1 下且都低于最小长度的 Section 才允许合并。
- Link Expansion V2：只从初始 Note 扩展一跳；每个初始 Note 最多两个链接目标；链接候选进入同一个 Rerank，不递归；统一按 Chunk ID 去重，并记录 `link_origin_note_id` 和 `expansion_reason`。
- Neighbor Expansion：只在 Rerank 后取同笔记前后各一个 Chunk，优先同一 Heading；邻居没有伪造的向量分数或 Rerank 分数，并记录扩展来源；Context 字符和单 Note Chunk 上限继续生效。

Chunk V2 写入隔离 Collection `rag_eval_7c_chunk_v2_d3b42e195b8c`，从未覆盖 `rag_chunks`。构建前要求 Vault 文件 Hash 与 MySQL 已索引 Hash 完全一致；中途失败会删除不完整 Collection。构建仅只读 Vault，不更新 Note、QA History、Session 或生产向量。

| Chunk 指标 | V1 `rag_chunks` | V2 Shadow |
| --- | ---: | ---: |
| Chunk 总数 | 3685 | 3191 |
| 每 Note 平均 Chunk | 45.49 | 39.40 |
| 字符 p50 / p90 / p95 | 143 / 431.6 / 576.6 | 214 / 458 / 616.5 |
| 低于 200 字比例 | 65.43% | 44.59% |
| 高于 1800 字比例 | 0.11% | 0.13% |
| 无标题比例 | 1.55% | 1.79% |
| 合并多个 Heading 比例 | 0% | 15.48% |
| 重复 Chunk 比例 | 1.36% | 1.19% |
| 相邻文本重叠均值 | 6.48% | 6.22% |

Shadow 向量由本机已有 CUDA 运行时加速生成；同一生产 Chunk 用该运行时重嵌入后，与已存向量的余弦相似度为 `1.0`，最大绝对维度差为 `1.53e-7`。模型仍为同一 BGE-M3，评测与 Rerank 仍使用项目 `.venv` 和原生产参数。

MMR 未入选：Baseline 重复 Chunk 仅 1.36%，没有明显的高重复证据。Parent-Child 未入选：V2 与受限邻居扩展已直接覆盖边界上下文问题，先避免引入运行时 Vault Parent 读取。Hybrid 的 Feasibility Decision 为“不实现”：当前 `EmbeddingService` 只通过 SentenceTransformers 产生 dense vector，未安装或验证 BGE-M3 sparse/lexical 输出链路；虽然 Qdrant 支持 Named Sparse Vector 与 RRF/DBSF，但不满足可可靠生成 sparse 表示这一前置条件，因此继续使用 Dense Retrieval，不以字符串匹配伪装 Hybrid，也不下载新模型。

7C 单变量和有限组合的 Retrieval-only 初筛结果如下：

| 候选 | Recall@10 | NDCG@10 | p95 ms | 7C 决策 |
| --- | ---: | ---: | ---: | --- |
| 7B Baseline | 0.340 | 0.299 | 35133 | 对照 |
| Chunk V2 | 0.440 | 0.391 | 42005 | 第一候选；初筛含 1 条暂停前 CPU Case，延迟仅诊断用 |
| Link Expansion V2 | 0.440 | 0.371 | 15839 | 第二候选 |
| Neighbor Expansion | 0.380 | 0.313 | 8550 | 拒绝；multi-note Recall 降至 0.20 |
| Chunk V2 + No Rerank | 0.400 | 0.311 | 7821 | 拒绝；rewrite 类 Recall 降至 0.20 |
| Chunk V2 + Link V2 | 0.440 | 0.364 | 17979 | 拒绝；未超过 Chunk V2 |

Chunk V2 将 `retrieval_miss` 从 15 降到 12、`chunk_too_small` 从 15 降到 9、`chunk_boundary_loss` 从 9 降到 7、`missing_link_context` 从 3 降到 2。它达到 Recall/NDCG 主提升门槛，但尚未经过统一运行时三次重复、Full RAG 引用与 no-answer 保护检查，因此 7C 不激活任何候选。

初筛加速器只替换 Torch 运行时，继续使用 `.venv` 的 SentenceTransformers 3.3.1。相同三组 CrossEncoder 输入的 CPU/CUDA 排序完全一致，分数最大差约 `1.2e-7`；系统 SentenceTransformers 5.5.1 因排序不一致被拒绝。7D 会让最终 Baseline 与前两名候选使用同一兼容运行时重复，初筛 p95 不用于跨运行时晋级判断。

## Full RAG 基线

校正后的 Full RAG 基线实验为 `ola-rag-baseline-v1-full-corrected-v1`，29/29 Case 成功，且所有 4 个 no-answer Case 均由完整问答链执行。主要结果如下：

| 指标 | 结果 |
| --- | ---: |
| Recall@10 | 0.420 |
| NDCG@10 | 0.359 |
| p95 总延迟 | 48504 ms |
| 平均 Rerank 延迟 | 20382 ms |
| 平均 LLM 延迟 | 8348 ms |
| Citation Precision / Recall | 0.659 / 0.560 |
| Answerability Accuracy | 0.862 |
| False Answer Rate | 0.138 |
| no-answer 类别 Accuracy / False Answer Rate | 0.000 / 1.000 |

这组结果是 7C/7D 的保护基线，不表示当前系统质量达标。尤其是 4 个 no-answer Case 全部错误作答，任何候选配置都不得以牺牲拒答安全来换取检索分数；最终晋级必须同时检查引用、拒答、错误回答、模型调用与延迟。

## 7D 三轮复测与决策

Baseline、Chunk V2、Link V2 在同一 CUDA-compatible Runtime、同一数据集 Hash 和 Seed 下分别执行 3 轮 Retrieval-only 与 Full RAG。每个实验均为 29 Case × 3 轮且最终 87/87 成功。报告同时保留逐题标准差与三轮整轮聚合的 `*_repeat_mean`、`*_repeat_stddev`，晋级只使用后者。

| Profile | Retrieval Recall@10 | Retrieval NDCG@10 | Full Citation P / R | Full p95 repeat mean |
| --- | ---: | ---: | ---: | ---: |
| Baseline | 0.4400 ± 0.0000 | 0.3725 ± 0.0025 | 0.7038 ± 0.0344 / 0.6000 ± 0.0283 | 18419.0 ± 4716.5 ms |
| Chunk V2 | 0.4400 ± 0.0000 | 0.3774 ± 0.0061 | 0.7021 ± 0.0542 / 0.6000 ± 0.0432 | 13668.1 ± 1829.8 ms |
| Link V2 | 0.4400 ± 0.0000 | 0.3708 ± 0.0000 | 0.7496 ± 0.0355 / 0.6400 ± 0.0283 | 18484.8 ± 3236.3 ms |

两个候选均未达到 Recall@10 或 NDCG@10 至少提升 0.02 的主门槛。更重要的是，三种 Profile 的 no-answer 类别三轮共 12 次评估全部错误作答，类别 Accuracy 为 0、False Answer Rate 为 1；Link V2 还在该类别出现 unsupported citations。因此 7D 不晋级任何候选，`rag-baseline-v1` 保持唯一 Active Profile，两个候选标记为 `rejected` 且 `promotion_eligible=false`。

完整实验 ID、均值/标准差、候选理由、绝对安全否决、回滚和后续条件见 [`RAG_OPTIMIZATION_DECISION.md`](RAG_OPTIMIZATION_DECISION.md)。

## 报告、API 与清理

每个完成实验在 `reports/rag_evaluation/<experiment-id>/` 生成 `summary.json`、`metrics.csv`、`failure_analysis.json` 和 `report.md`。报告不含完整问题、答案、Prompt、Context、Chunk、Secret 或绝对路径。

页面为 `GET /agent/rag-evaluation`，API 覆盖数据集、实验、2～6 实验对比、Profile、报告、失败类型和清理 Dry Run。

```http
POST /agent/rag-evaluation/maintenance/cleanup
Content-Type: application/json

{"dry_run": true}
```

清理会保护生产 Collection、Baseline、Active Profile、运行中实验和被引用报告。不会删除 Vault、QA History、Session、用户数据集、官方资料或 LangSmith 远端数据。

## 官方参考

- [LangSmith evaluation concepts](https://docs.langchain.com/langsmith/evaluation-concepts)
- [LangSmith evaluate / aevaluate](https://docs.langchain.com/langsmith/evaluate-llm-application)
- [LangSmith local evaluation](https://docs.langchain.com/langsmith/local)
- [LangSmith experiment configuration](https://docs.langchain.com/langsmith/experiment-configuration)
- [Qdrant hybrid and multi-stage queries](https://qdrant.tech/documentation/search/hybrid-queries/)
