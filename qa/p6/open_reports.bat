@echo off
cd /d "%~dp0..\.."

echo Opening test documents and report folder...
for %%f in ("docs\testing\p6\*.pdf") do start "" "%%~f"
start "" "qa\p6\reports"

echo.
echo Report folder: qa\p6\reports
echo - acceptance-latest.md : 40 acceptance cases
echo - defect-ledger.md     : 12 defects
echo - model-compare\model-compare-summary.md : model comparison status
echo.
echo Tip: open .md files in VS Code and press Ctrl+Shift+V to preview.
pause
