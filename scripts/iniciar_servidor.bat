@echo off
REM Inicia o watchdog do servidor "Teste de Frete" em segundo plano.
REM O watchdog mantem o servidor rodando e o reinicia sozinho se ele cair.

powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0watchdog_servidor.ps1"
