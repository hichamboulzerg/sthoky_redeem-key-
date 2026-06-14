@echo off
cd /d "C:\Users\Administrator.WIN-4Q9CTNFH5R7\stocky-redeem-agent"
:loop
echo [%date% %time%] starting agent >> agent.log
"C:\Program Files\Python312\python.exe" agent.py >> agent.log 2>&1
echo [%date% %time%] agent exited, restarting in 5s >> agent.log
timeout /t 5 /nobreak >nul
goto loop
