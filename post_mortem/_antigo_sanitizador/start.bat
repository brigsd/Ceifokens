@echo off
cd /d "%~dp0"
:: Inicia o python em segundo plano de forma oculta
powershell -WindowStyle Hidden -Command "Start-Process python -ArgumentList 'app.py' -WindowStyle Hidden"
:: Aguarda 1 segundo para garantir que o servidor subiu
timeout /t 1 /nobreak >nul
:: Abre a interface no navegador padrão
start http://127.0.0.1:8000/
exit
