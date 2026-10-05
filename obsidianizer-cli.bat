@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m obsidianizer.cli %*
) else (
    python -m obsidianizer.cli %*
)
set EXITCODE=%ERRORLEVEL%
endlocal & exit /b %EXITCODE%
