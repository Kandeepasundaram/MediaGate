@echo off
REM Sets up the Windows notification agent to run at logon via Task Scheduler.
REM Run this once from an elevated (Administrator) command prompt.

setlocal
set SCRIPT_DIR=%~dp0
set PYTHON_EXE=%SCRIPT_DIR%..\.venv\Scripts\python.exe
set AGENT_SCRIPT=%SCRIPT_DIR%windows_toast.py
set TASK_NAME=MediaManagerNotificationAgent

if not exist "%PYTHON_EXE%" (
    echo Virtualenv not found at %PYTHON_EXE%
    echo Create it first: python -m venv .venv ^&^& .venv\Scripts\pip install winrt-Windows.UI.Notifications winrt-Windows.Data.Xml.Dom
    exit /b 1
)

schtasks /Create /TN "%TASK_NAME%" /TR "\"%PYTHON_EXE%\" \"%AGENT_SCRIPT%\"" /SC ONLOGON /RL LIMITED /F

if %ERRORLEVEL% EQU 0 (
    echo Task "%TASK_NAME%" registered. It will start the toast agent on next logon.
    echo To start it immediately: schtasks /Run /TN "%TASK_NAME%"
) else (
    echo Failed to register scheduled task. Run this script as Administrator.
)

endlocal
