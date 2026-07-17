@echo off
setlocal

set "RELEASEGUARD_ROOT=%~dp0"
set "RELEASEGUARD_PYTHON=%RELEASEGUARD_ROOT%.venv\Scripts\python.exe"

if not exist "%RELEASEGUARD_PYTHON%" (
    echo ReleaseGuard virtual environment not found: "%RELEASEGUARD_PYTHON%" 1>&2
    exit /b 2
)

set "PYTHONPATH=%RELEASEGUARD_ROOT%src"
set "PYTHONDONTWRITEBYTECODE=1"

if "%~1"=="" (
    "%RELEASEGUARD_PYTHON%" -m releaseguard_agent.cli.main --help
) else (
    "%RELEASEGUARD_PYTHON%" -m releaseguard_agent.cli.main %*
)

exit /b %ERRORLEVEL%
