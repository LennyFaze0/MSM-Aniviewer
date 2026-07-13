@echo off
echo My Singing Monsters Animation Viewer
echo.
echo Select launch mode:
echo   1 - Normal mode
echo   2 - Basic mode
echo.
choice /c 12 /n /m "Press 1 or 2: "
set "ANIVIEWER_ARGS="
if errorlevel 2 set "ANIVIEWER_ARGS=--simple-mode"
echo.
echo Starting My Singing Monsters Animation Viewer...
python -u main.py %ANIVIEWER_ARGS%
pause
