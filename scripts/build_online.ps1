param([string]$Python = '', [switch]$FetchSources)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
if (-not $Python) {
    foreach ($candidate in @((Join-Path $root '.venv64\Scripts\python.exe'), (Join-Path $root '..\..\.venv64\Scripts\python.exe'))) {
        if (Test-Path -LiteralPath $candidate) { $Python = (Resolve-Path -LiteralPath $candidate).Path; break }
    }
}
if (-not $Python -or -not (Test-Path -LiteralPath $Python)) { throw 'A Python build environment is required; pass -Python.' }
$previousDeployment = $env:VITE_BINGDU_DEPLOYMENT
Push-Location $root
try {
    $env:VITE_BINGDU_DEPLOYMENT = 'online'
    npm run build
    if ($LASTEXITCODE -ne 0) { throw 'Online frontend build failed' }
    $arguments = @('scripts/prepare_legal.py')
    if ($FetchSources) { $arguments += '--fetch' }
    & $Python @arguments
    if ($LASTEXITCODE -ne 0) { throw 'Online legal preparation failed' }
    & $Python scripts/verify_legal_bundle.py build/legal
    if ($LASTEXITCODE -ne 0) { throw 'Online legal verification failed' }
    $releaseName = 'online-release-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff')
    $releaseDirectory = Join-Path $root "build/$releaseName"
    foreach ($service in @('web','cloud')) {
        & $Python -m PyInstaller --noconfirm --clean --distpath $releaseDirectory --workpath "build/online-pyinstaller-$service" "scripts/online_$service.spec"
        if ($LASTEXITCODE -ne 0) { throw "Online $service packaging failed" }
    }
    foreach ($service in @('IceReaderWeb','IceReaderCloud')) {
        $target = Join-Path $releaseDirectory "$service/legal"
        Copy-Item -LiteralPath build/legal -Destination $target -Recurse -Force
        & $Python scripts/verify_legal_bundle.py $target
        if ($LASTEXITCODE -ne 0) { throw "Packaged $service legal verification failed" }
    }
    $pointer = Join-Path $root 'build/online-current.txt'
    [System.IO.File]::WriteAllText($pointer + '.tmp', $releaseName)
    Move-Item -LiteralPath ($pointer + '.tmp') -Destination $pointer -Force
    Write-Output "Online release ready: $releaseDirectory; configure BINGDU_LEGAL_DIR to each service/legal directory."
} finally {
    if ($null -eq $previousDeployment) { Remove-Item Env:VITE_BINGDU_DEPLOYMENT -ErrorAction SilentlyContinue }
    else { $env:VITE_BINGDU_DEPLOYMENT = $previousDeployment }
    Pop-Location
}
