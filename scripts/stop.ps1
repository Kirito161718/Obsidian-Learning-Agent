[CmdletBinding()]
param([switch]$StopQdrant)

. (Join-Path $PSScriptRoot "common.ps1")

if (Test-Path -LiteralPath $script:PidFile -PathType Leaf) {
    $process = Get-ProjectFastApiProcess
    if ($null -eq $process) {
        Write-Warning "The PID file is stale or does not identify this project's FastAPI process. No process was stopped."
        Remove-Item -LiteralPath $script:PidFile -Force
    } else {
        Stop-Process -Id $process.ProcessId
        Wait-Process -Id $process.ProcessId -Timeout 15 -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $script:PidFile -Force -ErrorAction SilentlyContinue
        Write-Host "Stopped this project's FastAPI process (PID $($process.ProcessId))."
    }
} else {
    Write-Host "No project FastAPI PID file was found."
}

if ($StopQdrant) {
    if (-not (Test-DockerAvailable)) {
        throw "Docker Desktop is unavailable; Qdrant was not stopped."
    }
    & docker compose stop qdrant
    if ($LASTEXITCODE -ne 0) { throw "Failed to stop the qdrant service." }
    Write-Host "Stopped only the qdrant service. MySQL80 and Docker Desktop were not stopped."
} else {
    Write-Host "Qdrant was left running. Use -StopQdrant to stop it explicitly."
}
