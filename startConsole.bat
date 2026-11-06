@echo off
setlocal

REM Opens the Astrabot Console window, which runs the bot and shows its
REM output. Use this OR startBot.bat, never both: each starts its own bot.
cd /d "%~dp0"
git pull -q
git -C "G:\My Drive\codingShit\absLogin" pull -q
REM absLogin needs ABS_API_KEY set (e.g. via setx) before this runs.
REM Never put the key in this file -- it is committed to GitHub.
start "absLogin" /D "G:\My Drive\codingShit\absLogin" cmd /k py -3.14 app.py
start "" pyw -3.14 astra_console.py
endlocal
