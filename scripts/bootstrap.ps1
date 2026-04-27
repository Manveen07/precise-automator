$ErrorActionPreference = "Stop"

$Python = "python"
if (Test-Path "C:\Python312\python.exe") {
  $Python = "C:\Python312\python.exe"
}

docker compose up -d --wait postgres redis

if (!(Test-Path ".venv")) {
  & $Python -m venv .venv
}

.\.venv\Scripts\Activate.ps1
python -m pip install --disable-pip-version-check -r requirements.txt

if (!(Test-Path ".env")) {
  Copy-Item ".env.example" ".env"
}

python -m app.scripts.init_db

Write-Host "Bootstrap complete. Run: uvicorn app.main:app --reload"
