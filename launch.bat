@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
    py -3 "%~dp0source_launcher.py" %*
) else (
    python "%~dp0source_launcher.py" %*
)
if errorlevel 1 (
    echo.
    echo Startup failed. Log: .launcher-state\startup.log
    pause
    exit /b 1
)
exit /b 0
