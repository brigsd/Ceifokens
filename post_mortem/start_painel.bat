@echo off
title Painel Ceifokens Sanitizador
echo ==========================================================
echo        PAINEL CEIFOKENS SANITIZADOR — ANTIGRAVITY
echo ==========================================================
echo.
echo Iniciando o Servidor Web Flask...
echo.
python app_web.py
if %ERRORLEVEL% neq 0 (
    echo.
    echo Erro ao iniciar o servidor. Verifique se o Python esta no PATH
    echo ou se as dependencias estao instaladas.
    echo.
)
pause
