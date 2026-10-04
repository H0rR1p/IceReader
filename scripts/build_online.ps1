param([string]$Python = '', [switch]$FetchSources)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
if (-not $Python) { $Python = Join-Path $root '.venv64\Scripts\python.exe' }
Push-Location $root
try {
    $env:VITE_BINGDU_DEPLOYMENT = 'online'
    npm run build
    if ($LASTEXITCODE -ne 0) { throw 'Online frontend build failed' }
    $arguments = @('scripts/prepare_legal.py')
    if ($FetchSources) { $arguments += '--fetch' }
    & $Python @arguments
    if ($LASTEXITCODE -ne 0) { throw 'Online legal preparation failed' }
    & $Python scripts/verify_legal_bundle.py build/legal
    if ($LASTEXITCODE -ne 0) { throw 'Online legal verification failed' }
    foreach ($service in @('web','cloud')) {
        & $Python -m PyInstaller --noconfirm --clean --distpath build/online-release --workpath "build/online-pyinstaller-$service" "scripts/online_$service.spec"
        if ($LASTEXITCODE -ne 0) { throw "Online $service packaging failed" }
    }
    foreach ($service in @('IceReaderWeb','IceReaderCloud')) {
        $target = Join-Path $root "build/online-release/$service/legal"
        Copy-Item -LiteralPath build/legal -Destination $target -Recurse -Force
    }
    Write-Output 'Online release ready: build/online-release; configure BINGDU_LEGAL_DIR to each service/legal directory.'
} finally {
    Remove-Item Env:VITE_BINGDU_DEPLOYMENT -ErrorAction SilentlyContinue
    Pop-Location
}
