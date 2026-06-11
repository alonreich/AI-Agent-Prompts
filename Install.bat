@echo off
setlocal DisableDelayedExpansion

set "PYTHONDONTWRITEBYTECODE=1"

title AI Agent Prompt Controller - Professional Setup


set "MIN_PYTHON_VERSION=3.7"
set "PROJECT_DIR=%~dp0"
set "PROJECT_DIR=%PROJECT_DIR:~0,-1%"
set "BRIDGE_PY=%PROJECT_DIR%\bridge.py"


echo [STEP 1/6] Checking for Administrative Privileges...
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [INFO] Administrator rights are required.
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)
echo [OK] Administrative access confirmed.
echo.


echo [STEP 2/6] Validating Python Environment...

python --version >nul 2>&1
if %errorLevel% neq 0 (
    goto :PYTHON_MISSING
)

for /f "tokens=2" %%v in ('python --version 2^>^&1') do set "DETECTED_PY_VER=%%v"
echo [INFO] Found Python version: %DETECTED_PY_VER%

powershell -NoProfile -Command "if ([version]$env:DETECTED_PY_VER -lt [version]$env:MIN_PYTHON_VERSION) { exit 1 } else { exit 0 }"
if %errorLevel% neq 0 (
    goto :PYTHON_OLD
)

python -m pip --version >nul 2>&1
if %errorLevel% neq 0 (
    goto :PIP_MISSING
)
echo [OK] Python environment is healthy.
echo.


echo [STEP 3/6] Installing Required Components [Flask, Watchdog]...
python -m pip install flask flask-cors watchdog >nul 2>&1
if %errorLevel% neq 0 (
    echo [ERROR] Failed to install dependencies via pip.
    timeout /t 10
    exit /b
)
echo [OK] All components are ready.
echo.



echo [STEP 4/6] Deploying System Logic [bridge.py]...

set "MASTER_PY=%PROJECT_DIR%\bridge_master.py"
if not exist "%MASTER_PY%" (
    echo [ERROR] bridge_master.py not found in project directory.
    timeout /t 10
    exit /b
)
copy /Y "%MASTER_PY%" "%BRIDGE_PY%" >nul 2>&1
if exist "%BRIDGE_PY%" (
    echo [OK] bridge.py deployed from bridge_master.py.
) else (
    echo [ERROR] Failed to deploy bridge.py.
    timeout /t 10
    exit /b
)
echo.


echo [STEP 5/6] Managing Background Processes...

for /f "tokens=5" %%a in ('netstat -aon ^| findstr :5589 ^| findstr LISTENING') do (
    echo [INFO] Stopping existing Bridge [PID: %%a]...
    taskkill /F /PID %%a >nul 2>&1
)

echo [INFO] Waiting for port release...
timeout /t 2 /nobreak >nul
echo [OK] Port 5589 is clear.
echo.


echo [STEP 6/6] Configuring Persistence [Task Scheduler]...

set "TASK_NAME=AIAgentPromptBridge"
set "LOG_DIR=%PROJECT_DIR%\logs"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"


schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1





echo [INFO] Creating Task Scheduler task...
schtasks /create /tn "%TASK_NAME%" /tr "cmd /c set PYTHONDONTWRITEBYTECODE=1 ^& pythonw.exe \"%BRIDGE_PY%\"" /sc onlogon /rl highest /f >nul 2>&1

if %errorLevel% equ 0 (
    echo [OK] Task Scheduler task "%TASK_NAME%" created.
    echo [INFO] The bridge will now auto-start on Windows login and run in the background.
) else (
    echo [WARNING] Failed to create Task Scheduler task.
    echo [INFO] Falling back to Startup folder shortcut...
    
    set "LOCAL_AUTO_START=%LOCALAPPDATA%\AIAgentPromptController"
    if not exist "%LOCAL_AUTO_START%" mkdir "%LOCAL_AUTO_START%"
    set "VBS_PATH=%LOCAL_AUTO_START%\silent_start.vbs"

    powershell -NoProfile -Command "$vbs = 'Set WinScriptHost = CreateObject(\"WScript.Shell\")' + [char]10 + 'WinScriptHost.Run \"pythonw.exe \" & Chr(34) & \"%BRIDGE_PY%\" & Chr(34), 0' + [char]10 + 'Set WinScriptHost = Nothing'; $vbs | Out-File -LiteralPath '%VBS_PATH%' -Encoding ascii"

    set "STARTUP_FOLDER=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
    set "SHORTCUT_PATH=%STARTUP_FOLDER%\AIAgentPromptBridge.lnk"
    powershell -NoProfile -Command "$WshShell = New-Object -ComObject WScript.Shell; $Shortcut = $WshShell.CreateShortcut('%SHORTCUT_PATH%'); $Shortcut.TargetPath = '%VBS_PATH%'; $Shortcut.WorkingDirectory = '%PROJECT_DIR%'; $Shortcut.Save()"
    echo [OK] Startup folder shortcut created.
)
echo.

echo ============================================================
echo [SUCCESS] INSTALLATION COMPLETE
echo ============================================================
echo The AI Agent Prompt Controller is now running in the background.
echo.
echo   URL:  http://127.0.0.1:5589
echo   LOGS: %LOG_DIR%\bridge.log
echo.
echo Starting the bridge now...
schtasks /run /tn "%TASK_NAME%" >nul 2>&1
if %errorLevel% neq 0 (
    if exist "%VBS_PATH%" wscript.exe "%VBS_PATH%"
)
timeout /t 5
exit /b


:PYTHON_MISSING
echo [CRITICAL ERROR] PYTHON NOT DETECTED
echo Step 1: Download Python from https://www.python.org/
echo Step 2: **CRITICAL** Check \"Add Python to PATH\" during install.
timeout /t 30
exit /b

:PYTHON_OLD
echo [CRITICAL ERROR] PYTHON VERSION OUTDATED (Min: %MIN_PYTHON_VERSION%)
timeout /t 30
exit /b

:PIP_MISSING
echo [CRITICAL ERROR] PIP NOT DETECTED
echo Run: python -m ensurepip --default-pip
timeout /t 30
exit /b




