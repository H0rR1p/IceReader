param(
    [Parameter(Mandatory = $true)]
    [string]$AppRoot,
    [string]$RuntimeRoot = (Join-Path $PSScriptRoot "..\voice-runtime"),
    [string]$Character = "琪露诺",
    [switch]$StartNow,
    [switch]$RegisterLogonTask
)

$ErrorActionPreference = "Stop"
$app = (Resolve-Path $AppRoot).Path
$runtime = (Resolve-Path $RuntimeRoot).Path
$ymmDirectory = Join-Path $runtime "幻想乡口音剪辑器"
$ymm = Join-Path $ymmDirectory "YukkuriMovieMaker.exe"
$template = Join-Path $runtime "幻想乡口音.ymmp"
$bridgeSource = Join-Path $app "assets\ymm4-bridge"
$bridgeTarget = Join-Path $ymmDirectory "user\plugin\BingduYmmBridge"

foreach ($required in @($ymm, $template, (Join-Path $bridgeSource "BingduYmmBridge.dll"), (Join-Path $bridgeSource "BingduYmmBridge.deps.json"), (Join-Path $ymmDirectory "SoundTouch.Net.dll"))) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "缺少配音运行文件：$required"
    }
}

New-Item -ItemType Directory -Path $bridgeTarget -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $bridgeSource "BingduYmmBridge.dll") -Destination $bridgeTarget -Force
Copy-Item -LiteralPath (Join-Path $bridgeSource "BingduYmmBridge.deps.json") -Destination $bridgeTarget -Force
Copy-Item -LiteralPath (Join-Path $ymmDirectory "SoundTouch.Net.dll") -Destination $bridgeTarget -Force

$variables = @{
    BINGDU_VOICE_YMM_PATH = $ymm
    BINGDU_VOICE_TEMPLATE_PATH = $template
    BINGDU_VOICE_CHARACTER = $Character
}
foreach ($entry in $variables.GetEnumerator()) {
    [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value, "Machine")
    Set-Item -Path "Env:$($entry.Key)" -Value $entry.Value
}

if ($RegisterLogonTask) {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $action = New-ScheduledTaskAction -Execute $ymm -Argument ('"{0}"' -f $template) -WorkingDirectory $ymmDirectory
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity
    $principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    Register-ScheduledTask -TaskName "BingduVoiceRuntime" -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
}

if ($StartNow) {
    if (-not [Environment]::UserInteractive) {
        throw "YMM4 需要 Windows 交互式桌面和音频输出设备，当前会话不能启动配音运行时。"
    }
    Get-Process YukkuriMovieMaker -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Process -FilePath $ymm -ArgumentList ('"{0}"' -f $template) -WorkingDirectory $ymmDirectory -WindowStyle Minimized
    $connection = Join-Path $env:LOCALAPPDATA "BingduYmmBridge\connection.json"
    $deadline = [DateTime]::UtcNow.AddSeconds(60)
    while ([DateTime]::UtcNow -lt $deadline -and -not (Test-Path -LiteralPath $connection)) {
        Start-Sleep -Milliseconds 500
    }
    if (-not (Test-Path -LiteralPath $connection)) {
        throw "YMM4 已启动，但冰读配音桥未在 60 秒内就绪。请登录桌面检查插件和音频设备。"
    }
}

Write-Host "配音运行时已安装。请重启冰读 Web 后端，使机器级环境变量生效。"
Write-Host "YMM4: $ymm"
Write-Host "模板: $template"
Write-Host "默认角色: $Character"
