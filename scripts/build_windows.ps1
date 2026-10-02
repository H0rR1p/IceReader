$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Copy-DirectoryContents {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination,
        [switch]$Verify
    )
    if (-not (Test-Path -LiteralPath $Source)) { return }
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    $sourceItems = Get-ChildItem -LiteralPath $Source -Force
    foreach ($item in $sourceItems) {
        Copy-Item -LiteralPath $item.FullName -Destination $Destination -Recurse -Force
    }
    if ($Verify) {
        $sourceCount = (Get-ChildItem -LiteralPath $Source -Recurse -File -Force | Measure-Object).Count
        $destinationCount = (Get-ChildItem -LiteralPath $Destination -Recurse -File -Force | Measure-Object).Count
        if ($destinationCount -lt $sourceCount) {
            throw "本地数据复制不完整：源文件 $sourceCount 个，目标文件 $destinationCount 个"
        }
    }
}

npm run build
& ".\.venv64\Scripts\python.exe" ".\scripts\install_builtin_dictionary.py"
if ($LASTEXITCODE -ne 0) {
    throw "内置 Jitendex 日中词典安装或校验失败，已停止构建"
}
& ".\.venv64\Scripts\python.exe" ".\scripts\create_windows_icon.py"
& ".\.venv64\Scripts\python.exe" -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --windowed `
    --name "bingdu" `
    --icon "$root\assets\bingdu.ico" `
    --add-data "$root\dist;dist" `
    --add-data "$root\assets\ymm4-bridge;ymm4-bridge" `
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
        Copy-DirectoryContents -Source $existingData -Destination $preservedData -Verify
    }
    Remove-Item -LiteralPath $bundleOutput -Recurse -Force
}
Rename-Item -LiteralPath $executable -NewName ($bundleName + ".exe")
Copy-Item -LiteralPath $bundleSource -Destination $bundleOutput -Recurse -Force
Remove-Item -LiteralPath $bundleSource -Recurse -Force
Copy-Item -LiteralPath (Join-Path $root "THIRD-PARTY-NOTICES.txt") -Destination $bundleOutput -Force
if (Test-Path -LiteralPath $preservedData) {
    Copy-DirectoryContents -Source $preservedData -Destination (Join-Path $bundleOutput "data") -Verify
    Remove-Item -LiteralPath $preservedData -Recurse -Force
} elseif (Test-Path -LiteralPath (Join-Path $root "data")) {
    Copy-DirectoryContents -Source (Join-Path $root "data") -Destination (Join-Path $bundleOutput "data") -Verify
}

$bundledDictionaryArchives = Join-Path $bundleOutput "data\dictionaries"
$projectDictionaryArchives = Join-Path $root "data\dictionaries"
if (Test-Path -LiteralPath $projectDictionaryArchives) {
    Copy-DirectoryContents -Source $projectDictionaryArchives -Destination $bundledDictionaryArchives -Verify
}
$previousBingduDataDir = $env:BINGDU_DATA_DIR
try {
    $env:BINGDU_DATA_DIR = Join-Path $bundleOutput "data"
    & ".\.venv64\Scripts\python.exe" ".\scripts\install_builtin_dictionary.py"
    if ($LASTEXITCODE -ne 0) {
        throw "无法把内置 Jitendex 日中词典合并到目录版数据"
    }
} finally {
    if ($null -eq $previousBingduDataDir) {
        Remove-Item Env:BINGDU_DATA_DIR -ErrorAction SilentlyContinue
    } else {
        $env:BINGDU_DATA_DIR = $previousBingduDataDir
    }
}

# The indexed SQLite dictionary is sufficient at runtime. Keep the verified
# source archive in the development data directory, but do not duplicate it in
# the Windows bundle.
if (Test-Path -LiteralPath $bundledDictionaryArchives) {
    Remove-Item -LiteralPath $bundledDictionaryArchives -Recurse -Force
}

# During development, also merge locally extracted EPUB resources into the
# bundle. Database files and settings remain those preserved from the bundle.
$projectBooks = Join-Path $root "data\books"
if (Test-Path -LiteralPath $projectBooks) {
    Copy-DirectoryContents -Source $projectBooks -Destination (Join-Path $bundleOutput "data\books")
}
& ".\.venv64\Scripts\python.exe" ".\scripts\validate_book_assets.py" (Join-Path $bundleOutput "data") --repair-previews
if ($LASTEXITCODE -ne 0) {
    throw "目录版数据引用了缺失的 EPUB 图片或预览文件，已停止构建"
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

