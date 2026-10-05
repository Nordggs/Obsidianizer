@echo off
rem Release build for Obsidianizer (Windows).
rem Produces dist\Obsidianizer\ ready for zipping / Inno Setup.
rem
rem IMPORTANT: Obsidianizer.exe.config MUST sit next to Obsidianizer.exe —
rem it enables loadFromRemoteSources so the app starts from archives
rem downloaded from the internet (Mark-of-the-Web), see v0.5.1.

.venv\Scripts\python.exe -m PyInstaller Obsidianizer.spec --distpath dist --workpath build --noconfirm
if errorlevel 1 exit /b 1

copy /Y Obsidianizer.exe.config dist\Obsidianizer\ >nul
echo Config copied: dist\Obsidianizer\Obsidianizer.exe.config

xcopy /E /I /Y integration dist\Obsidianizer\integration >nul
echo Integration folder copied: dist\Obsidianizer\integration
