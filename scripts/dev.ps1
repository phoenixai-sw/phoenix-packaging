param([switch]$Stop)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot
$taskState = Join-Path $taskRoot '.local/dev-processes.json'
New-Item -ItemType Directory -Path '.local' -Force | Out-Null
if ($Stop) {
    if (Test-Path -LiteralPath $taskState) {
        $taskProcesses = Get-Content -LiteralPath $taskState -Raw | ConvertFrom-Json
        foreach ($taskProcess in $taskProcesses) {
            $taskRunning = Get-Process -Id $taskProcess.id -ErrorAction SilentlyContinue
            if ($taskRunning -and $taskRunning.StartTime.ToUniversalTime().ToString('o') -eq $taskProcess.started) {
                Stop-Process -Id $taskProcess.id
            }
        }
    }
    Write-Output 'Phoenix local processes stopped.'
    return
}
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
