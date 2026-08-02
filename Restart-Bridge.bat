@echo off
setlocal EnableDelayedExpansion

title AI Agent Prompt Controller - Restart Bridge

:: ==========================================================================
::  Restart-Bridge.bat
::
::  Restarts the bridge PROCESS without touching Task Scheduler persistence.
::  Stops the running instance, waits for port 5589 to clear, then asks Task
::  Scheduler to start it again - so the task keeps owning the process and
::  still fires at logon exactly as before.
::
::  Use this after editing bridge.py.
::  Install.bat is only needed for a full (re)install.
::
::  Note: the bridge itself keeps running NON-elevated. This script elevates
::  only so it can end and start the task and kill a stuck process; the task
::  principal is unchanged, so the bridge still runs as the logged-on user
::  with limited rights.
:: ==========================================================================

set "PROJECT_DIR=%~dp0"
if "%PROJECT_DIR:~-1%"=="\" set "PROJECT_DIR=%PROJECT_DIR:~0,-1%"
set "PORT=5589"
set "PID_FILE=%TMP%\AI-Agent-Prompt\.bridge.pid"
set "VBS_PATH=%LOCALAPPDATA%\AIAgentPromptController\silent_start.vbs"
set "TASK_TMP=%TEMP%\aib_restart_task.txt"
set "PS=powershell -NoProfile -ExecutionPolicy Bypass -Command"

:: ------------------------------------------------------------------ elevate
net session >nul 2>&1
if not "%errorLevel%"=="0" (
    echo.
    echo  [INFO] Administrator privileges required - prompting for elevation...
    %PS% "Start-Process -FilePath '%~f0' -Verb RunAs" >nul 2>&1
    if errorlevel 1 (
        echo  [ERROR] Elevation was cancelled or failed.
        echo          Right-click Restart-Bridge.bat and choose "Run as administrator".
        timeout /t 8 >nul
    )
    exit /b
)

echo =========================================================
echo   AI Agent Prompt Controller - Restart Bridge
echo =========================================================
echo.
echo   Project : %PROJECT_DIR%
echo   Port    : %PORT%
echo.

:: ------------------------------------------------------- 1. locate the task
echo [1/6] Locating the scheduled task...
set "TASK_NAME="
for %%T in ("AIAgentPromptBridge" "AIAgentBridge") do (
    if not defined TASK_NAME (
        schtasks /query /tn "%%~T" >nul 2>&1
        if not errorlevel 1 set "TASK_NAME=%%~T"
    )
)

if not defined TASK_NAME call :DiscoverTask

if defined TASK_NAME (
    echo       Found task: "!TASK_NAME!"
) else (
    echo       [WARN] No scheduled task found for this project.
    echo              Will fall back to the Startup-folder VBS launcher.
)
echo.

:: ---------------------------------------------------------- 2. stop the task
echo [2/6] Stopping the running bridge...
if defined TASK_NAME (
    schtasks /end /tn "!TASK_NAME!" >nul 2>&1
    if not errorlevel 1 (
        echo       Task instance ended.
    ) else (
        echo       Task was not running.
    )
)

:: PID file written by bridge.py on startup
if exist "%PID_FILE%" (
    set "OLD_PID="
    set /p OLD_PID=<"%PID_FILE%"
    if defined OLD_PID (
        taskkill /F /PID !OLD_PID! >nul 2>&1
        if not errorlevel 1 echo       Killed PID !OLD_PID! from the PID file.
    )
)

:: Anything still holding port 5589, however it was launched
for /f "tokens=5" %%a in ('netstat -aon ^| findstr /r /c:":%PORT% .*LISTENING"') do (
    taskkill /F /PID %%a >nul 2>&1
    echo       Killed PID %%a listening on port %PORT%.
)

:: Any stray python holding bridge.py (VBS fallback, manual launch, etc.)
%PS% "Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'python' -and $_.CommandLine -and $_.CommandLine -match 'bridge\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1
echo.

