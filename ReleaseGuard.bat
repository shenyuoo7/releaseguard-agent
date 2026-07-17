@echo off
setlocal
chcp 65001 >nul

set "RELEASEGUARD_ROOT=%~dp0"
set "RELEASEGUARD_LAUNCHER=%RELEASEGUARD_ROOT%scripts\simple_launcher.ps1"

if not exist "%RELEASEGUARD_LAUNCHER%" (
    echo [ERROR] Launcher script not found: "%RELEASEGUARD_LAUNCHER%"
    echo Restore the project files and try again.
    if "%~1"=="" pause
    exit /b 2
)

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%RELEASEGUARD_LAUNCHER%" %*
set "RELEASEGUARD_EXIT_CODE=%ERRORLEVEL%"

if not "%RELEASEGUARD_EXIT_CODE%"=="0" if "%~1"=="" (
    echo.
    echo The launcher failed. Review the error message above.
    pause
)

exit /b %RELEASEGUARD_EXIT_CODE%
