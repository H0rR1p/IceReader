param(
    [string]$Output = (Join-Path $PSScriptRoot "..\build\bingdu-voice-runtime.zip")
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ymm = Join-Path $root "幻想乡口音剪辑器"
$template = Join-Path $root "幻想乡口音.ymmp"
if (-not (Test-Path -LiteralPath (Join-Path $ymm "YukkuriMovieMaker.exe"))) { throw "工作区中缺少 YMM4" }
if (-not (Test-Path -LiteralPath $template)) { throw "工作区中缺少幻想乡口音模板" }

$destination = [IO.Path]::GetFullPath($Output)
New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($destination)) -Force | Out-Null
Remove-Item -LiteralPath $destination -Force -ErrorAction SilentlyContinue

Push-Location $root
try {
    & tar.exe -a -c -f $destination `
        --exclude='幻想乡口音剪辑器/user/log' `
        --exclude='幻想乡口音剪辑器/user/backup' `
        --exclude='幻想乡口音剪辑器/user/setting' `
        --exclude='幻想乡口音剪辑器/user/resources' `
        '幻想乡口音剪辑器' '幻想乡口音.ymmp'
    if ($LASTEXITCODE -ne 0) { throw "创建配音运行包失败，tar 返回 $LASTEXITCODE" }
} finally {
    Pop-Location
}

$hash = Get-FileHash -Algorithm SHA256 -LiteralPath $destination
Write-Host "配音运行包：$destination"
Write-Host "大小：$((Get-Item -LiteralPath $destination).Length) 字节"
Write-Host "SHA256：$($hash.Hash)"
