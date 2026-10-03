$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$python = Join-Path $root ".venv64\Scripts\python.exe"
npm run build
if ($LASTEXITCODE -ne 0) { throw "前端构建失败" }
& $python scripts/create_windows_icon.py
if ($LASTEXITCODE -ne 0) { throw "图标生成失败" }
$dictionaryData = Join-Path $root "build\desktop-dictionary-data"
$previousData = $env:BINGDU_DATA_DIR
New-Item -ItemType Directory -Path (Join-Path $dictionaryData "dictionaries") -Force | Out-Null
$cachedArchive = Join-Path $root "data\dictionaries\jitendex-yomitan-zh-v2026.08.11-zh.4.zip"
if (Test-Path -LiteralPath $cachedArchive) {
    Copy-Item -LiteralPath $cachedArchive -Destination (Join-Path $dictionaryData "dictionaries") -Force
}
try {
    $env:BINGDU_DATA_DIR = $dictionaryData
    & $python scripts/install_builtin_dictionary.py
    if ($LASTEXITCODE -ne 0) { throw "内置词典初始化失败" }
} finally {
    if ($null -eq $previousData) { Remove-Item Env:BINGDU_DATA_DIR -ErrorAction SilentlyContinue }
    else { $env:BINGDU_DATA_DIR = $previousData }
}
Copy-Item -LiteralPath (Join-Path $dictionaryData "dictionary.sqlite3") -Destination (Join-Path $root "build\desktop-dictionary.sqlite3") -Force
& $python -m PyInstaller --noconfirm --onedir --console --name bingdu-service `
    --icon "$root\assets\bingdu.ico" `
    --add-data "$root\dist;dist" `
    --add-data "$root\assets\ymm4-bridge;ymm4-bridge" `
    --add-data "$root\build\desktop-dictionary.sqlite3;." `
    --collect-all sudachidict_core --collect-all sudachipy `
    --distpath "$root\build\desktop-backend" --workpath "$root\build\desktop-pyinstaller" `
    --specpath "$root\build" "$root\backend\desktop_launcher.py"
if ($LASTEXITCODE -ne 0) { throw "客户端内部服务打包失败" }
npx electron-builder --win --x64
if ($LASTEXITCODE -ne 0) { throw "桌面客户端打包失败" }

$compiler = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
& $compiler /nologo /target:winexe /win32icon:"$root\assets\bingdu.ico" /out:"$root\冰读.exe" "$root\scripts\windows_bootstrap.cs"
if ($LASTEXITCODE -ne 0) { throw "项目根目录启动器更新失败" }
