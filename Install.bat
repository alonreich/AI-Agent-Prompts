@echo off
setlocal EnableDelayedExpansion

:: ============================================================================
:: Configuration
:: ============================================================================
set "PYTHONDONTWRITEBYTECODE=1"
set "MIN_PYTHON_VERSION=3.7"
set "TASK_NAME=AIAgentPromptBridge"
set "BRIDGE_URL=http://127.0.0.1:5589"

title AI Agent Prompt Controller - Professional Setup

:: ============================================================================
:: Path and Log Setup
:: ============================================================================
set "PROJECT_DIR=%~dp0"
set "PROJECT_DIR=%PROJECT_DIR:~0,-1%"
set "BRIDGE_PY=%PROJECT_DIR%\bridge.py"
set "LOG_DIR=%TMP%\AI-Agent-Prompt"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
set "LOG_FILE=%LOG_DIR%\install.log"
set "BRIDGE_LOG=%LOG_DIR%\bridge.log"

:: Clear previous log file for a clean run
echo Installation started on %date% at %time% > "%LOG_FILE%"

:: Jump over subroutines (execution must never fall into a subroutine body)
goto :MAIN

:: ============================================================================
:: Logging Subroutine
:: ============================================================================
:LOG
echo [%~1] %~2
echo [%date% %time%] [%~1] %~2 >> "%LOG_FILE%"
goto :EOF

:: ============================================================================
:: Health-Probe Subroutine (sets HEALTH_OK=1 if bridge answers /api/ping)
:: ============================================================================
:PROBE_BRIDGE
set "HEALTH_OK=0"
powershell -NoProfile -Command "try { $r = Invoke-RestMethod -Uri '%BRIDGE_URL%/api/ping' -TimeoutSec 3; if ($r.status -eq 'ok') { exit 0 }; exit 1 } catch { exit 1 }" >nul 2>&1
if %errorLevel% equ 0 set "HEALTH_OK=1"
goto :EOF

:MAIN
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
call :LOG "STEP 1/8" "Checking for Administrative Privileges..."
net session >nul 2>&1
if %errorLevel% neq 0 goto :NOT_ADMIN
call :LOG "OK" "Administrative access confirmed."
echo.

:: ============================================================================
:: STEP 2: Choose Installation Location (nothing is ever hardcoded)
:: ============================================================================
call :LOG "STEP 2/8" "Choosing installation location..."
echo.
echo   Where would you like the application to live?
echo.
echo     [1] Right here - run from this folder:
echo         %PROJECT_DIR%
echo.
echo     [2] Standard location - copy the project to:
echo         C:\AI-Agent-Prompt
echo.
choice /c 12 /n /t 30 /d 1 /m "  Choose 1 or 2 (defaults to 1 after 30 seconds): "
if %errorLevel% equ 2 (
    set "TARGET_DIR=C:\AI-Agent-Prompt"
) else (
    set "TARGET_DIR=%PROJECT_DIR%"
)

if /i not "%TARGET_DIR%"=="%PROJECT_DIR%" (
    call :LOG "INFO" "Copying application files to %TARGET_DIR% ..."
    robocopy "%PROJECT_DIR%" "%TARGET_DIR%" /E /XD ".git" "__pycache__" ".venv" "venv" /XF "*.log" "*.pyc" >>"%LOG_FILE%" 2>&1
    if !errorLevel! geq 8 (
        call :LOG "ERROR" "File copy to %TARGET_DIR% failed. See log for details: %LOG_FILE%"
        goto :INSTALL_FAIL
    )
    set "PROJECT_DIR=%TARGET_DIR%"
    set "BRIDGE_PY=%TARGET_DIR%\bridge.py"
    if not exist "!BRIDGE_PY!" (
        call :LOG "ERROR" "bridge.py is missing at the destination after copy. Aborting."
        goto :INSTALL_FAIL
    )
    call :LOG "OK" "Files copied. The application will run from: %TARGET_DIR%"
    call :LOG "INFO" "Your original folder was left untouched; you may delete it later if desired."
) else (
    call :LOG "OK" "Running from current location: %PROJECT_DIR%"
)
echo.

:: ============================================================================
:: STEP 3: Validate Python Environment (probes python, then the py launcher)
:: ============================================================================
call :LOG "STEP 3/8" "Validating Python Environment..."

set "PY_CMD="
python --version >nul 2>&1
if %errorLevel% equ 0 set "PY_CMD=python"

