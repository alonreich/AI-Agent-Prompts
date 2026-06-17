@echo off
setlocal EnableDelayedExpansion

:: ============================================================================
:: Configuration
:: ============================================================================
set "PYTHONDONTWRITEBYTECODE=1"
set "MIN_PYTHON_VERSION=3.7"
set "TASK_NAME=AIAgentPromptBridge"

title AI Agent Prompt Controller - Professional Setup

:: ============================================================================
:: Path and Log Setup
:: ============================================================================
set "PROJECT_DIR=%~dp0"
set "PROJECT_DIR=%PROJECT_DIR:~0,-1%"
set "BRIDGE_PY=%PROJECT_DIR%\bridge.py"
set "LOG_DIR=%PROJECT_DIR%\logs"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
set "LOG_FILE=%LOG_DIR%\install.log"

:: Clear previous log file for a clean run
echo Installation started on %date% at %time% > "%LOG_FILE%"

:: ============================================================================
:: Logging Subroutine
:: ============================================================================
:LOG
echo [%~1] %~2
echo [%date% %time%] [%~1] %~2 >> "%LOG_FILE%"
goto :EOF

:: ============================================================================
:: Installer Header
:: ============================================================================
echo.
call :LOG "INFO" "AI Agent Prompt Controller Installer"
call :LOG "INFO" "=================================="
call :LOG "INFO" "This script will set up the application environment and configure persistence."
call :LOG "INFO" "Detailed logs will be saved to: %LOG_FILE%"
echo.

:: ============================================================================
:: STEP 1: Check for Administrative Privileges
:: ============================================================================
call :LOG "STEP 1/6" "Checking for Administrative Privileges..."
net session >nul 2>&1
if %errorLevel% neq 0 (
    call :LOG "FATAL" "Administrator rights are required to continue."
    echo.
    echo *****************************************************************
    echo *                        CRITICAL ERROR                         *
    echo *****************************************************************
    echo *                                                               *
    echo *      This installer requires Administrator privileges.        *
    echo *                                                               *
    echo *      Please right-click the Install.bat file and select       *
    echo *                 "Run as administrator".                       *
    echo *                                                               *
    echo *****************************************************************
    echo.
    call :LOG "HALT" "Installer stopped. User must re-run as administrator."
    timeout /t 15
    exit /b
)
call :LOG "OK" "Administrative access confirmed."
echo.

:: ============================================================================
:: STEP 2: Validate Python Environment
:: ============================================================================
call :LOG "STEP 2/6" "Validating Python Environment..."

python --version >nul 2>&1
if %errorLevel% neq 0 (
    goto :PYTHON_MISSING
)

for /f "tokens=2" %%v in ('python --version 2^>^&1') do set "DETECTED_PY_VER=%%v"
call :LOG "INFO" "Found Python version: %DETECTED_PY_VER%"

powershell -NoProfile -Command "if ([version]$env:DETECTED_PY_VER -lt [version]$env:MIN_PYTHON_VERSION) { exit 1 } else { exit 0 }"
if %errorLevel% neq 0 (
    goto :PYTHON_OLD
)

python -m pip --version >nul 2>&1
if %errorLevel% neq 0 (
    call :LOG "WARNING" "Pip is not detected. Attempting to install it..."
    python -m ensurepip --default-pip >>"%LOG_FILE%" 2>&1
    python -m pip --version >nul 2>&1
    if %errorLevel% neq 0 (
        goto :PIP_MISSING
    )
    call :LOG "OK" "Pip was successfully installed."
)
call :LOG "OK" "Python environment is healthy."
echo.

:: ============================================================================
:: STEP 3: Install Required Components
:: ============================================================================
call :LOG "STEP 3/6" "Installing required components (Flask, Watchdog)..."
call :LOG "INFO" "This may take a moment. Full output is being logged."
python -m pip install flask flask-cors watchdog >>"%LOG_FILE%" 2>&1
if %errorLevel% neq 0 (
    call :LOG "ERROR" "Failed to install dependencies via pip."
    call :LOG "INFO" "Please check your network connection and proxy settings."
    call :LOG "INFO" "Review the log for details: %LOG_FILE%"
    goto :INSTALL_FAIL
)
call :LOG "OK" "All Python components are ready."
echo.

:: ============================================================================
:: STEP 4: Deploy System Logic
:: ============================================================================
call :LOG "STEP 4/6" "Deploying system logic (bridge.py)..."

set "MASTER_PY=%PROJECT_DIR%\bridge_master.py"
if not exist "%MASTER_PY%" (
    call :LOG "ERROR" "Core logic file 'bridge_master.py' not found in project directory."
    goto :INSTALL_FAIL
)
copy /Y "%MASTER_PY%" "%BRIDGE_PY%" >nul 2>&1
if exist "%BRIDGE_PY%" (
    call :LOG "OK" "bridge.py deployed from bridge_master.py."
) else (
    call :LOG "ERROR" "Failed to deploy bridge.py."
    goto :INSTALL_FAIL
)
echo.

