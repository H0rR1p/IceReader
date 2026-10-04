$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$python = Join-Path $root ".venv64\Scripts\python.exe"
npm run build
if ($LASTEXITCODE -ne 0) { throw "前端构建失败" }
& $python scripts/create_windows_icon.py
if ($LASTEXITCODE -ne 0) { throw "图标生成失败" }
& $python scripts/prepare_legal.py
if ($LASTEXITCODE -ne 0) { throw "许可证或对应源码包准备失败，请先运行 scripts/prepare_legal.py --fetch" }
& $python -m PyInstaller --noconfirm --onedir --console --name bingdu-service `
    --icon "$root\assets\bingdu.ico" `
    --add-data "$root\dist;dist" `
    --add-data "$root\assets\ymm4-bridge;ymm4-bridge" `
    --collect-all sudachidict_core --collect-all sudachipy `
    --distpath "$root\build\desktop-backend" --workpath "$root\build\desktop-pyinstaller" `
    --specpath "$root\build" "$root\backend\desktop_launcher.py"
if ($LASTEXITCODE -ne 0) { throw "客户端内部服务打包失败" }
$releaseName = "desktop-release-" + (Get-Date -Format "yyyyMMdd-HHmmss-fff")
npx electron-builder --win --x64 "--config.directories.output=build/$releaseName"
if ($LASTEXITCODE -ne 0) { throw "桌面客户端打包失败" }
& $python scripts/verify_legal_bundle.py "$root\build\$releaseName\win-unpacked\resources\legal"
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
