@echo off
py -3.12 -m venv "%~dp0.venv"
if errorlevel 1 exit /b %errorlevel%
"%~dp0.venv\Scripts\python.exe" -m pip install -r "%~dp0requirements.txt"
exit /b %errorlevel%