:: ------------------------------------------------- 3. wait for the port to free
echo [3/6] Waiting for port %PORT% to clear...
set "FREE=0"
for /l %%i in (1,1,15) do (
    if "!FREE!"=="0" (
        netstat -aon | findstr /r /c:":%PORT% .*LISTENING" >nul 2>&1
        if errorlevel 1 (
            set "FREE=1"
        ) else (
            timeout /t 1 /nobreak >nul
        )
    )
)
if "!FREE!"=="1" (
    echo       Port %PORT% is free.
) else (
    echo       [ERROR] Port %PORT% is still in use after 15 seconds.
    echo               Something is holding it that could not be stopped.
    echo               Run:  netstat -aon ^| findstr :%PORT%
    goto :Finish
)
echo.

:: ------------------------------------------------------ 4. enforce no-cache
echo [4/6] Enforcing the no-cache policy...
if exist "%PROJECT_DIR%\__pycache__" (
    rmdir /s /q "%PROJECT_DIR%\__pycache__" >nul 2>&1
    echo       Removed stray __pycache__.
) else (
    echo       Clean - no bytecode cache present.
)
echo.

:: ------------------------------------------------------------ 5. start again
echo [5/6] Starting the bridge...
set "STARTED=0"
if defined TASK_NAME (
    schtasks /run /tn "!TASK_NAME!" >nul 2>&1
    if not errorlevel 1 (
        set "STARTED=1"
        echo       Task "!TASK_NAME!" triggered - Task Scheduler owns the process.
    ) else (
        echo       [WARN] schtasks /run failed.
    )
)

if "!STARTED!"=="0" (
    if exist "%VBS_PATH%" (
        wscript.exe "%VBS_PATH%" >nul 2>&1
        set "STARTED=1"
        echo       Started via the Startup-folder VBS launcher.
    )
)

if "!STARTED!"=="0" (
    echo       [ERROR] Could not start the bridge - no task and no VBS launcher.
    echo               Run Install.bat once to (re)create persistence.
    goto :Finish
)
echo.

:: ----------------------------------------------------------- 6. verify health
echo [6/6] Verifying the bridge answers...
%PS% "$ok=$false; for($i=0;$i -lt 20;$i++){ try { $r=Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:%PORT%/api/ping' -TimeoutSec 2; if($r.StatusCode -eq 200){$ok=$true;break} } catch { }; Start-Sleep -Seconds 1 }; if($ok){ exit 0 } else { exit 1 }"
if errorlevel 1 (
    echo       [ERROR] The bridge did not answer within 20 seconds.
    echo               Log: %TMP%\AI-Agent-Prompt\bridge.log
    goto :Finish
)

set "NEW_PID="
for /f "tokens=5" %%a in ('netstat -aon ^| findstr /r /c:":%PORT% .*LISTENING"') do set "NEW_PID=%%a"

set "BRIDGE_VER="
%PS% "try { ((Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:%PORT%/api/version' -TimeoutSec 3).Content | ConvertFrom-Json).version } catch { }" > "%TASK_TMP%" 2>nul
if exist "%TASK_TMP%" (
    set /p BRIDGE_VER=<"%TASK_TMP%"
    del /f /q "%TASK_TMP%" >nul 2>&1
)

echo.
echo =========================================================
echo   [SUCCESS] Bridge restarted.
if defined NEW_PID echo   New PID     : !NEW_PID!
if defined TASK_NAME echo   Task        : !TASK_NAME!  (still runs at logon)
if defined BRIDGE_VER echo   Version     : !BRIDGE_VER!
echo   URL         : http://127.0.0.1:%PORT%
echo =========================================================
echo.
echo   Refresh the browser tab to pick up the new backend.
echo.

:Finish
echo.
pause
endlocal
exit /b

:: --------------------------------------------------------------------------
:DiscoverTask
:: Last resort: ask Task Scheduler for any task whose action mentions bridge.py
:: (covers a task that was renamed by hand).
%PS% "try { $t = Get-ScheduledTask -ErrorAction Stop | Where-Object { $_.Actions | Where-Object { ($_.Execute + ' ' + $_.Arguments) -match 'bridge\.py' } } | Select-Object -First 1; if ($t) { ($t.TaskPath + $t.TaskName) } } catch { }" > "%TASK_TMP%" 2>nul
if exist "%TASK_TMP%" (
    set /p TASK_NAME=<"%TASK_TMP%"
    del /f /q "%TASK_TMP%" >nul 2>&1
)
if defined TASK_NAME (
    for /f "tokens=* delims= " %%X in ("!TASK_NAME!") do set "TASK_NAME=%%X"
)
exit /b
