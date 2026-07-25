# 本地运行与运维

## 运行关系

标准 Windows 运行方式是：

- Windows 服务 `MySQL80` 提供 3306 端口；
- Docker Desktop 只运行 Compose 服务 `qdrant`，提供 6333/6334；
- FastAPI 在项目 `.venv` 中本地运行，提供 8000；
- `scripts/start.ps1` 绝不会启动 Compose 中的 `mysql` 服务。

Compose 文件保留完整开发拓扑，但一键脚本只执行 `docker compose up -d qdrant`。不要在 MySQL80 已占用 3306 时执行完整的 `docker compose up`。

## 首次配置

1. 安装 Python、MySQL 8 和 Docker Desktop。
2. 创建并激活虚拟环境，安装 `requirements.txt`。
3. 将 `.env.example` 复制为 `.env`，填写 MySQL、DeepSeek、Vault 等本机配置。不要提交 `.env`。
4. 确认 MySQL80 已启动，随后手动执行一次：

   ```powershell
   alembic upgrade head
   ```

5. 执行只读诊断：

   ```powershell
   .\scripts\doctor.ps1
   ```

若 PowerShell 的本机策略阻止脚本，可只为当前进程使用 `Set-ExecutionPolicy -Scope Process Bypass`；不需要管理员权限或永久更改系统策略。

## 一键启动、状态与停止

```powershell
.\scripts\start.ps1
.\scripts\status.ps1
.\scripts\stop.ps1
```

启动成功后访问：

- 首页：`http://127.0.0.1:8000/`
- 学习工作台：`http://127.0.0.1:8000/learning`
- 官方资料：`http://127.0.0.1:8000/official`
- Agent 控制台：`http://127.0.0.1:8000/agent`
- RAG 评估：`http://127.0.0.1:8000/agent/rag-evaluation`
- 详细诊断：`http://127.0.0.1:8000/system/status`

FastAPI PID 只写入 `runtime/pids/fastapi.pid`。停止脚本会核对 PID、命令行中的 `uvicorn`、`app.main:app` 和项目路径；PID 指向其他进程时只清理无效 PID 文件，不停止该进程。

默认停止不会影响 MySQL80、Qdrant 或 Docker Desktop。只有显式执行以下命令才会停止 Qdrant：

```powershell
.\scripts\stop.ps1 -StopQdrant
```

## 模型设备与预热

推荐配置：

```env
RERANKER_DEVICE=auto
RERANKER_BATCH_SIZE=8
MODEL_PREWARM_ENABLED=true
MODEL_PREWARM_ON_STARTUP=false
```

`auto` 只在当前 Python 环境的 Torch 确认可用且模型测试推理成功时选择 CUDA；CUDA 加载或运行失败会自动释放 CUDA 引用并重载到 CPU，RAG 继续可用。显卡驱动可用并不等于虚拟环境安装了 CUDA Torch，可用下面的命令核验：

```powershell
.\.venv\Scripts\python.exe -c "import torch; print(torch.__version__, torch.cuda.is_available())"
```

手动安全预热不会调用 DeepSeek、读取整个 Vault、写 QA History 或修改 Qdrant：

```powershell
.\scripts\start.ps1 -Prewarm
```

也可在应用运行后调用 `POST /system/models/prewarm`。状态为 `not_loaded`、`loading`、`ready` 或 `error`；关闭或未启用时为 `disabled`。预热失败为 fail-open。

本机 8 GB RTX 5060 Laptop GPU 的固定 5 Case、每 Case 20 个相同候选实测中，batch 8 峰值约 2.33 GB，Top-5 排序 5/5 完全一致，无 OOM。CUDA 热 Rerank 约 0.395 秒/Case，CPU 约 12.253 秒/Case。项目 `.venv` 若仍为 CPU-only Torch，生产 `auto` 会安全选择 CPU。

## 常见错误

- `Port 8000 is occupied`：脚本不会杀掉占用端口的其他程序。先识别并自行处理占用者。
- `MySQL80 connection is unavailable`：确认 Windows 服务、3306 和 `.env`；脚本不会改启 Docker MySQL。
- `Database migration is not at head`：手动执行 `alembic upgrade head`，脚本不会擅自迁移。
- Qdrant 不可用：确认 Docker Desktop；启动脚本只尝试启动 `qdrant` 服务。
- 模型为 `not_loaded`：这是惰性加载的正常状态，可执行安全预热。
- CUDA 回退 CPU：检查虚拟环境的 Torch 是否为 CUDA 构建、显存是否充足；回退不会阻止应用启动。
- `/system/status` 显示降级：页面仍可打开；按 `doctor.ps1` 的非敏感结果逐项排查。

## 安全清理

默认命令只做 Dry Run：

```powershell
.\scripts\cleanup.ps1
```

确认列表后才执行：

```powershell
.\scripts\cleanup.ps1 -Apply
```

脚本只处理项目 `runtime/temp`、`runtime/cache`、超过保留期的 `runtime/logs`、过期 PID，以及应用已明确支持的普通 Run/Tool Audit 历史。它使用项目路径边界校验，不处理 Vault、Qdrant Collection、QA History、Session、Proposal、Approval、Note Version、官方当前快照、固定评估数据集、Active Profile 或 LangSmith 远程数据。

## 手动备份与恢复

自动备份系统不在当前范围。重要操作前建议分别备份：

1. 用 `mysqldump` 导出当前 MySQL 数据库；
2. 关闭写入后备份 Qdrant Docker volume，或使用 Qdrant Snapshot；
3. 使用文件系统快照或版本控制备份 Obsidian Vault；
4. 单独保存 `.env` 到受控的密钥存储，不放入代码备份。

恢复时先停止本项目 FastAPI，恢复 MySQL 与 Qdrant，核对 Vault，再运行 `alembic current`、`doctor.ps1` 和 `status.ps1`。不要把不同时间点的 Vault、MySQL 和 Qdrant 任意拼接；恢复后先做只读检索验证，再开放写入。
