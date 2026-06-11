# Build XT POS installer + update package directly (mirrors build-setup.bat).
# Skips build-setup.bat's flaky `pip install || goto :error` line; the venv
# already has every build dependency. Run from the project root.
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
Set-Location $root

# The pyinstaller.exe console-script launcher is broken under this venv's
# Python 3.14, so drive PyInstaller via `python -m PyInstaller` instead.
$pyExe = Join-Path $root '.venv\Scripts\python.exe'

$OUT      = Join-Path $root 'setup'
$WORK     = Join-Path $root 'setup\build'
$WORKPATH = Join-Path $root 'setup\build\_pyi'
$STAGE    = Join-Path $root 'setup\build\app'

function Run($exe, $argList) {
    Write-Host ">> $exe $($argList -join ' ')"
    & $exe @argList
    if ($LASTEXITCODE -ne 0) { throw "FAILED ($LASTEXITCODE): $exe $($argList -join ' ')" }
}

Write-Host 'Cleaning previous build output...'
foreach ($d in @($OUT, (Join-Path $root 'build'), (Join-Path $root 'dist'))) {
    if (Test-Path $d) { Remove-Item -Recurse -Force $d }
}

Write-Host '[1/4] Compiling the POS app (POS.exe)...'
Run $pyExe @('-m', 'PyInstaller', 'pos.spec',       '--noconfirm', '--distpath', $WORK, '--workpath', $WORKPATH)
Write-Host '[2/4] Building the uninstaller...'
Run $pyExe @('-m', 'PyInstaller', 'uninstall.spec', '--noconfirm', '--distpath', $WORK, '--workpath', $WORKPATH)
Write-Host '[3/4] Building the updater...'
Run $pyExe @('-m', 'PyInstaller', 'update.spec',    '--noconfirm', '--distpath', $WORK, '--workpath', $WORKPATH)

Write-Host "Staging the three executables into $STAGE ..."
New-Item -ItemType Directory -Force $STAGE | Out-Null
foreach ($exe in @('POS.exe', 'Uninstall.exe', 'Update.exe')) {
    Copy-Item -Force (Join-Path $WORK $exe) (Join-Path $STAGE $exe)
}

Write-Host '[4/4] Building the installer...'
Run $pyExe @('-m', 'PyInstaller', 'setup.spec', '--noconfirm', '--distpath', $OUT, '--workpath', $WORKPATH)

Write-Host 'Packaging the update (zip + manifest) into release\ ...'
# Only pass --base-url when set; an empty value would make argparse error.
$mkArgs = @('make_update.py', '--source', $STAGE)
if ($env:UPDATE_BASE_URL) { $mkArgs += @('--base-url', $env:UPDATE_BASE_URL) }
Run $pyExe $mkArgs

Write-Host 'Cleaning up intermediates...'
if (Test-Path $WORK) { Remove-Item -Recurse -Force $WORK }

$ver = (Get-Content (Join-Path $root 'VERSION')).Trim()
Write-Host '============================================================'
Write-Host " Done. v$ver"
Write-Host "   Installer:  setup\XTPOS-Setup.exe"
Write-Host "   Update zip: release\XTPOS-$ver.zip  (+ release\manifest.json)"
Write-Host '============================================================'
