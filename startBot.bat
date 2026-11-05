FOR /F "tokens=2 delims= " %%P IN ('tasklist /FO Table /M /NH ^| Find /i "Py"') DO (TASKKILL /PID %%P /F)
git -C "G:\My Drive\codingShit\Astrabot" pull
git -C "G:\My Drive\codingShit\absLogin" pull
@REM  REM absLogin needs ABS_API_KEY set (e.g. via setx) before this runs.
@REM  REM Never put the key in this file -- it is committed to GitHub.
start "absLogin" /D "G:\My Drive\codingShit\absLogin" cmd /k py -3.14 app.py
py -3.14 bot.py
