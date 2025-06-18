@echo off
ECHO ================================================================
ECHO              Simulation Environment Setup
ECHO ================================================================
ECHO This script will set up a clean Python environment for the
ECHO simulation GUI to ensure it has all the needed libraries.
ECHO This may take a minute on the first run.
ECHO.

REM Check if Python is installed and available
python --version >nul 2>&1
IF %ERRORLEVEL% NEQ 0 (
    ECHO FATAL ERROR: Python is not installed or not found in your PATH.
    ECHO Please install Python 3 from python.org and try again.
    pause
    exit /b
)

REM Set the name for our local virtual environment folder
SET VENV_DIR=venv

REM Check if the virtual environment folder already exists
IF NOT EXIST "%VENV_DIR%\Scripts\activate.bat" (
    ECHO Creating a new virtual environment...
    python -m venv %VENV_DIR%
    IF %ERRORLEVEL% NEQ 0 (
        ECHO Failed to create virtual environment. Please check your Python installation.
        pause
        exit /b
    )
)

ECHO Activating the environment...
call "%VENV_DIR%\Scripts\activate.bat"

ECHO Installing required libraries (numpy, matplotlib)...
pip install -r requirements.txt

ECHO.
ECHO ================================================================
ECHO                  Launching Simulation GUI
ECHO ================================================================
ECHO.
python combined_gui.py

ECHO.
ECHO GUI has been closed. Press any key to exit.
pause >nul