@echo off
REM Passagem única do agendador de ingestão (Sprint 21).
REM Registrado no Agendador de Tarefas do Windows como "PlataformaFiscal-Ingestao".
REM Equivalente cron (Linux):  0 6 * * *  python -u -m scripts.scheduler --once
cd /d "%~dp0.."
if not exist var mkdir var
set PYTHONUTF8=1
".venv\Scripts\python.exe" -u -m scripts.scheduler --once >> "var\scheduler.log" 2>&1
