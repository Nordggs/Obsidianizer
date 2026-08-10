@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" "Obsidianizer.py" %*
) else (
    python "Obsidianizer.py" %*
)

set EXITCODE=%ERRORLEVEL%
endlocal & exit /b %EXITCODE%