@echo off
title Bullshit Uninstaller

echo Stopping Bullshit.exe if it is running...
taskkill /IM "Bullshit.exe" /F >nul 2>nul

echo.
echo Removing startup script...

REM resolve startup folder path
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "VBS_FILE=%STARTUP%\FoxyJumpscare.vbs"

if exist "%VBS_FILE%" (
    del "%VBS_FILE%" >nul 2>nul
REM    echo Removed "%VBS_FILE%".
) else (
REM    echo No startup script found at "%VBS_FILE%".
)

echo.
echo Done!
echo You will no longer get jumpscared :3...
echo.
pause
