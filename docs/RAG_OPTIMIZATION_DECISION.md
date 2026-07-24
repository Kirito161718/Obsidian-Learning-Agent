# RAG 优化决策记录

## 决策范围与数据边界

本记录覆盖阶段 7B、7C、7D。所有候选都来自固定数据集上的 Baseline 失败证据；没有修改正式 Vault，也没有覆盖生产 Qdrant Collection。

- 数据集：`obsidian-real-v1`
- Schema：`rag-eval-v1`
- Case：29，覆盖 single-note、multi-note、conversational-rewrite、internal-link、no-answer、conflicting-sources
- 数据集 Hash：`d3b42e195b8c4720493553a08ce38cf014d5c580f473585dce636d3d86bac4d0`
- stale：0；可安全上传 LangSmith 的审阅子集：4
- 生产 Profile：`rag-baseline-v1`
- 生产 Collection：`rag_chunks`
- V2 Shadow：`rag_eval_7c_chunk_v2_d3b42e195b8c`

真实问题、答案要点、Vault 路径和正文不进入本文或 Git。LangSmith 只接收 4 个显式审阅、安全、未 stale 的 Retrieval-only Case；最终三轮实验只保存在本地。

## 7B：基线和消融

`rag-baseline-v1` 是优化前生产快照：Dense BGE-M3、按需改写与双路召回、BGE Rerank、每 Note 最多 3 个 Chunk、12000 字符 Context；内部链接和邻居扩展关闭，Chunk 版本为 `markdown-structural-v1`。

校正后的单轮基线：

| 模式 | Recall@10 | NDCG@10 | Citation P/R | no-answer 类 Accuracy | 整体 False Answer | p95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Retrieval-only | 0.340 | 0.299 | — | — | — | 35133 ms |
| Full RAG | 0.420 | 0.359 | 0.659 / 0.560 | 0.000 | 0.138 | 48504 ms |

失败证据包括 15 个 `retrieval_miss`、15 个 `chunk_too_small`、9 个 `chunk_boundary_loss`、5 个 `rerank_inversion` 和 3 个 `missing_link_context`。4 个 no-answer Case 全部被错误回答。

| 消融 | Recall@10 | NDCG@10 | p95 ms | 结论 |
| --- | ---: | ---: | ---: | --- |
| No Rewrite | 0.360 | 0.327 | 35025 | 无稳定正收益证据 |
| Original Only | 0.360 | 0.327 | 35437 | 双路收益需谨慎解释 |
| No Rerank | 0.360 | 0.305 | 2371 | 延迟改善大，但质量保护不足 |
| No Link Expansion | 0.440 | 0.371 | 36323 | 与 Baseline 配置 Hash 相同，差异是运行波动 |
| No Memory Rewrite | 0.340 | 0.293 | 42897 | 无稳定收益 |

消融是单轮诊断，不单独作为晋级依据。

## 7C：证据驱动候选

Chunk V2 只合并相邻、同级、同一 H1 下且均低于最小长度的 Section，并保护代码围栏、表头和完整列表项。它只写入 Shadow Collection：

| Chunk 指标 | V1 | V2 Shadow |
| --- | ---: | ---: |
| Chunk 总数 | 3685 | 3191 |
| 每 Note 平均 Chunk | 45.49 | 39.40 |
| 字符 p50 / p90 / p95 | 143 / 431.6 / 576.6 | 214 / 458 / 616.5 |
| 低于 200 字比例 | 65.43% | 44.59% |
| 重复 Chunk 比例 | 1.36% | 1.19% |

Link V2 只扩展一跳，每个初始 Note 最多两个目标，扩展结果统一去重并进入同一次 Rerank。Neighbor 只在 Rerank 后补同 Note 相邻 Chunk，不伪造相关度分数。

| 候选 | Recall@10 | NDCG@10 | 7C 初筛结论 |
| --- | ---: | ---: | --- |
| Chunk V2 | 0.440 | 0.391 | 第一候选 |
| Link V2 | 0.440 | 0.371 | 第二候选 |
| Neighbor | 0.380 | 0.313 | 拒绝；multi-note Recall 0.20 |
| Chunk V2 + No Rerank | 0.400 | 0.311 | 拒绝；rewrite Recall 0.20 |
| Chunk V2 + Link V2 | 0.440 | 0.364 | 拒绝；未超过单独 Chunk V2 |

MMR 未实现，因为 Baseline 重复 Chunk 只有 1.36%。Parent-Child 暂缓，避免引入运行时 Vault Parent 读取。Hybrid 不可行：当前 EmbeddingService 没有经过验证的 sparse 输出；不以字符串匹配伪装 Hybrid，也不下载新模型。

Shadow 使用本机已有 CUDA Runtime 构建。同一生产 Chunk 重嵌入的余弦相似度为 `1.0`，最大绝对维度差为 `1.53e-7`。最终评测使用项目 SentenceTransformers 3.3.1；CPU/CUDA CrossEncoder 排序一致，最大分数差约 `1.2e-7`。

## 7D：统一运行时三轮复测

