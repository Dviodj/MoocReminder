@echo off
chcp 65001 >nul
rem Developer entry point for a checked-out copy; the released package ships the
rem Chinese-named launcher only. Both run the same code path.
call "%~dp0启动MoocReminder.bat" %*
exit /b %errorlevel%
