# Starts the apartment DSS at http://127.0.0.1:8100 (creates the virtual environment on first run)
param([int]$Port = 8100)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    py -3 -m venv .venv
    .venv\Scripts\python.exe -m pip install --upgrade pip
    .venv\Scripts\python.exe -m pip install -r requirements.txt
}
Start-Process "http://127.0.0.1:$Port"
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port $Port
