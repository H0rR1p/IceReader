$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

npm run build
& ".\.venv64\Scripts\python.exe" ".\scripts\create_windows_icon.py"
& ".\.venv64\Scripts\python.exe" -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
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

$bundleSource = Join-Path $root "build\release\bingdu"
$bundleName = (-join ([char]0x51B0, [char]0x8BFB))
$bundleOutput = Join-Path $root ("build\release\" + $bundleName)
$executable = Join-Path $bundleSource "bingdu.exe"
$renamedExecutable = Join-Path $bundleSource ($bundleName + ".exe")
$preservedData = Join-Path $root "build\release\.bingdu-user-data"

if (Test-Path -LiteralPath $bundleOutput) {
    $existingData = Join-Path $bundleOutput "data"
    if (Test-Path -LiteralPath $existingData) {
        if (Test-Path -LiteralPath $preservedData) {
            Remove-Item -LiteralPath $preservedData -Recurse -Force
        }
        Move-Item -LiteralPath $existingData -Destination $preservedData
    }
    Remove-Item -LiteralPath $bundleOutput -Recurse -Force
}
Rename-Item -LiteralPath $executable -NewName ($bundleName + ".exe")
Move-Item -LiteralPath $bundleSource -Destination $bundleOutput
if (Test-Path -LiteralPath $preservedData) {
    Move-Item -LiteralPath $preservedData -Destination (Join-Path $bundleOutput "data")
} elseif (Test-Path -LiteralPath (Join-Path $root "data")) {
    Copy-Item -LiteralPath (Join-Path $root "data") -Destination (Join-Path $bundleOutput "data") -Recurse
}
Write-Host "Build complete: $bundleOutput"
