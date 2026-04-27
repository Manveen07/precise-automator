$ErrorActionPreference = "Stop"

.\.venv\Scripts\Activate.ps1
$RedisUrl = if ($env:REDIS_URL) { $env:REDIS_URL } else { "redis://localhost:6379/0" }
rq worker campaign_sync --url $RedisUrl --worker-class app.workers.rq_windows.WindowsSimpleWorker
