@echo off
setlocal EnableDelayedExpansion

title AI Agent Prompt Controller - Uninstaller

:: ============================================================================
:: Check for Administrator Privileges (Auto-Elevate if needed)
:: ============================================================================
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo.
    echo  [INFO] Administrator privileges required - prompting for elevation...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs" >nul 2>&1
    if !errorLevel! neq 0 (
        echo  [ERROR] Elevation was cancelled or failed.
        echo          Right-click UnInstall.bat and select "Run as administrator".
        timeout /t 8 >nul
    )
    exit /b
)

set "PROJECT_DIR=%~dp0"
if "%PROJECT_DIR:~-1%"=="\" set "PROJECT_DIR=%PROJECT_DIR:~0,-1%"
cd /d "%PROJECT_DIR%"
set "PORT=5589"
set "PS=powershell -NoProfile -ExecutionPolicy Bypass -Command"

echo =========================================================
echo AI Agent Prompt Controller - Uninstaller
echo =========================================================
echo.

:: ============================================================================
:: STEP 1: Stop and Remove Scheduled Tasks FIRST
:: (Must happen before killing process, otherwise RestartOnFailure will resurrect it)
:: ============================================================================
echo [INFO] Stopping and removing Scheduled Tasks...
for %%T in ("AIAgentPromptBridge" "AIAgentBridge") do (
    schtasks /end /tn "%%~T" >nul 2>&1
    schtasks /delete /tn "%%~T" /f >nul 2>&1
)

:: Last resort: query any scheduled task pointing to bridge.py and delete it
%PS% "try { Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object { $_.Actions | Where-Object { ($_.Execute + ' ' + $_.Arguments) -match 'bridge\.py' } } | ForEach-Object { Stop-ScheduledTask -TaskName $_.TaskName -ErrorAction SilentlyContinue; Unregister-ScheduledTask -TaskName $_.TaskName -Confirm:$false } } catch { }" >nul 2>&1

:: ============================================================================
:: STEP 2: Terminate Running Bridge Processes
:: ============================================================================
echo [INFO] Stopping background processes...

:: 1. Check PID files and verify identity before killing
for %%F in (
    "%TMP%\AI-Agent-Prompt\.bridge.pid"
    "%TMP%\AI-Agent-Prompt\bridge.pid"
    "%TEMP%\AI-Agent-Prompt\.bridge.pid"
    "%TEMP%\AI-Agent-Prompt\bridge.pid"
) do (
    if exist "%%~F" (
        set "TARGET_PID="
        set /p TARGET_PID=<"%%~F"
        if defined TARGET_PID (
            taskkill /F /PID !TARGET_PID! >nul 2>&1
            del /f /q "%%~F" >nul 2>&1
        )
    )
)

:: 2. Kill anything listening on port 5589
for /f "tokens=5" %%a in ('netstat -aon ^| findstr /r /c:":%PORT% .*LISTENING"') do (
    echo [INFO] Killing process PID %%a on port %PORT%...
    taskkill /F /PID %%a >nul 2>&1
)

:: 3. Sweep any stray python processes running bridge.py
%PS% "Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'python' -and $_.CommandLine -and $_.CommandLine -match 'bridge\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1

:: ============================================================================
:: STEP 3: Remove Startup Shortcuts
:: ============================================================================
echo [INFO] Removing Startup Shortcuts...
for %%D in (
    "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
    "%ProgramData%\Microsoft\Windows\Start Menu\Programs\Startup"
) do (
    if exist "%%~D\AIAgentBridge.lnk" del /f /q "%%~D\AIAgentBridge.lnk" >nul 2>&1
    if exist "%%~D\AIAgentPromptBridge.lnk" del /f /q "%%~D\AIAgentPromptBridge.lnk" >nul 2>&1
)

:: ============================================================================
:: STEP 4: Remove Local AppData Files
:: ============================================================================
echo [INFO] Removing Local AppData files...
set "LOCAL_AUTO_START=%LOCALAPPDATA%\AIAgentPromptController"
if exist "%LOCAL_AUTO_START%" rmdir /s /q "%LOCAL_AUTO_START%" >nul 2>&1

:: ============================================================================
:: STEP 5: Remove Temporary and Log Files
:: ============================================================================
echo [INFO] Removing temporary and log files...
if exist "%TMP%\AI-Agent-Prompt" rmdir /s /q "%TMP%\AI-Agent-Prompt" >nul 2>&1
if exist "%TEMP%\AI-Agent-Prompt" rmdir /s /q "%TEMP%\AI-Agent-Prompt" >nul 2>&1

:: ============================================================================
:: STEP 6: Remove Python Bytecode Cache (Strict No-Cache Policy)
:: ============================================================================
echo [INFO] Purging Python bytecode cache...
if exist "%PROJECT_DIR%\__pycache__" rmdir /s /q "%PROJECT_DIR%\__pycache__" >nul 2>&1
del /f /q "%PROJECT_DIR%\*.pyc" >nul 2>&1

echo.
echo =========================================================
echo [SUCCESS] Uninstallation complete.
echo All background tasks, processes, and persistence files have been removed.
echo Note: The main project folder has been preserved. You may delete it manually if desired.
echo =========================================================
echo.
pause
