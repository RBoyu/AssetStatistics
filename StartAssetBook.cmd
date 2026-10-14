@echo off
setlocal
cd /d "%~dp0"
where uv >nul 2>&1
if errorlevel 1 goto missing_uv
uv run --locked assetbook %*
set "assetbook_exit=%errorlevel%"
if not "%assetbook_exit%"=="0" pause
exit /b %assetbook_exit%
:missing_uv
echo uv was not found. Install uv, reopen this window, and try again.
echo Instructions: https://docs.astral.sh/uv/getting-started/installation/
pause
exit /b 1
