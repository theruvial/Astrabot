@echo off
setlocal

REM --- Crash tracking ---
REM Bot output shows in this console AND is appended to
REM logs\bot.log (UTF-8, tracebacks included). Python is piped here,
REM not attached to a console, so it needs -u or print() output sits
REM in a buffer, and PYTHONIOENCODING or emoji in names raise errors.
REM One line per start/stop goes to logs\crashes.log with a
REM timestamp and the bot's exit code, so you can see at a glance
REM when it crashed and how often. After 5 crashes in a row it
REM stops restarting itself instead of looping forever.
set "LOGDIR=%~dp0logs"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set "CRASHLOG=%LOGDIR%\crashes.log"
set "BOTLOG=%LOGDIR%\bot.log"

cd /d "%~dp0"
git pull -q
git -C "G:\My Drive\codingShit\absLogin" pull -q
REM absLogin needs ABS_API_KEY set (e.g. via setx) before this runs.
REM Never put the key in this file -- it is committed to GitHub.
start "absLogin" /D "G:\My Drive\codingShit\absLogin" cmd /k py -3.14 app.py

set "PYTHONIOENCODING=utf-8"
set "CRASHES=0"
:run_bot
echo [%DATE% %TIME%] Starting bot >> "%CRASHLOG%"
powershell -NoProfile -Command "[Console]::OutputEncoding = [Text.Encoding]::UTF8; $log = [IO.StreamWriter]::new($env:BOTLOG, $true, [Text.UTF8Encoding]::new($false)); $log.AutoFlush = $true; cmd /c 'py -3.14 -u bot.py 2>&1' | ForEach-Object { $_; $log.WriteLine($_) }; $code = $LASTEXITCODE; $log.Close(); exit $code"
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