下表均为 29 Case × 3 轮，所有实验 87/87 成功。`±` 后为三轮整轮聚合值的总体标准差，而不是逐题标准差。一次 DeepSeek 连接故障产生的 25 个失败键已在同一 Baseline Full RAG 实验中精确重试并覆盖，最终失败数为 0。

### Retrieval-only

| Profile | 实验 ID | Recall@10 | NDCG@10 | 轮均 p95 |
| --- | --- | ---: | ---: | ---: |
| Baseline | `24477395-cce0-482c-ae69-c1b6ae1e2815` | 0.4400 ± 0.0000 | 0.3725 ± 0.0025 | 15806.8 ± 4946.5 ms |
| Chunk V2 | `b72dd7bd-41c8-48a7-a553-9dbfea34b9cb` | 0.4400 ± 0.0000 | 0.3774 ± 0.0061 | 10310.0 ± 1437.1 ms |
| Link V2 | `7914dc7c-944e-440b-976f-6e1e41b4acb9` | 0.4400 ± 0.0000 | 0.3708 ± 0.0000 | 13893.7 ± 4848.8 ms |

Chunk V2 的 NDCG 相对 Baseline 只增加 0.0049，Link V2 反而下降 0.0017；两者都没有达到 Recall 或 NDCG 至少提升 0.02 的主晋级门槛。

### Full RAG

| Profile | 实验 ID | Recall@10 | NDCG@10 | Citation P / R | 整体 False Answer | 轮均 p95 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Baseline | `374484a9-a73d-462b-8826-b256c977b686` | 0.4133 ± 0.0189 | 0.3522 ± 0.0094 | 0.7038 ± 0.0344 / 0.6000 ± 0.0283 | 0.1379 ± 0.0000 | 18419.0 ± 4716.5 ms |
| Chunk V2 | `00f65b14-a128-44c3-8fc4-7c818fca2153` | 0.4200 ± 0.0283 | 0.3583 ± 0.0141 | 0.7021 ± 0.0542 / 0.6000 ± 0.0432 | 0.1379 ± 0.0000 | 13668.1 ± 1829.8 ms |
| Link V2 | `7ed224f1-aa7a-4376-807e-98e0305f6a2e` | 0.4267 ± 0.0189 | 0.3624 ± 0.0119 | 0.7496 ± 0.0355 / 0.6400 ± 0.0283 | 0.1379 ± 0.0000 | 18484.8 ± 3236.3 ms |

三者的整体 Answerability Accuracy / no-answer Accuracy 都是 0.8621，但这是所有类别的平均。必须单独检查 no-answer 类别：

| Profile | no-answer Case Runs | 类别 Accuracy | 类别 False Answer | 其他保护问题 |
| --- | ---: | ---: | ---: | --- |
| Baseline | 12 | 0.000 | 1.000 | 所有无答案问题均被回答 |
| Chunk V2 | 12 | 0.000 | 1.000 | 未修复拒答 |
| Link V2 | 12 | 0.000 | 1.000 | 未修复拒答，并出现 unsupported citations |

## 最终决策

**不晋级任何候选，保持 `rag-baseline-v1` 为唯一 Active Profile。**

原因：

1. Chunk V2 和 Link V2 均未达到 Recall@10 或 NDCG@10 至少提升 0.02 的主门槛。
2. Chunk V2 的 Full RAG citation precision 从 0.7038 小幅降至 0.7021，没有形成明确质量优势。
3. Link V2 提升引用指标，但 Retrieval-only NDCG 未提升，Full RAG 检索增益也未达门槛，延迟与 Baseline 接近。
4. 两个候选都没有修复 no-answer 类别 0% accuracy / 100% false-answer 的绝对安全失败；Link V2 还在该类别产生 unsupported citations。

数据库中 `rag-candidate-chunk-v2-v1` 与 `rag-candidate-link-v2-v1` 已标记为 `rejected`，`promotion_eligible=false`，没有执行激活 API。Shadow Collection 保留用于复现实验；生产 `rag_chunks` 未修改。

## 晋级、回滚与后续条件

未来候选必须至少满足 Recall@10 或 NDCG@10 提升 0.02，同时：

- Citation Precision 下降不超过 0.01；
- no-answer Accuracy 不下降且必须通过绝对安全检查；
- False Answer Rate 不增加；
- p95 增长不超过 20%；
- 不无理由增加模型调用；
- single/multi/rewrite/internal-link/conflicting/no-answer 分类无严重退化。

Profile 激活必须经过显式确认 API，评测脚本不会自动激活。未来若有候选通过并被人工激活，其 `rollback_profile_id` 必须指向 `rag-baseline-v1`；回滚只重新激活基线，不删除 Shadow、不修改 Vault、不回写 QA History。

下一轮优化应先修复 answerability/no-answer 判定，再基于新的固定数据集版本重新做 7B 式基线和候选门禁。需要重建索引时，先在新的 `rag_eval_*` Collection 完整构建与验证，禁止原位重建 `rag_chunks`，禁止同一实验混用两个 Collection。
