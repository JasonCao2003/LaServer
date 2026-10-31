@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" -Setup
set "service_exit_code=%ERRORLEVEL%"
if not "%service_exit_code%"=="0" echo Service command failed. Exit code: %service_exit_code%
echo.
pause
exit /b %service_exit_code%
