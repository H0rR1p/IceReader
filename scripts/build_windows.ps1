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
        # Copy the data snapshot before replacing the bundle. Moving a directory
        # containing SQLite WAL files can fail on Windows even after the app exits.
        Copy-Item -LiteralPath $existingData -Destination $preservedData -Recurse -Force
    }
    Remove-Item -LiteralPath $bundleOutput -Recurse -Force
}
Rename-Item -LiteralPath $executable -NewName ($bundleName + ".exe")
Copy-Item -LiteralPath $bundleSource -Destination $bundleOutput -Recurse -Force
Remove-Item -LiteralPath $bundleSource -Recurse -Force
if (Test-Path -LiteralPath $preservedData) {
    Copy-Item -LiteralPath $preservedData -Destination (Join-Path $bundleOutput "data") -Recurse -Force
    Remove-Item -LiteralPath $preservedData -Recurse -Force
} elseif (Test-Path -LiteralPath (Join-Path $root "data")) {
    Copy-Item -LiteralPath (Join-Path $root "data") -Destination (Join-Path $bundleOutput "data") -Recurse
}

$compiler64 = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
$compiler32 = Join-Path $env:WINDIR "Microsoft.NET\Framework\v4.0.30319\csc.exe"
$compiler = if (Test-Path -LiteralPath $compiler64) { $compiler64 } else { $compiler32 }
$launcherOutput = Join-Path $root "build\release\bingdu-launcher.exe"
& $compiler /nologo /target:winexe /win32icon:"$root\assets\bingdu.ico" /out:"$launcherOutput" "$root\scripts\windows_bootstrap.cs"
if ($LASTEXITCODE -ne 0) {
    throw "冰读启动器编译失败，退出代码：$LASTEXITCODE"
}
Copy-Item -LiteralPath $launcherOutput -Destination (Join-Path $root ($bundleName + ".exe")) -Force
Write-Host "Build complete: $bundleOutput"
Write-Host "Launcher: $(Join-Path $root ($bundleName + '.exe'))"
