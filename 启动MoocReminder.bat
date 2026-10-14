@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

rem Clear the "downloaded from the internet" mark Windows puts on files from an
rem extracted ZIP, otherwise it shows a publisher warning every launch. Only
rem that mark is removed; file contents are never modified.
powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command ^
  "$root='%~dp0';" ^
  "$marked=@(Get-ChildItem -LiteralPath $root -Recurse -File -ErrorAction SilentlyContinue | Where-Object { Get-Item -LiteralPath $_.FullName -Stream Zone.Identifier -ErrorAction SilentlyContinue });" ^
  "foreach ($item in $marked) { Unblock-File -LiteralPath $item.FullName -ErrorAction SilentlyContinue }" >nul 2>nul

rem No argument is a normal launch: a script host opens the startup window with
rem no console window. An argument goes to the launcher, so diagnostics such as
rem --check-runtime remain available.
if "%~1"=="" (
    start "" wscript.exe "%~dp0launch.vbs"
    exit /b 0
)

where python >nul 2>nul
if errorlevel 1 (
    py -3 "%~dp0source_launcher.py" %*
) else (
    python "%~dp0source_launcher.py" %*
)
if errorlevel 1 (
    echo.
    echo 启动失败。日志位置：.launcher-state\startup.log
    pause
    exit /b 1
)
exit /b 0
