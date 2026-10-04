param([switch]$Debug, [string]$Python = $env:BINGDU_BUILD_PYTHON, [string]$Gradle = '')
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
function Run-Step([string]$Command, [string[]]$Arguments) {
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Command failed with exit code $LASTEXITCODE" }
}
if (!$Python) { throw 'Set BINGDU_BUILD_PYTHON to a 64-bit Python 3.12 executable.' }
Run-Step $Python @('-c', 'import sys,struct; assert sys.version_info[:2] == (3,12) and struct.calcsize(chr(80))==8')
if (!$env:ANDROID_NDK_HOME) { throw 'Set ANDROID_NDK_HOME to Android NDK r27d.' }
if (!$env:ANDROID_HOME -or !$env:JAVA_HOME) { throw 'Set ANDROID_HOME (API 36, build tools 36.0.0) and JAVA_HOME (JDK 21).' }
if (!$Debug -and !$env:BINGDU_ANDROID_KEYSTORE) { throw 'Release requires BINGDU_ANDROID_KEYSTORE, BINGDU_ANDROID_STORE_PASSWORD, BINGDU_ANDROID_KEY_ALIAS and BINGDU_ANDROID_KEY_PASSWORD.' }
$env:BINGDU_BUILD_PYTHON = $Python
$env:PYTHONUTF8 = '1'
if (!$Gradle) { $Gradle = (Resolve-Path 'android/gradlew.bat').Path }
Run-Step 'npm.cmd' @('ci', '--ignore-scripts')
Run-Step $Python @('scripts/prepare_android_assets.py')
Run-Step $Python @('scripts/build_android_native.py')
Run-Step 'npm.cmd' @('run', 'build')
Run-Step 'npx.cmd' @('cap', 'sync', 'android')
# Resolve embedded Python wheels before collecting their exact license metadata.
Run-Step $Gradle @('-p', 'android', ':app:generateDebugPythonRequirementsAssets', ':app:androidDependencyInventory', '--console=plain')
Run-Step $Python @('scripts/prepare_android_licenses.py')
Run-Step $Python @('scripts/prepare_legal.py', '--android', '--fetch', '--output', 'android/app/src/main/assets/legal')
$variant = if ($Debug) { 'Debug' } else { 'Release' }
Run-Step $Gradle @('-p', 'android', "assemble$variant", '--console=plain')
$source = "android/app/build/outputs/apk/$($variant.ToLower())/app-$($variant.ToLower()).apk"
$destination = 'build/android-release'
New-Item -ItemType Directory -Path $destination -Force | Out-Null
$version = (Get-Content package.json -Raw | ConvertFrom-Json).version
$name = if ($Debug) { "IceReader-$version-debug.apk" } else { "IceReader-$version.apk" }
$output = Join-Path $destination $name
Copy-Item -LiteralPath $source -Destination $output -Force
Run-Step (Join-Path $env:ANDROID_HOME 'build-tools/36.0.0/apksigner.bat') @('verify', '--verbose', $output)
(Get-FileHash -LiteralPath $output -Algorithm SHA256).Hash.ToLower() + "  $name" | Set-Content "$output.sha256" -Encoding ascii
Write-Output "APK: $((Resolve-Path $output).Path)"
