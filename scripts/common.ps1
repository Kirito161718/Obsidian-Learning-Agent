Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

$script:ProjectRoot = [IO.Path]::GetFullPath((Split-Path $PSScriptRoot -Parent))
$script:PidFile = Join-Path $script:ProjectRoot "runtime\pids\fastapi.pid"

function Get-ProjectPython {
    $candidates = @(
        (Join-Path $script:ProjectRoot ".venv\Scripts\python.exe"),
        (Join-Path $script:ProjectRoot "venv\Scripts\python.exe")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return $candidate
        }
    }
    $command = Get-Command python -ErrorAction SilentlyContinue
    if ($null -ne $command) {
        return $command.Source
    }
    return $null
}

function Test-TcpPort {
    param([int]$Port, [int]$TimeoutMs = 1200)
    $client = New-Object Net.Sockets.TcpClient
    try {
        $pending = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
        if (-not $pending.AsyncWaitHandle.WaitOne($TimeoutMs, $false)) {
            return $false
        }
        $client.EndConnect($pending)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Get-ProjectFastApiProcess {
    if (-not (Test-Path -LiteralPath $script:PidFile -PathType Leaf)) {
        return $null
    }
    $raw = (Get-Content -Raw -LiteralPath $script:PidFile).Trim()
    $pidValue = 0
    if (-not [int]::TryParse($raw, [ref]$pidValue)) {
        return $null
    }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$pidValue" -ErrorAction SilentlyContinue
    if ($null -eq $process) {
        return $null
    }
    $commandLine = [string]$process.CommandLine
    if (
        $commandLine -notlike "*uvicorn*" -or
        $commandLine -notlike "*app.main:app*" -or
        $commandLine -notlike "*$script:ProjectRoot*"
    ) {
        return $null
    }
    return $process
}

function Get-SystemSnapshot {
    $python = Get-ProjectPython
    if ($null -eq $python) {
        return $null
    }
    $json = & $python -m app.services.system_status 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $json) {
        return $null
    }
    try {
        return ($json | Out-String | ConvertFrom-Json)
    } catch {
        return $null
    }
}

function Get-AlembicState {
    $python = Get-ProjectPython
    if ($null -eq $python) {
        return [pscustomobject]@{ Current = $null; Head = $null; IsCurrent = $false }
    }
    $alembic = Join-Path (Split-Path $python -Parent) "alembic.exe"
    if (-not (Test-Path -LiteralPath $alembic -PathType Leaf)) {
        $command = Get-Command alembic -ErrorAction SilentlyContinue
        if ($null -eq $command) {
            return [pscustomobject]@{ Current = $null; Head = $null; IsCurrent = $false }
        }
        $alembic = $command.Source
    }
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $headOutput = (& $alembic heads 2>&1 | Out-String)
        $currentOutput = (& $alembic current 2>&1 | Out-String)
    } finally {
        $ErrorActionPreference = $previousPreference
    }
    $headMatch = [regex]::Match($headOutput, "[0-9a-fA-F_]{8,}")
    $currentMatch = [regex]::Match($currentOutput, "[0-9a-fA-F_]{8,}")
    $head = if ($headMatch.Success) { $headMatch.Value } else { $null }
    $current = if ($currentMatch.Success) { $currentMatch.Value } else { $null }
    return [pscustomobject]@{
        Current = $current
        Head = $head
        IsCurrent = ($null -ne $head -and $head -eq $current)
    }
}

function Test-DockerAvailable {
    $docker = Get-Command docker -ErrorAction SilentlyContinue
    if ($null -eq $docker) {
        return $false
    }
    & docker info --format "{{.ServerVersion}}" 2>$null | Out-Null
    return $LASTEXITCODE -eq 0
}

function Write-Check {
    param([string]$Name, [string]$Status, [string]$Detail)
    Write-Host ("{0,-22} {1,-12} {2}" -f $Name, $Status, $Detail)
}
