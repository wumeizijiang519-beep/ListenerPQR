@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  where py >nul 2>nul
  if errorlevel 1 goto no_python
  py -3.12 -m venv .venv
  if errorlevel 1 goto no_python
)
if not exist ".venv\.listenerpqr-ready" (
  ".venv\Scripts\python.exe" -m pip install -e .
  if errorlevel 1 goto failed
  echo ready>".venv\.listenerpqr-ready"
)
".venv\Scripts\python.exe" -m listenerpqr.app
if errorlevel 1 goto failed
exit /b 0
:no_python
echo Install Python 3.12 x64 from https://www.python.org/downloads/windows/
echo Keep the Python Launcher option enabled, then run start.bat again.
pause
exit /b 1
:failed
echo ListenerPQR could not start. Check the error above and your network connection.
pause
exit /b 1
