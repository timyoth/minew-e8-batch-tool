@echo off
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo Local Python environment not found. Run setup.cmd first. 1>&2
  exit /b 1
)
"%~dp0.venv\Scripts\python.exe" "%~dp0batch_configure.py" %*
exit /b %errorlevel%
