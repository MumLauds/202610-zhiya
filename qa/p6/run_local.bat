@echo off
cd /d "%~dp0..\.."

echo ============================================================
echo Zhiya P6 local test: prepare data -^> start server -^> run tests
echo ============================================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv\Scripts\python.exe not found.
    echo Run these commands in the repository root first:
    echo   python -m venv .venv
    echo   .venv\Scripts\python.exe -m pip install -r backend\requirements-dev.txt
    echo.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" qa\p6\run_p6.py --start-local --db qa\p6\instance\zhiya.sqlite3
set "p6_exit=%errorlevel%"

echo.
echo P6 exit code %p6_exit%. Reports are in qa\p6\reports
pause
exit /b %p6_exit%