if not defined PY_CMD (
    py -3 --version >nul 2>&1
    if !errorLevel! equ 0 (
        set "PY_CMD=py -3"
        call :LOG "INFO" "Using the Windows 'py' launcher (python.exe not on PATH)."
    )
)

if not defined PY_CMD goto :PYTHON_MISSING

for /f "tokens=2" %%v in ('%PY_CMD% --version 2^>^&1') do set "DETECTED_PY_VER=%%v"
call :LOG "INFO" "Found Python version: %DETECTED_PY_VER%"

:: Strip pre-release suffixes (e.g. 3.13.0rc1) before comparing versions
powershell -NoProfile -Command "$v = $env:DETECTED_PY_VER -replace '[^0-9.].*$',''; if ([version]$v -lt [version]$env:MIN_PYTHON_VERSION) { exit 1 } else { exit 0 }"
if %errorLevel% neq 0 goto :PYTHON_OLD

:: Resolve full interpreter paths so persistence never depends on logon PATH
set "PYTHON_EXE="
for /f "delims=" %%p in ('%PY_CMD% -c "import sys; print(sys.executable)"') do set "PYTHON_EXE=%%p"
if not defined PYTHON_EXE goto :PYTHON_MISSING
if not exist "%PYTHON_EXE%" goto :PYTHON_MISSING

for %%p in ("%PYTHON_EXE%") do set "PY_HOME=%%~dpp"
set "PYTHONW_EXE=%PY_HOME%pythonw.exe"
if not exist "%PYTHONW_EXE%" (
    call :LOG "WARNING" "pythonw.exe not found next to python.exe. Falling back to python.exe (a console window may appear)."
    set "PYTHONW_EXE=%PYTHON_EXE%"
)
call :LOG "INFO" "Interpreter resolved: %PYTHONW_EXE%"

%PY_CMD% -m pip --version >nul 2>&1
if %errorLevel% neq 0 (
    call :LOG "WARNING" "Pip is not detected. Attempting to install it..."
    %PY_CMD% -m ensurepip --default-pip >>"%LOG_FILE%" 2>&1
    %PY_CMD% -m pip --version >nul 2>&1
    if !errorLevel! neq 0 (
        goto :PIP_MISSING
    )
    call :LOG "OK" "Pip was successfully installed."
)
call :LOG "OK" "Python environment is healthy."
echo.

:: ============================================================================
:: STEP 4: Install Required Components
:: ============================================================================
call :LOG "STEP 4/8" "Installing required components (Flask, Flask-CORS, Watchdog)..."
call :LOG "INFO" "This may take a moment. Full output is being logged."
%PY_CMD% -m pip install flask flask-cors watchdog >>"%LOG_FILE%" 2>&1
if %errorLevel% neq 0 (
    call :LOG "ERROR" "Failed to install dependencies via pip."
    call :LOG "INFO" "Please check your network connection and proxy settings."
    call :LOG "INFO" "Review the log for details: %LOG_FILE%"
    goto :INSTALL_FAIL
)
:: Probe: verify the modules actually import (catches broken/partial installs).
:: -B: never write bytecode cache (strict no-cache policy).
%PY_CMD% -B -c "import flask, flask_cors, watchdog" >>"%LOG_FILE%" 2>&1
if %errorLevel% neq 0 (
    call :LOG "ERROR" "Dependencies installed but failed to import. Environment may be corrupt."
    goto :INSTALL_FAIL
)
call :LOG "OK" "All Python components are ready and importable."
echo.

:: ============================================================================
:: STEP 5: Deploy System Logic
:: ============================================================================
call :LOG "STEP 5/8" "Verifying system logic (bridge.py)..."

if exist "%BRIDGE_PY%" (
    call :LOG "OK" "bridge.py is present."
) else (
    call :LOG "ERROR" "Failed to locate bridge.py in project directory."
    goto :INSTALL_FAIL
)
:: Probe: syntax-check the backend before we promise it will run at boot.
:: Uses ast.parse (NOT py_compile) so no __pycache__/.pyc is ever written --
:: the project has a strict no-cache policy.
%PY_CMD% -B -c "import ast; ast.parse(open(r'%BRIDGE_PY%', encoding='utf-8').read())" >>"%LOG_FILE%" 2>&1
if %errorLevel% neq 0 (
    call :LOG "ERROR" "bridge.py contains a syntax error and cannot start. Aborting."
    goto :INSTALL_FAIL
)
call :LOG "OK" "bridge.py compiles cleanly."

