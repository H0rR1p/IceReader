$ErrorActionPreference = "Stop"

if (-not (Test-Path ".venv64\Scripts\python.exe")) {
    Write-Host "首次运行：正在创建 Python 环境并安装依赖..."
    python -m venv .venv64
    .\.venv64\Scripts\python -m pip install -r backend\requirements.txt
}

if (-not (Test-Path "node_modules")) {
    Write-Host "首次运行：正在安装前端依赖..."
    npm install
}

Write-Host "启动本地 API 与网页..."
$api = Start-Process -FilePath ".\.venv64\Scripts\python.exe" -ArgumentList "-m", "uvicorn", "backend.app:app", "--host", "127.0.0.1", "--port", "8000" -PassThru -WindowStyle Hidden
try {
    npm run dev
}
finally {
    Stop-Process -Id $api.Id -ErrorAction SilentlyContinue
}
