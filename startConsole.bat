@echo off
setlocal

REM Opens the Golurk Console window, which runs the bot, wiki and absLogin
REM and shows their output. Use this OR startBot.bat, never both.
REM absLogin needs ABS_API_KEY set (e.g. via setx). Never put it in this file.
cd /d "%~dp0"
git pull -q
git -C "G:\My Drive\codingShit\absLogin" pull -q
start "" pyw -3.14 astra_console.py
endlocal
