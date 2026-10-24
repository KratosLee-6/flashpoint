@echo off
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dpn0.ps1" %*
set "SPARK_LAUNCH_EXIT=%errorlevel%"
if not "%SPARK_LAUNCH_EXIT%"=="0" (
  echo.
  echo [AI--boom] Launch failed. The error is shown above.
  pause
)
exit /b %SPARK_LAUNCH_EXIT%
