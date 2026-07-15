@echo off
setlocal EnableDelayedExpansion

title AI Agent Prompt Controller - Uninstaller

echo =========================================================
echo AI Agent Prompt Controller - Uninstaller
echo =========================================================
echo.

:: Check for Administrator privileges
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [ERROR] Administrator privileges are required to uninstall.
    echo Please right-click UnInstall.bat and select "Run as administrator".
    timeout /t 10
    exit /b
)

echo [INFO] Stopping background processes...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :5589 ^| findstr LISTENING') do (
    echo [INFO] Killing process PID %%a...
    taskkill /F /PID %%a >nul 2>&1
)

echo [INFO] Removing Scheduled Tasks...
schtasks /delete /tn "AIAgentPromptBridge" /f >nul 2>&1
schtasks /delete /tn "AIAgentBridge" /f >nul 2>&1

echo [INFO] Removing Startup Shortcuts...
if exist "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\AIAgentBridge.lnk" del /f /q "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\AIAgentBridge.lnk" >nul 2>&1
if exist "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\AIAgentPromptBridge.lnk" del /f /q "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\AIAgentPromptBridge.lnk" >nul 2>&1

echo [INFO] Removing Local AppData files...
set "LOCAL_AUTO_START=%LOCALAPPDATA%\AIAgentPromptController"
if exist "%LOCAL_AUTO_START%" rmdir /s /q "%LOCAL_AUTO_START%" >nul 2>&1

echo [INFO] Removing temporary and log files...
set "LOG_DIR=%TMP%\AI-Agent-Prompt"
if exist "%LOG_DIR%" rmdir /s /q "%LOG_DIR%" >nul 2>&1

echo.
echo [SUCCESS] Uninstallation complete. All background tasks and persistence files have been removed.
echo Note: The main project folder has been preserved. You may delete it manually if desired.
echo.
pause
