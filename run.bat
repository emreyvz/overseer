@echo off
REM Overseer quick start. Launches the backend (FastAPI bridge) at http://127.0.0.1:8787.
REM Open that URL in a browser, or run the desktop shell:  cd web ^&^& npm run desktop
setlocal
cd /d "%~dp0"
echo Starting Overseer backend at http://127.0.0.1:8787 ...
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" main.py
) else (
  python main.py
)
