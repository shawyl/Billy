@echo off
setlocal EnableExtensions

set "APP_TITLE=Billy - Telegram Bill Split Bot"
set "PROJECT_DIR=%~dp0"

title Billy Launcher
cd /d "%PROJECT_DIR%"

echo ==========================================
echo Starting Billy - Telegram Bill Split Bot
echo ==========================================
echo.

if not exist "kill_existing_billy.ps1" (
    echo ERROR: kill_existing_billy.ps1 was not found.
    echo.
    echo Please place kill_existing_billy.ps1 in the same folder as launch_billy.bat.
    echo.
    pause
    exit /b 1
)

echo Closing existing Billy instances...
powershell -NoProfile -ExecutionPolicy Bypass -File "%PROJECT_DIR%kill_existing_billy.ps1"
if errorlevel 1 (
    echo.
    echo WARNING: Existing Billy cleanup returned a warning/error.
    echo Continuing startup anyway.
    echo.
)

echo.
echo Existing Billy check completed.
echo.

title %APP_TITLE%

if not exist ".env" (
    echo ERROR: .env file was not found.
    echo.
    echo Please copy .env.example to .env and fill in:
    echo - TELEGRAM_BOT_TOKEN
    echo - OLLAMA_BASE_URL
    echo - OLLAMA_TEXT_MODEL
    echo - OLLAMA_VISION_MODEL
    echo.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo.
        echo ERROR: Failed to create virtual environment.
        echo Make sure Python is installed and available in PATH.
        pause
        exit /b 1
    )
)

echo Activating virtual environment...
call ".venv\Scripts\activate.bat"
if errorlevel 1 (
    echo.
    echo ERROR: Failed to activate virtual environment.
    pause
    exit /b 1
)

echo Installing dependencies...
python -m pip install --upgrade pip
if errorlevel 1 (
    echo.
    echo ERROR: Failed to upgrade pip.
    pause
    exit /b 1
)

pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo ERROR: Failed to install dependencies.
    pause
    exit /b 1
)

echo Checking Ollama connection...
curl -s http://localhost:11434/api/tags >nul 2>&1
if errorlevel 1 (
    echo.
    echo WARNING: Ollama does not seem to be reachable at http://localhost:11434
    echo Please start Ollama before using Billy.
    echo.
    echo You can usually start it by opening the Ollama app,
    echo or by running: ollama serve
    echo.
    pause
)

echo.
echo Launching Billy...
echo Press CTRL+C to stop the bot.
echo.

python -m src.bot
if errorlevel 1 (
    echo.
    echo ERROR: Billy stopped with an error.
    pause
    exit /b 1
)

echo.
echo Billy stopped.
pause
