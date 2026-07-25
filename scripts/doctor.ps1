[CmdletBinding()]
param()

. (Join-Path $PSScriptRoot "common.ps1")

$failed = $false
$python = Get-ProjectPython
if ($null -eq $python) {
    Write-Check "Python" "UNAVAILABLE" "Install Python or create .venv."
    $failed = $true
} else {
    $version = & $python --version 2>&1
    Write-Check "Python" "HEALTHY" $version
    $venvStatus = if ($python -like "$script:ProjectRoot*") { "HEALTHY" } else { "DEGRADED" }
    $venvDetail = if ($python -like "$script:ProjectRoot*") { "Project environment selected." } else { "Using PATH Python." }
    Write-Check "Virtual environment" $venvStatus $venvDetail
}

$envExists = Test-Path -LiteralPath (Join-Path $script:ProjectRoot ".env") -PathType Leaf
Write-Check ".env" $(if ($envExists) { "HEALTHY" } else { "UNAVAILABLE" }) "Contents are never printed."
if (-not $envExists) { $failed = $true }

$mysqlService = Get-Service -Name "MySQL80" -ErrorAction SilentlyContinue
$mysqlServiceState = if ($null -eq $mysqlService) { "not installed" } else { $mysqlService.Status.ToString() }
$mysqlPort = Test-TcpPort 3306
Write-Check "MySQL80" $(if ($mysqlPort) { "HEALTHY" } else { "UNAVAILABLE" }) "service=$mysqlServiceState; port3306=$mysqlPort"

$dockerAvailable = Test-DockerAvailable
Write-Check "Docker Desktop" $(if ($dockerAvailable) { "HEALTHY" } else { "UNAVAILABLE" }) "Read-only daemon check."
$qdrantPort = Test-TcpPort 6333
$qdrantContainer = "not checked"
if ($dockerAvailable) {
    $qdrantContainer = (& docker compose ps --status running --services qdrant 2>$null | Out-String).Trim()
    if (-not $qdrantContainer) { $qdrantContainer = "not running" }
}
Write-Check "Qdrant" $(if ($qdrantPort) { "HEALTHY" } else { "DEGRADED" }) "port6333=$qdrantPort; container=$qdrantContainer"

$snapshot = Get-SystemSnapshot
if ($null -eq $snapshot) {
    Write-Check "Application probes" "UNAVAILABLE" "Could not run safe local diagnostics."
    $failed = $true
} else {
    Write-Check "MySQL connection" $snapshot.database.status "Read-only SELECT 1."
    Write-Check "Vault" $snapshot.vault.status ("markdown_files=" + $snapshot.vault.markdown_files)
    Write-Check "Embedding cache" $(if ($snapshot.embedding.cache_available) { "AVAILABLE" } else { "NOT_FOUND" }) ("runtime=" + $snapshot.embedding.status + "; no model was loaded")
    Write-Check "Reranker cache" $(if ($snapshot.reranker.cache_available) { "AVAILABLE" } else { "NOT_FOUND" }) ("runtime=" + $snapshot.reranker.status + "; configured_device=" + $snapshot.reranker.configured_device)
    Write-Check "DeepSeek" $(if ($snapshot.deepseek_configured) { "CONFIGURED" } else { "NOT_CONFIGURED" }) "Secret is not displayed."
    Write-Check "LangSmith" $snapshot.langsmith.status ("configured=" + $snapshot.langsmith.configured + "; safe_mode=" + $snapshot.langsmith.safe_mode)
    if ($snapshot.database.status -ne "healthy" -or $snapshot.vault.status -eq "unavailable") {
        $failed = $true
    }
}

$alembic = Get-AlembicState
Write-Check "Alembic" $(if ($alembic.IsCurrent) { "HEALTHY" } else { "DEGRADED" }) ("current=" + $alembic.Current + "; head=" + $alembic.Head)
if (-not $alembic.IsCurrent) { $failed = $true }

$port8000 = Test-TcpPort 8000
$projectProcess = Get-ProjectFastApiProcess
$portStatus = if ($null -ne $projectProcess) { "PROJECT_RUNNING" } elseif ($port8000) { "OCCUPIED" } else { "AVAILABLE" }
Write-Check "Port 8000" $portStatus "No process is stopped by doctor."
if ($port8000 -and $null -eq $projectProcess) { $failed = $true }

if ($failed) { exit 1 }
exit 0
