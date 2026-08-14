@echo off
REM Windows Task Scheduler entrypoint for the nfl_props live board.
REM Defaults: ProjectDir = repo root, PythonExe = .venv, SEND_DISCORD = false.
REM Uses NFL_DISCORD_WEBHOOK_URL only, so other sports' webhooks are untouched.
REM Loads User env from the registry so setx values work without a re-login.

setlocal
if "%ProjectDir%"=="" set "ProjectDir=%~dp0.."
if "%PythonExe%"=="" set "PythonExe=%ProjectDir%\.venv\Scripts\python.exe"
if not exist "%PythonExe%" set "PythonExe=python"

cd /d "%ProjectDir%"
if not exist "logs" mkdir logs

REM Pull User-level env (setx) into this process if not already present.
if not defined SEND_DISCORD (
  for /f "tokens=2*" %%A in ('reg query "HKCU\Environment" /v SEND_DISCORD 2^>nul') do set "SEND_DISCORD=%%B"
)
if not defined NFL_DISCORD_WEBHOOK_URL (
  for /f "tokens=2*" %%A in ('reg query "HKCU\Environment" /v NFL_DISCORD_WEBHOOK_URL 2^>nul') do set "NFL_DISCORD_WEBHOOK_URL=%%B"
)

if "%SEND_DISCORD%"=="" set "SEND_DISCORD=false"

set "ARGS=run_board.py"
if /I "%SEND_DISCORD%"=="true" set "ARGS=run_board.py --discord"
if /I "%SEND_DISCORD%"=="1" set "ARGS=run_board.py --discord"
if /I "%SEND_DISCORD%"=="yes" set "ARGS=run_board.py --discord"

"%PythonExe%" %ARGS% >> "logs\nfl_board.log" 2>&1
set EXITCODE=%ERRORLEVEL%
echo [%DATE% %TIME%] exit=%EXITCODE% >> "logs\nfl_board.log"
exit /b %EXITCODE%