:: No-cache policy: remove any stray Python bytecode cache from the project
if exist "%PROJECT_DIR%\__pycache__" rmdir /s /q "%PROJECT_DIR%\__pycache__" >nul 2>&1
echo.

:: ============================================================================
:: STEP 6: Manage Background Processes (probe BEFORE killing)
:: ============================================================================
call :LOG "STEP 6/8" "Managing background processes..."
call :LOG "INFO" "Checking whether port 5589 is currently in use..."

set "PORT_BUSY=0"
for /f "tokens=5" %%a in ('netstat -aon ^| findstr /r /c:":5589 .*LISTENING"') do set "PORT_BUSY=1"

if "%PORT_BUSY%"=="1" (
    call :LOG "INFO" "Port 5589 is in use. Probing to confirm it is our bridge..."
    call :PROBE_BRIDGE
    if "!HEALTH_OK!"=="1" (
        call :LOG "INFO" "Confirmed: an existing AI Agent Bridge is running. Stopping it for upgrade..."
        for /f "tokens=5" %%a in ('netstat -aon ^| findstr /r /c:":5589 .*LISTENING"') do (
            call :LOG "INFO" "Stopping existing Bridge process [PID: %%a]..."
            taskkill /F /PID %%a >>"%LOG_FILE%" 2>&1
        )
    ) else (
        call :LOG "FATAL" "Port 5589 is occupied by an UNKNOWN application. Refusing to kill it blindly."
        echo.
        echo *****************************************************************
        echo *  PORT CONFLICT: Another application is using port 5589.       *
        echo *  It does not answer like the AI Agent Bridge, so it was NOT   *
        echo *  terminated. Please close that application and re-run this    *
        echo *  installer.                                                   *
        echo *****************************************************************
        echo.
        goto :INSTALL_FAIL
    )
)

:: Wait (max 10s) and re-probe until the port is actually free
set /a PORT_WAIT=0
:WAIT_PORT_FREE
set "PORT_BUSY=0"
for /f "tokens=5" %%a in ('netstat -aon ^| findstr /r /c:":5589 .*LISTENING"') do set "PORT_BUSY=1"
if "%PORT_BUSY%"=="1" (
    set /a PORT_WAIT+=1
    if !PORT_WAIT! geq 10 (
        call :LOG "ERROR" "Port 5589 did not become free after 10 seconds."
        goto :INSTALL_FAIL
    )
    timeout /t 1 /nobreak >nul
    goto :WAIT_PORT_FREE
)
call :LOG "OK" "Port 5589 is clear."
echo.

:: ============================================================================
:: STEP 7: Configure Persistence (verified, least-privilege)
:: ============================================================================
call :LOG "STEP 7/8" "Configuring Persistence for auto-start..."

call :LOG "INFO" "Cleaning up any old persistence tasks or shortcuts..."
schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1
schtasks /delete /tn "AIAgentBridge" /f >nul 2>&1
if exist "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\AIAgentBridge.lnk" del /f /q "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\AIAgentBridge.lnk" >nul 2>&1
if exist "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\AIAgentPromptBridge.lnk" del /f /q "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\AIAgentPromptBridge.lnk" >nul 2>&1

