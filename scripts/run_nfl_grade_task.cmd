@echo off
REM Tuesday-morning task: grade finished games, then refresh data and
REM rebuild the model state for the new week.
REM grade.py refreshes games.csv itself; refresh-data/build/rebuild-state
REM pull the new week's play-by-play into the ratings.

setlocal
if "%ProjectDir%"=="" set "ProjectDir=%~dp0.."
if "%PythonExe%"=="" set "PythonExe=%ProjectDir%\.venv\Scripts\python.exe"
if not exist "%PythonExe%" set "PythonExe=python"

cd /d "%ProjectDir%"
if not exist "logs" mkdir logs

echo [%DATE% %TIME%] grade start >> "logs\nfl_grade.log"
"%PythonExe%" grade.py >> "logs\nfl_grade.log" 2>&1
set GRADE_EXIT=%ERRORLEVEL%

echo [%DATE% %TIME%] weekly data refresh >> "logs\nfl_grade.log"
"%PythonExe%" -m nfl_props.cli refresh-data >> "logs\nfl_grade.log" 2>&1
"%PythonExe%" -m nfl_props.cli build >> "logs\nfl_grade.log" 2>&1
"%PythonExe%" -m nfl_props.cli rebuild-state >> "logs\nfl_grade.log" 2>&1
set REFRESH_EXIT=%ERRORLEVEL%

echo [%DATE% %TIME%] grade_exit=%GRADE_EXIT% refresh_exit=%REFRESH_EXIT% >> "logs\nfl_grade.log"
if not "%GRADE_EXIT%"=="0" exit /b %GRADE_EXIT%
exit /b %REFRESH_EXIT%
