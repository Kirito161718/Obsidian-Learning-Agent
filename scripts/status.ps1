[CmdletBinding()]
param()

. (Join-Path $PSScriptRoot "common.ps1")

$process = Get-ProjectFastApiProcess
Write-Check "FastAPI" $(if ($null -ne $process) { "HEALTHY" } elseif (Test-TcpPort 8000) { "FOREIGN_PROCESS" } else { "STOPPED" }) $(if ($null -ne $process) { "pid=" + $process.ProcessId } else { "Project process is not running." })

$mysql = Get-Service -Name "MySQL80" -ErrorAction SilentlyContinue
Write-Check "MySQL80" $(if (Test-TcpPort 3306) { "HEALTHY" } else { "UNAVAILABLE" }) ("service=" + $(if ($null -eq $mysql) { "not installed" } else { $mysql.Status }))
Write-Check "Qdrant" $(if (Test-TcpPort 6333) { "HEALTHY" } else { "UNAVAILABLE" }) "port=6333"

$snapshot = $null
if ($null -ne $process) {
    try {
        $snapshot = Invoke-RestMethod -Uri "http://127.0.0.1:8000/system/status" -TimeoutSec 35
    } catch {
        $snapshot = $null
    }
}
if ($null -eq $snapshot) { $snapshot = Get-SystemSnapshot }
if ($null -eq $snapshot) {
    Write-Check "Diagnostics" "UNAVAILABLE" "Safe status probe failed."
    exit 1
}

Write-Check "Vault" $snapshot.vault.status ("markdown_files=" + $snapshot.vault.markdown_files)
Write-Check "Embedding" $snapshot.embedding.status "Model contents are not inspected."
Write-Check "Reranker" $snapshot.reranker.status ("device=" + $snapshot.reranker.active_device + "; configured=" + $snapshot.reranker.configured_device)
Write-Check "Active RAG Profile" $(if ($snapshot.active_rag_profile) { "HEALTHY" } else { "DEGRADED" }) $(if ($snapshot.active_rag_profile) { $snapshot.active_rag_profile } else { "not set" })
Write-Check "LangSmith" $snapshot.langsmith.status ("configured=" + $snapshot.langsmith.configured + "; safe_mode=" + $snapshot.langsmith.safe_mode)
