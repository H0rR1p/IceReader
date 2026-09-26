$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

npm run build
& ".\.venv64\Scripts\python.exe" ".\scripts\create_windows_icon.py"
& ".\.venv64\Scripts\python.exe" -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name "bingdu" `
    --icon "$root\assets\bingdu.ico" `
    --add-data "$root\dist;dist" `
    --collect-all sudachidict_core `
    --collect-all sudachipy `
    --distpath ".\build\release" `
    --workpath ".\build\pyinstaller" `
    --specpath ".\build" `
    "$root\backend\launcher.py"

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller 打包失败，退出代码：$LASTEXITCODE"
}

$output = Join-Path $root "build\release\bingdu.exe"
$finalName = (-join ([char]0x51B0, [char]0x8BFB)) + ".exe"
$finalOutput = Join-Path $root $finalName
Copy-Item -LiteralPath $output -Destination $finalOutput -Force
Write-Host "Build complete: $finalOutput"
