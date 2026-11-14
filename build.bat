@echo off
rem Сборка русификатора: ROM + патч BPS (и IPS) в папку build
python tools\insert.py "Terranigma (Europe).sfc" script\rus build\Terranigma_RUS.sfc --bps build\Terranigma_RUS.bps --ips build\Terranigma_RUS.ips
if errorlevel 1 pause & exit /b 1
python tools\verify.py "Terranigma (Europe).sfc" build\Terranigma_RUS.sfc script > build\verify.txt
pause
