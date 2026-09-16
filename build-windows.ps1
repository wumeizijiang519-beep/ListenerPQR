param([switch]$CloudOnly)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not (Test-Path '.venv\Scripts\python.exe')) {
    py -3.12 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.12 x64 first.' }
}
$python = '.venv\Scripts\python.exe'
$extra = if ($CloudOnly) { '.[dev]' } else { '.[dev,local]' }
& $python -m pip install -e $extra
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
& $python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'Tests failed.' }
$buildArgs = @('-m', 'PyInstaller', '--noconfirm', '--clean', '--windowed', '--onedir', '--noupx',
    '--name', 'ListenerPQR', '--collect-all', 'sounddevice', '--collect-all', '_sounddevice_data',
    '--hidden-import', 'keyring.backends.Windows', '--collect-data', 'certifi')
if (-not $CloudOnly) {
    $buildArgs += @('--collect-all', 'faster_whisper', '--collect-all', 'ctranslate2',
        '--collect-all', 'tokenizers', '--collect-all', 'onnxruntime', '--collect-all', 'av')
}
& $python @buildArgs launcher.py
if ($LASTEXITCODE -ne 0) { throw 'Packaging failed.' }
Copy-Item README.md, LICENSE, THIRD_PARTY_NOTICES.md dist\ListenerPQR\
& $python -m pip freeze | Set-Content -Encoding UTF8 dist\ListenerPQR\BUILD-DEPENDENCIES.txt
& $python scripts\collect_licenses.py dist\ListenerPQR\third-party-licenses
if ($LASTEXITCODE -ne 0) { throw 'License collection failed.' }
Remove-Item smoke-ok.txt -ErrorAction SilentlyContinue
$process = Start-Process -FilePath 'dist\ListenerPQR\ListenerPQR.exe' -ArgumentList '--smoke-test' -PassThru
if (-not $process.WaitForExit(60000)) { $process.Kill(); throw 'Packaged app did not start within 60 seconds.' }
if ($process.ExitCode -ne 0 -or -not (Test-Path 'smoke-ok.txt')) { throw 'Packaged app smoke test failed.' }
Compress-Archive -Path dist\ListenerPQR -DestinationPath dist\ListenerPQR-Win11-x64.zip -Force
Get-FileHash dist\ListenerPQR-Win11-x64.zip -Algorithm SHA256 | Format-List
Write-Host 'Ready: dist\ListenerPQR-Win11-x64.zip'
