[CmdletBinding()]
param(
    [switch]$Apply,
    [ValidateRange(1, 3650)][int]$RetentionDays = 10,
    [ValidateRange(1, 3650)][int]$LogRetentionDays = 30
)

. (Join-Path $PSScriptRoot "common.ps1")

$runtimeRoot = [IO.Path]::GetFullPath((Join-Path $script:ProjectRoot "runtime"))
$allowedRoots = @(
    [IO.Path]::GetFullPath((Join-Path $runtimeRoot "temp")),
    [IO.Path]::GetFullPath((Join-Path $runtimeRoot "cache")),
    [IO.Path]::GetFullPath((Join-Path $runtimeRoot "logs"))
)
$cutoff = (Get-Date).AddDays(-$RetentionDays)
$logCutoff = (Get-Date).AddDays(-$LogRetentionDays)
$candidates = New-Object Collections.Generic.List[IO.FileInfo]

foreach ($root in $allowedRoots) {
    if (-not (Test-Path -LiteralPath $root -PathType Container)) { continue }
    $rootPrefix = $root.TrimEnd("\") + "\"
    $rootCutoff = if ($root -eq $allowedRoots[2]) { $logCutoff } else { $cutoff }
    Get-ChildItem -LiteralPath $root -File -Recurse -Force |
        Where-Object {
            -not ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -and
            $_.LastWriteTime -lt $rootCutoff
        } |
        ForEach-Object {
            $fullPath = [IO.Path]::GetFullPath($_.FullName)
            if (-not $fullPath.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
                throw "Cleanup boundary check failed."
            }
            $candidates.Add($_)
        }
}

$stalePid = $false
if (Test-Path -LiteralPath $script:PidFile -PathType Leaf) {
    $stalePid = $null -eq (Get-ProjectFastApiProcess)
}

Write-Host $(if ($Apply) { "APPLY" } else { "DRY RUN" })
Write-Host ("Eligible runtime files: " + $candidates.Count)
foreach ($item in $candidates) {
    $relative = $item.FullName.Substring($script:ProjectRoot.Length).TrimStart("\")
    Write-Host ("  " + $relative)
}
Write-Host ("Stale PID file: " + $stalePid)

$projectProcess = Get-ProjectFastApiProcess
if ($null -ne $projectProcess) {
    try {
        $payload = @{ dry_run = (-not $Apply); older_than_days = $RetentionDays } | ConvertTo-Json
        $history = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/agent/maintenance/cleanup-history" -ContentType "application/json" -Body $payload -TimeoutSec 30
        Write-Host ("Eligible ordinary run history: " + $history.agent_runs)
        Write-Host ("Eligible tool audit history: " + $history.tool_executions)
    } catch {
        Write-Warning "Run-history cleanup preview was unavailable; no database fallback was attempted."
    }
} else {
    Write-Host "Run history: skipped because this project's FastAPI is not running."
}

if (-not $Apply) {
    Write-Host "No files or database rows were changed. Re-run with -Apply to delete only the items listed above."
    exit 0
}

foreach ($item in $candidates) {
    Remove-Item -LiteralPath $item.FullName -Force
}
if ($stalePid) {
    Remove-Item -LiteralPath $script:PidFile -Force
}
Write-Host "Cleanup applied within project runtime paths only."