:: Note: full interpreter path (no PATH dependency at logon), standard user
:: privileges (the bridge only reads/writes the user's own prompt folder).
:: ATTEMPT 1 - Hardened registration via the PowerShell ScheduledTasks module.
:: Classic 'schtasks /create' defaults silently break boot persistence:
::   - the task will NOT start while the machine is on battery power (laptops)
::   - the task is force-killed after 72 hours (default ExecutionTimeLimit)
:: The settings below disable both, fire missed logon triggers when the system
:: becomes available, and auto-restart the bridge up to 3 times on crash.
call :LOG "INFO" "Creating hardened Task Scheduler job (runs at logon, standard privileges)..."
set "AIB_PYW=%PYTHONW_EXE%"
set "AIB_BRIDGE=%BRIDGE_PY%"
set "AIB_DIR=%PROJECT_DIR%"
set "AIB_TASK=%TASK_NAME%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$q=[char]34; $u=$env:USERDOMAIN+'\'+$env:USERNAME; $a=New-ScheduledTaskAction -Execute $env:AIB_PYW -Argument ('-B '+$q+$env:AIB_BRIDGE+$q) -WorkingDirectory $env:AIB_DIR; $t=New-ScheduledTaskTrigger -AtLogOn -User $u; $s=New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1); $p=New-ScheduledTaskPrincipal -UserId $u -LogonType Interactive -RunLevel Limited; Register-ScheduledTask -TaskName $env:AIB_TASK -Action $a -Trigger $t -Settings $s -Principal $p -Force | Out-Null" >>"%LOG_FILE%" 2>&1

:: Probe: confirm the task really exists before trusting it for next boot
schtasks /query /tn "%TASK_NAME%" >nul 2>&1
if %errorLevel% equ 0 (
    call :LOG "OK" "Hardened Task Scheduler job '%TASK_NAME%' created and verified."
    call :LOG "INFO" "Battery-safe, no 72h kill limit, auto-restarts on crash, standard privileges."
    set "PERSISTENCE_MODE=task"
    goto :PERSIST_DONE
)

:: ATTEMPT 2 - Classic schtasks (older systems without the ScheduledTasks module)
call :LOG "WARNING" "PowerShell task registration failed. Trying classic schtasks..."
schtasks /create /tn "%TASK_NAME%" /tr "\"%PYTHONW_EXE%\" -B \"%BRIDGE_PY%\"" /sc onlogon /f >>"%LOG_FILE%" 2>&1
schtasks /query /tn "%TASK_NAME%" >nul 2>&1
if %errorLevel% equ 0 (
    call :LOG "OK" "Task Scheduler job '%TASK_NAME%' created via classic schtasks."
    call :LOG "INFO" "Note: classic mode may pause the bridge on battery power."
    set "PERSISTENCE_MODE=task"
    goto :PERSIST_DONE
)

:: ATTEMPT 3 - VBS launcher + Startup folder shortcut (works everywhere)
call :LOG "WARNING" "Failed to create any Task Scheduler job. This can happen on some systems."
call :LOG "INFO" "Falling back to creating a shortcut in the Startup folder..."

    set "LOCAL_AUTO_START=%LOCALAPPDATA%\AIAgentPromptController"
    if not exist "!LOCAL_AUTO_START!" mkdir "!LOCAL_AUTO_START!"
    set "VBS_PATH=!LOCAL_AUTO_START!\silent_start.vbs"

    powershell -NoProfile -Command "$vbs = 'Set WinScriptHost = CreateObject(\"WScript.Shell\")' + [char]10 + 'WinScriptHost.Run Chr(34) & \"%PYTHONW_EXE%\" & Chr(34) & \" -B \" & Chr(34) & \"%BRIDGE_PY%\" & Chr(34), 0' + [char]10 + 'Set WinScriptHost = Nothing'; $vbs | Out-File -LiteralPath '!VBS_PATH!' -Encoding ascii" >>"%LOG_FILE%" 2>&1

    set "STARTUP_FOLDER=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
    set "SHORTCUT_PATH=!STARTUP_FOLDER!\AIAgentPromptBridge.lnk"
    powershell -NoProfile -Command "$WshShell = New-Object -ComObject WScript.Shell; $Shortcut = $WshShell.CreateShortcut('!SHORTCUT_PATH!'); $Shortcut.TargetPath = '!VBS_PATH!'; $Shortcut.WorkingDirectory = '%PROJECT_DIR%'; $Shortcut.Save()" >>"%LOG_FILE%" 2>&1

    :: Probe: confirm the fallback artifacts actually exist
    if exist "!SHORTCUT_PATH!" (
        if exist "!VBS_PATH!" (
            call :LOG "OK" "Startup folder shortcut created and verified."
            set "PERSISTENCE_MODE=shortcut"
        ) else (
            call :LOG "ERROR" "VBS launcher was not written. Persistence is NOT configured."
            goto :INSTALL_FAIL
        )
    ) else (
        call :LOG "ERROR" "Startup shortcut was not created. Persistence is NOT configured."
        goto :INSTALL_FAIL
    )

:PERSIST_DONE
echo.

:: ============================================================================
:: STEP 8: Launch and VERIFY the backend is alive and serving data
:: ============================================================================
call :LOG "STEP 8/8" "Starting the backend and verifying it is healthy..."

if "%PERSISTENCE_MODE%"=="task" (
    schtasks /run /tn "%TASK_NAME%" >>"%LOG_FILE%" 2>&1
    if !errorLevel! neq 0 (
        call :LOG "WARNING" "Task run command failed. Launching the bridge directly..."
        start "" "%PYTHONW_EXE%" -B "%BRIDGE_PY%"
    )
) else (
    if exist "%VBS_PATH%" (
        wscript.exe "%VBS_PATH%" >>"%LOG_FILE%" 2>&1
    ) else (
        start "" "%PYTHONW_EXE%" -B "%BRIDGE_PY%"
    )
)

call :LOG "INFO" "Probing %BRIDGE_URL%/api/ping (up to 20 seconds)..."
set /a HEALTH_RETRIES=0
:HEALTH_LOOP
call :PROBE_BRIDGE
if "%HEALTH_OK%"=="1" goto :HEALTH_DATA
set /a HEALTH_RETRIES+=1
if %HEALTH_RETRIES% geq 20 goto :BACKEND_DEAD
timeout /t 1 /nobreak >nul
goto :HEALTH_LOOP

:HEALTH_DATA
set "BRIDGE_PID="
for /f "tokens=5" %%a in ('netstat -aon ^| findstr /r /c:":5589 .*LISTENING"') do set "BRIDGE_PID=%%a"
if defined BRIDGE_PID (
    call :LOG "OK" "Backend is alive and answering on port 5589 (PID: %BRIDGE_PID%)."
    >"%LOG_DIR%\bridge.pid" echo %BRIDGE_PID%
    call :LOG "INFO" "PID recorded to: %LOG_DIR%\bridge.pid"
) else (
    call :LOG "OK" "Backend is alive and answering on port 5589."
    call :LOG "WARNING" "Listening PID could not be resolved from netstat."
)
call :LOG "INFO" "Verifying the backend serves prompt data (/api/data)..."
powershell -NoProfile -Command "try { $r = Invoke-WebRequest -Uri '%BRIDGE_URL%/api/data' -TimeoutSec 5 -UseBasicParsing; if ($r.StatusCode -eq 200) { exit 0 }; exit 1 } catch { exit 1 }" >nul 2>&1
if %errorLevel% neq 0 (
    call :LOG "WARNING" "Backend is running but /api/data did not return successfully."
    call :LOG "INFO" "Check the bridge log: %BRIDGE_LOG%"
) else (
    call :LOG "OK" "Backend verified: ping OK, data endpoint OK."
)
echo.

:: ============================================================================
:: Finalization
:: ============================================================================
call :LOG "SUCCESS" "INSTALLATION COMPLETE AND VERIFIED"
echo ============================================================
echo The AI Agent Prompt Controller is now running in the background.
echo.
echo   URL:  %BRIDGE_URL%
echo   HOME: %PROJECT_DIR%
echo   LOGS: %LOG_FILE%
echo.
timeout /t 5
exit /b 0

:: ============================================================================
:: Failure Handlers
:: ============================================================================
:NOT_ADMIN
call :LOG "FATAL" "Not running as Administrator. Installer halted."
color 4F
cls
echo.
echo.
echo        #####   #######   ####    ######
echo       #     #     #     #    #   #     #
echo       #           #    #      #  #     #
echo        #####      #    #      #  ######
echo             #     #    #      #  #
echo       #     #     #     #    #   #
echo        #####      #      ####    #
echo.
echo   =================================================================
echo.
echo        THIS WINDOW IS  * NOT *  RUNNING AS ADMINISTRATOR.
echo.
echo        THE INSTALLER CANNOT CONTINUE AND HAS STOPPED.
echo.
echo   =================================================================
echo.
echo        HOW TO FIX IT  -  2 EASY STEPS:
echo.
echo          STEP 1:  CLOSE this window.
echo.
echo          STEP 2:  RIGHT-CLICK on the file:  Install.bat
echo                   and choose:  "Run as administrator"
echo.
echo   =================================================================
echo.
echo        Press any key to close this window . . .
echo.
pause >nul
color
exit /b 1

:BACKEND_DEAD
call :LOG "FATAL" "The backend did NOT come online within 20 seconds."
echo.
echo *****************************************************************
echo *                 BACKEND FAILED TO START                       *
echo *****************************************************************
echo *                                                               *
echo *  The server was launched but never answered on port 5589.    *
echo *                                                               *
echo *  Check these logs for the reason:                             *
echo *    %BRIDGE_LOG%
echo *    %LOG_FILE%
echo *                                                               *
echo *****************************************************************
echo.
goto :INSTALL_FAIL

:PYTHON_MISSING
call :LOG "FATAL" "Python is not detected (checked 'python' and the 'py' launcher)."
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
exit /b 1
