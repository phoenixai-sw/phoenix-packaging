param([switch]$Stop)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot
$taskState = Join-Path $taskRoot '.local/dev-processes.json'
New-Item -ItemType Directory -Path '.local' -Force | Out-Null
if ($Stop) {
    # Python's Windows launcher and Next spawn children; stop only commands
    # containing this workspace's exact executable/script path, not reused PIDs.
    $taskPythonCommand = '"' + (Join-Path $taskRoot '.venv\Scripts\python.exe') + '"'
    $taskNextPath = Join-Path $taskRoot 'node_modules\next\dist\'
    $taskOwned = Get-CimInstance Win32_Process | Where-Object {
        ($_.Name -eq 'python.exe' -and $_.CommandLine -and $_.CommandLine.StartsWith($taskPythonCommand, [StringComparison]::OrdinalIgnoreCase) -and
            ($_.CommandLine.Contains(' -m uvicorn services.api.main:app ') -or $_.CommandLine.Contains(' -m services.worker.runner'))) -or
        ($_.Name -eq 'node.exe' -and $_.CommandLine -and $_.CommandLine.Contains($taskNextPath))
    }
    foreach ($taskProcess in $taskOwned) { Stop-Process -Id $taskProcess.ProcessId -ErrorAction SilentlyContinue }
    Write-Output 'Phoenix local processes stopped.'
    return
}
$taskListening = Get-NetTCPConnection -LocalPort 3000,8000 -State Listen -ErrorAction SilentlyContinue
if ($taskListening) { throw 'Port 3000 or 8000 is already in use. Stop this workspace with scripts/dev.ps1 -Stop, or resolve the conflicting service first.' }
if (Test-Path -LiteralPath '.env') {
    foreach ($taskLine in Get-Content -LiteralPath '.env') {
        if ($taskLine -match '^([A-Z][A-Z0-9_]*)=(.*)$') { [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2].Trim('"'), 'Process') }
    }
}
$taskPython = Join-Path $taskRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Create .venv and install services/api/requirements-dev.txt first.' }
& $taskPython -m alembic -c services/api/alembic.ini upgrade head
if ($LASTEXITCODE -ne 0) { throw 'Database migration failed.' }
$taskStarted = @()
$taskApi = Start-Process -FilePath $taskPython -ArgumentList @('-m','uvicorn','services.api.main:app','--host','127.0.0.1','--port','8000') -WorkingDirectory $taskRoot -WindowStyle Hidden -RedirectStandardOutput '.local/api.log' -RedirectStandardError '.local/api.error.log' -PassThru
$taskStarted += @{id=$taskApi.Id;started=$taskApi.StartTime.ToUniversalTime().ToString('o');kind='api'}
$taskWorker = Start-Process -FilePath $taskPython -ArgumentList @('-m','services.worker.runner') -WorkingDirectory $taskRoot -WindowStyle Hidden -RedirectStandardOutput '.local/worker.log' -RedirectStandardError '.local/worker.error.log' -PassThru
$taskStarted += @{id=$taskWorker.Id;started=$taskWorker.StartTime.ToUniversalTime().ToString('o');kind='worker'}
$taskNode = (Get-Command node).Source
$taskNext = '"' + (Join-Path $taskRoot 'node_modules/next/dist/bin/next') + '"'
$taskWeb = Start-Process -FilePath $taskNode -ArgumentList @($taskNext,'dev','--hostname','127.0.0.1','--port','3000') -WorkingDirectory (Join-Path $taskRoot 'apps/web') -WindowStyle Hidden -RedirectStandardOutput (Join-Path $taskRoot '.local/web.log') -RedirectStandardError (Join-Path $taskRoot '.local/web.error.log') -PassThru
$taskStarted += @{id=$taskWeb.Id;started=$taskWeb.StartTime.ToUniversalTime().ToString('o');kind='web'}
$taskStarted | ConvertTo-Json | Set-Content -LiteralPath $taskState -Encoding UTF8
Write-Output 'Phoenix is starting at http://localhost:3000. Logs: .local/*.log'
