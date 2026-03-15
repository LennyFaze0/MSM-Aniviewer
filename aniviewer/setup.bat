@echo off
echo ========================================
echo My Singing Monsters Animation Viewer
echo Setup Script
echo ========================================
echo.

echo Checking Python installation...
python --version
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH
    echo Please install Python 3.10 or higher from python.org
    pause
    exit /b 1
)
echo.

echo Installing required packages...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
echo.
echo Ensuring PSD dependencies are available...
python -m utils.pytoshop_installer --package pytoshop --min-version 1.2.1 --preinstall
python -m utils.pytoshop_installer --package packbits --min-version 0.1.0 --preinstall

if errorlevel 1 (
    echo.
    echo ERROR: Failed to install some packages
    echo Please check the error messages above
    pause
    exit /b 1
)

echo.
echo ========================================
echo Setup completed successfully!
echo ========================================
echo.
echo To run the application, execute:
echo     python main.py
echo.
echo Or simply double-click: run_viewer.bat
echo.
pause
