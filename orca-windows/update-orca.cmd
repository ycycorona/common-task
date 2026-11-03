@echo off
setlocal
rem Windows entry point for update-orca.ps1 (see that file for full help).
set "PS=powershell"
where pwsh >nul 2>nul && set "PS=pwsh"
"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0update-orca.ps1" %*
exit /b %ERRORLEVEL%
