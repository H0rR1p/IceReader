param([string]$Python = "", [switch]$FetchSources)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
if (-not $Python) {
    foreach ($candidate in @((Join-Path $root '.venv64\Scripts\python.exe'), (Join-Path $root '..\..\.venv64\Scripts\python.exe'))) {
        if (Test-Path -LiteralPath $candidate) { $Python = (Resolve-Path -LiteralPath $candidate).Path; break }
    }
}
if (-not $Python -or -not (Test-Path -LiteralPath $Python)) { throw 'A Python build environment is required; pass -Python.' }
npm run build
if ($LASTEXITCODE -ne 0) { throw "前端构建失败" }
& $python scripts/create_windows_icon.py
if ($LASTEXITCODE -ne 0) { throw "图标生成失败" }
$legalArguments = @('scripts/prepare_legal.py')
if ($FetchSources) { $legalArguments += '--fetch' }
& $Python @legalArguments
if ($LASTEXITCODE -ne 0) { throw "许可证或对应源码包准备失败，请先运行 scripts/prepare_legal.py --fetch" }
& $python -m PyInstaller --noconfirm --onedir --console --name bingdu-service `
    --icon "$root\assets\bingdu.ico" `
    --add-data "$root\dist;dist" `
    --add-data "$root\resources\grammar;resources/grammar" `
    --add-data "$root\assets\ymm4-bridge;ymm4-bridge" `
    --collect-all sudachidict_core --collect-all sudachipy `
    --distpath "$root\build\desktop-backend" --workpath "$root\build\desktop-pyinstaller" `
    --specpath "$root\build" "$root\backend\desktop_launcher.py"
if ($LASTEXITCODE -ne 0) { throw "客户端内部服务打包失败" }
$releaseName = "desktop-release-" + (Get-Date -Format "yyyyMMdd-HHmmss-fff")
# The shell uses only Electron and Node builtins; frontend dependencies are in dist.
# Stage real files so a shared worktree node_modules junction never enters ASAR.
$shellDirectory = Join-Path $root "build/$releaseName-shell"
New-Item -ItemType Directory -Path (Join-Path $shellDirectory 'public') -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $root 'desktop') -Destination (Join-Path $shellDirectory 'desktop') -Recurse
Copy-Item -LiteralPath (Join-Path $root 'public/bingdu-logo.png') -Destination (Join-Path $shellDirectory 'public/bingdu-logo.png')
$shellPackage = Get-Content package.json -Raw -Encoding UTF8 | ConvertFrom-Json
foreach ($property in @('dependencies', 'devDependencies', 'scripts')) { $shellPackage.PSObject.Properties.Remove($property) }
[System.IO.File]::WriteAllText((Join-Path $shellDirectory 'package.json'), ($shellPackage | ConvertTo-Json -Depth 20))
npx electron-builder --win --x64 "--config.directories.app=$shellDirectory" "--config.directories.output=build/$releaseName"
if ($LASTEXITCODE -ne 0) { throw "桌面客户端打包失败" }
& $python scripts/verify_legal_bundle.py "$root\build\$releaseName\win-unpacked\resources\legal" --desktop
if ($LASTEXITCODE -ne 0) { throw "许可与对应源码包验证失败" }
& $python scripts/verify_windows_icon.py "$root\build\$releaseName\win-unpacked\冰读.exe" "$root\assets\bingdu.ico"
if ($LASTEXITCODE -ne 0) { throw "客户端 EXE 图标验证失败" }
& $python scripts/verify_windows_icon.py "$root\build\$releaseName\IceReader-$((Get-Content package.json -Raw -Encoding UTF8 | ConvertFrom-Json).version)-Setup.exe" "$root\assets\bingdu.ico"
if ($LASTEXITCODE -ne 0) { throw "安装包图标验证失败" }

$compiler = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
& $compiler /nologo /target:winexe /win32icon:"$root\assets\bingdu.ico" /out:"$root\冰读.exe" "$root\scripts\windows_bootstrap.cs"
if ($LASTEXITCODE -ne 0) { throw "项目根目录启动器更新失败" }
$pointer = Join-Path $root "build\desktop-current.txt"
[System.IO.File]::WriteAllText($pointer + ".tmp", $releaseName)
Move-Item -LiteralPath ($pointer + ".tmp") -Destination $pointer -Force