:: ============================================================================
:: STEP 5: Manage Background Processes
:: ============================================================================
call :LOG "STEP 5/6" "Managing background processes..."
call :LOG "INFO" "Checking for existing application processes on port 5589..."
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :5589 ^| findstr LISTENING') do (
    call :LOG "INFO" "Stopping existing Bridge process [PID: %%a]..."
    taskkill /F /PID %%a >>"%LOG_FILE%" 2>&1
)

call :LOG "INFO" "Waiting for port to become available..."
timeout /t 2 /nobreak >nul
call :LOG "OK" "Port 5589 is clear."
echo.

:: ============================================================================
:: STEP 6: Configure Persistence
:: ============================================================================
call :LOG "STEP 6/6" "Configuring Persistence for auto-start..."

call :LOG "INFO" "Cleaning up any old persistence tasks or shortcuts..."
schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1
schtasks /delete /tn "AIAgentBridge" /f >nul 2>&1
if exist "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\AIAgentBridge.lnk" del /f /q "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\AIAgentBridge.lnk" >nul 2>&1
if exist "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\AIAgentPromptBridge.lnk" del /f /q "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\AIAgentPromptBridge.lnk" >nul 2>&1

call :LOG "INFO" "Attempting to create Task Scheduler job for persistence (Recommended)..."
schtasks /create /tn "%TASK_NAME%" /tr "cmd /c cd /d \"%PROJECT_DIR%\" ^& set PYTHONDONTWRITEBYTECODE=1 ^& pythonw.exe \"%BRIDGE_PY%\"" /sc onlogon /rl highest /f >>"%LOG_FILE%" 2>&1

if %errorLevel% equ 0 (
    call :LOG "OK" "Task Scheduler job '%TASK_NAME%' created successfully."
    call :LOG "INFO" "The bridge will now auto-start on Windows login and run in the background."
) else (
    call :LOG "WARNING" "Failed to create Task Scheduler job. This can happen on some systems."
    call :LOG "INFO" "Falling back to creating a shortcut in the Startup folder..."
    
    set "LOCAL_AUTO_START=%LOCALAPPDATA%\AIAgentPromptController"
    if not exist "%LOCAL_AUTO_START%" mkdir "%LOCAL_AUTO_START%"
    set "VBS_PATH=%LOCAL_AUTO_START%\silent_start.vbs"

    powershell -NoProfile -Command "$vbs = 'Set WinScriptHost = CreateObject(\"WScript.Shell\")' + [char]10 + 'WinScriptHost.Run \"pythonw.exe \" & Chr(34) & \"%BRIDGE_PY%\" & Chr(34), 0' + [char]10 + 'Set WinScriptHost = Nothing'; $vbs | Out-File -LiteralPath '%VBS_PATH%' -Encoding ascii" >>"%LOG_FILE%" 2>&1

    set "STARTUP_FOLDER=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
    set "SHORTCUT_PATH=%STARTUP_FOLDER%\AIAgentPromptBridge.lnk"
    powershell -NoProfile -Command "$WshShell = New-Object -ComObject WScript.Shell; $Shortcut = $WshShell.CreateShortcut('%SHORTCUT_PATH%'); $Shortcut.TargetPath = '%VBS_PATH%'; $Shortcut.WorkingDirectory = '%PROJECT_DIR%'; $Shortcut.Save()" >>"%LOG_FILE%" 2>&1
    call :LOG "OK" "Startup folder shortcut created successfully."
)
echo.

:: ============================================================================
:: Finalization
:: ============================================================================
call :LOG "SUCCESS" "INSTALLATION COMPLETE"
echo ============================================================
echo The AI Agent Prompt Controller is now running in the background.
echo.
echo   URL:  http://127.0.0.1:5589
echo   LOGS: %LOG_FILE%
echo.
call :LOG "INFO" "Starting the application now..."
schtasks /run /tn "%TASK_NAME%" >nul 2>&1
if %errorLevel% neq 0 (
    if exist "%VBS_PATH%" wscript.exe "%VBS_PATH%" >nul 2>&1
)
timeout /t 5
exit /b

:: ============================================================================
:: Failure Handlers
:: ============================================================================
:PYTHON_MISSING
call :LOG "FATAL" "Python is not detected in the system's PATH."
echo [CRITICAL ERROR] PYTHON NOT DETECTED
echo Step 1: Download Python from https://www.python.org/
echo Step 2: **CRITICAL** Check "Add Python to PATH" during install.
goto :INSTALL_FAIL

:PYTHON_OLD
call :LOG "FATAL" "Python version is outdated. Found %DETECTED_PY_VER%, requires %MIN_PYTHON_VERSION% or newer."
echo [CRITICAL ERROR] PYTHON VERSION OUTDATED (Min: %MIN_PYTHON_VERSION%)
goto :INSTALL_FAIL

:PIP_MISSING
call :LOG "FATAL" "Pip could not be detected or installed."
echo [CRITICAL ERROR] PIP NOT DETECTED
echo Please ensure Python is installed correctly and try again.
echo Review the log for details: %LOG_FILE%
goto :INSTALL_FAIL

:INSTALL_FAIL
echo.
call :LOG "FAILURE" "Installation did not complete successfully."
echo An error occurred. Please review the output above and check the log file:
echo %LOG_FILE%
timeout /t 30
exit /b
