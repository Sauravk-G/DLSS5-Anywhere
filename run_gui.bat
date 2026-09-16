@echo off
title Launching DLSS5-Anywhere GUI
echo ========================================================
echo   Starting DLSS5-Anywhere GUI Suite...
echo ========================================================
python main.py
if errorlevel 1 (
    echo.
    echo An error occurred. Press any key to exit.
    pause
)
