# 官方资料校验、审批与版本化写回

## 安全定位

阶段 6 首次允许修改已有 Obsidian Markdown，但入口不是任意文件写入：用户必须先手动登记公开官方来源，再手动关联数据库中已索引的笔记，并显式触发抓取、校验和整批审批。网页、PDF 和模型输出始终是不可信数据，不能改变系统规则、审批风险、目标路径或写入范围。

当前链路为：

```text
Source -> Snapshot -> Link -> Proposal -> Approval -> Note Version -> 原子写入 -> 单笔记 Reindex
```

- **Source** 固定 canonical URL 与 allowed host；有快照后不能修改 URL，需新建来源并禁用旧来源。
- **Snapshot** 保存标准化正文的 SHA-256、HTTP 元数据和项目相对 gzip 路径；MySQL 不保存完整正文。
- **Link** 只关联现有 `obsidian_notes.id` 与可选标题范围，不接受绝对路径。
- **Proposal** 保存结构化编辑的摘要、证据清单、Hash 与项目相对 artifact 路径，不在 MySQL 保存完整笔记。
- **Approval** 是一次性 `HIGH_RISK` 授权，绑定完整 Proposal payload hash，只能全部接受或全部拒绝。
- **Note Version** 在写入前保存 Before Content、实际 diff 和 After Hash；当前不提供自动恢复。

## 抓取、标准化与快照

默认只允许 HTTPS。仅测试或显式本地开发配置可访问 `127.0.0.1`/`localhost` HTTP；不支持 Cookie、Authorization、登录站点、浏览器渲染或反爬绕过。URL 统一拒绝凭据、未知协议、非登记 Host、HTTPS 降级、跨 Host 重定向和超过重定向上限的请求。

每次连接前解析 DNS，所有结果都必须是允许的公网地址；连接使用已验证 IP，同时保留原 Host 的 TLS SNI、证书校验与 Host Header，避免 DNS rebinding。私有、回环、链路本地、保留、组播、云元数据和其他非公网地址均拒绝。响应采用流式大小限制与 Content-Type allowlist，不自动发送 Cookie、认证或代理凭据。

HTML 会移除脚本、样式、导航、隐藏与非正文节点，再保留标题、列表、表格和代码结构；Markdown/文本严格按 UTF-8；PDF 只提取有文本的有限页数，不为图片 PDF 伪造内容。标准化结果生成稳定章节 ID、manifest 和 SHA-256。ETag 与 Last-Modified 用于条件请求，304 或相同内容 Hash 均不会创建重复 Snapshot。

默认每个 Source 保留最新 5 个未被 Proposal 引用的 Snapshot；清理只能手动 Dry Run 后 Apply。

## 基线与增量校验

首次抓取得到基线 Snapshot；用户手动 Audit 时，系统加载登记来源、当前/上一快照、启用的 Note Link 和当前笔记 Hash。来源内容无变化时不会调用 LLM。内容变化时先产生确定性 diff，再只向 LLM 提供限长、标记为不可信的数据。

LLM 只能返回受 Schema 约束的 `replace`、`insert_before`、`insert_after` 或 `append_section` 操作。每项操作必须引用当前 Snapshot 中精确存在的 Evidence excerpt 与 section ID，并且只能落在 Link 允许的标题范围。目标必须精确匹配一次；不允许整篇替换、Frontmatter 修改、超出操作数/Item 数/变更比例上限或破坏代码围栏、表格结构。完整统一 diff 必须在审批前展示。

## 审批与持久化恢复

Proposal 构建完成后，官方更新 LangGraph 创建一次性 Approval 并在 `interrupt()` 处暂停。`thread_id` 固定为 `proposal_id`。阶段 6 使用实现官方 `BaseCheckpointSaver` 接口的 SQLAlchemy/MySQL Saver；表结构由 Alembic `20260721_0007` 管理。实现按 `langgraph==1.2.9` 与 `langgraph-checkpoint==4.1.1` 校验，Checkpoint 只保存 ID、相对路径、Hash、状态和紧凑统计，不保存网页、笔记、Prompt 或 Proposal 正文。

进程重启后，客户端用同一 Proposal ID 读取 pending 状态；服务端校验用户提交的 payload hash、`confirm_all=true` 与固定确认文本，再以 `Command(resume=...)` 恢复。决策与 Resume 外层使用按 Proposal 派生的 MySQL named lock，确保多 Worker 并发只执行一次；连接中断会自动释放，允许后续请求从 Checkpoint 恢复。过期、拒绝、已消费、Hash 不符或重复决策均不能执行。阶段 6 不支持部分审批，也不允许模型或 MCP 自主批准。

## 写入、回滚、版本与索引

Apply 会在任何写入前重新读取所有目标笔记并核对原始字节 SHA-256。任意一篇变化都会把整批标为 `stale`，不写任何文件、不建部分版本、不 Reindex；必须重新 Audit。

校验通过后，系统先为全部目标创建临时文件和 Note Version，再逐个原子替换。若中途失败，会用已创建的 Before Version 反向恢复已经替换的文件，并清理临时文件；回滚异常会留下稳定错误状态供人工处理。Frontmatter、UTF-8 BOM 与原换行风格保持不变。

文件写入成功后只调用现有单笔记 Reindex。索引失败不回滚已经获批的 Markdown，而是标为 `applied_with_index_error` 并提供显式重试；重试不会再次写笔记或创建 Version。默认每篇笔记保留最新 10 个未固定 Version，`pinned=true` 不自动清理。

## API、页面与当前边界

`/official` 提供 Source、Link、Fetch、Snapshot Preview、Audit、Proposal Diff、整批 Approve/Reject、Version Diff、Reindex Retry 和 Cleanup。所有抓取、校验、决策、应用与清理均由用户显式触发；应用启动不会自动联网或写 Vault。

当前不支持定时检查、后台抓取、自动批准、部分批准、私有登录资料、浏览器 JavaScript 渲染、自动发现官方来源、任意 URL/路径/SQL/Shell 工具、自动恢复旧版本或修改 `.obsidian/`。阶段 5 的 MCP Server 继续只暴露六个只读工具；阶段 6 的写入能力不通过 MCP 暴露。
