@echo off
setlocal

REM --- Crash tracking ---
REM Bot output goes to logs\bot.log (appended, includes tracebacks).
REM One line per start/stop goes to logs\crashes.log with a
REM timestamp and the bot's exit code, so you can see at a glance
REM when it crashed and how often. After 5 crashes in a row it
REM stops restarting itself instead of looping forever.
set "LOGDIR=%~dp0logs"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set "CRASHLOG=%LOGDIR%\crashes.log"
set "BOTLOG=%LOGDIR%\bot.log"

FOR /F "tokens=2 delims= " %%P IN ('tasklist /FO Table /M /NH ^| Find /i "Py"') DO (TASKKILL /PID %%P /F)
cd C:\Coding\Astrabot
git pull
git -C "G:\My Drive\codingShit\absLogin" pull
REM absLogin needs ABS_API_KEY set (e.g. via setx) before this runs.
REM Never put the key in this file -- it is committed to GitHub.
start "absLogin" /D "G:\My Drive\codingShit\absLogin" cmd /k py -3.14 app.py

set "CRASHES=0"
:run_bot
echo [%DATE% %TIME%] Starting bot >> "%CRASHLOG%"
py -3.14 bot.py >> "%BOTLOG%" 2>&1
set "EXITCODE=%ERRORLEVEL%"
echo [%DATE% %TIME%] Bot stopped, exit code %EXITCODE% >> "%CRASHLOG%"
if %EXITCODE%==0 goto :done
set /a CRASHES+=1
if %CRASHES% GEQ 5 (
    echo [%DATE% %TIME%] Bot crashed %CRASHES% times in a row - not restarting. See %BOTLOG% for the traceback. >> "%CRASHLOG%"
    goto :done
)
timeout /t 10 /nobreak >nul
goto :run_bot

:done
echo [%DATE% %TIME%] startBot finished. >> "%CRASHLOG%"
endlocal
