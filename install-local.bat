@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -3.12 -m venv .venv
  if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -m pip install -e ".[local]"
if errorlevel 1 goto failed
echo ready>".venv\.listenerpqr-ready"
echo Local speech recognition is ready. Run start.bat and select local recognition in Settings.
echo The selected model will be downloaded on first use, unless you provide a local model directory.
pause
exit /b 0
:failed
echo Installation failed. Install Python 3.12 x64 and check your network connection.
pause
exit /b 1
