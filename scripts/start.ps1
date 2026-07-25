[CmdletBinding()]
param([switch]$Prewarm)

. (Join-Path $PSScriptRoot "common.ps1")

Write-Host "Running read-only diagnostics..."
& (Join-Path $PSScriptRoot "doctor.ps1")
$doctorExit = $LASTEXITCODE

$existing = Get-ProjectFastApiProcess
if ($null -ne $existing) {
    Write-Host "FastAPI is already running for this project (PID $($existing.ProcessId))."
    Write-Host "Open http://127.0.0.1:8000/"
    exit 0
}
if (Test-TcpPort 8000) {
    throw "Port 8000 is occupied by another process. No process was stopped."
}

$python = Get-ProjectPython
if ($null -eq $python) { throw "Python is unavailable." }
if (-not (Test-Path -LiteralPath (Join-Path $script:ProjectRoot ".env"))) {
    throw ".env is missing. Copy .env.example and configure it first."
}

$snapshot = Get-SystemSnapshot
if ($null -eq $snapshot -or $snapshot.database.status -ne "healthy") {
    throw "MySQL80 connection is unavailable. Docker MySQL will not be started."
}

if (-not (Test-TcpPort 6333)) {
    if (-not (Test-DockerAvailable)) {
        throw "Qdrant is unavailable and Docker Desktop is not ready."
    }
    Write-Host "Starting only the Docker Compose qdrant service..."
    & docker compose up -d qdrant
    if ($LASTEXITCODE -ne 0) { throw "Failed to start the qdrant service." }
    $qdrantReady = $false
    for ($index = 0; $index -lt 30; $index++) {
        if (Test-TcpPort 6333) { $qdrantReady = $true; break }
        Start-Sleep -Seconds 1
    }
    if (-not $qdrantReady) { throw "Qdrant did not become ready on port 6333." }
}

$alembic = Get-AlembicState
if (-not $alembic.IsCurrent) {
    throw "Database migration is not at head. Run 'alembic upgrade head' manually, then retry."
}
if ($doctorExit -ne 0) {
    Write-Host "Doctor reported a recoverable warning; mandatory startup checks passed."
}

$runtime = Join-Path $script:ProjectRoot "runtime"
$pidDirectory = Join-Path $runtime "pids"
$logDirectory = Join-Path $runtime "logs"
New-Item -ItemType Directory -Force -Path $pidDirectory, $logDirectory | Out-Null
$stdout = Join-Path $logDirectory "fastapi.stdout.log"
$stderr = Join-Path $logDirectory "fastapi.stderr.log"
$arguments = @(
    "-m", "uvicorn", "app.main:app",
    "--app-dir", $script:ProjectRoot,
    "--host", "127.0.0.1",
    "--port", "8000"
)
$process = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $script:ProjectRoot -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
Set-Content -LiteralPath $script:PidFile -Value $process.Id -Encoding ASCII

$healthy = $false
for ($index = 0; $index -lt 30; $index++) {
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:8000/health" -TimeoutSec 2
        if ($health.status -eq "ok") { $healthy = $true; break }
    } catch {
        Start-Sleep -Seconds 1
    }
}
if (-not $healthy) {
    if (-not $process.HasExited) { Stop-Process -Id $process.Id -ErrorAction SilentlyContinue }
    Remove-Item -LiteralPath $script:PidFile -Force -ErrorAction SilentlyContinue
    throw "FastAPI failed its health check. Inspect runtime/logs/fastapi.stderr.log."
}

if ($Prewarm) {
    Write-Host "Running the safe model prewarm endpoint..."
    try {
        $result = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/system/models/prewarm" -TimeoutSec 600
        Write-Host ("Prewarm status: " + $result.status)
    } catch {
        Write-Warning "Prewarm failed open; FastAPI remains available."
    }
}

Write-Host "FastAPI started (PID $($process.Id))."
Write-Host "Open http://127.0.0.1:8000/"
