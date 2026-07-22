@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
title Overseer
cd /d "%~dp0"

echo.
echo   ================================================
echo      OVERSEER   -   setup and launch
echo   ================================================
echo.

REM ---- 1. make sure uv (the Python toolchain) is installed ----
where uv >nul 2>nul
if errorlevel 1 (
  echo   [setup] Installing uv ^(one time^)...
  powershell -NoProfile -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"
  set "PATH=%USERPROFILE%\.local\bin;%PATH%"
)
where uv >nul 2>nul
if errorlevel 1 set "PATH=%USERPROFILE%\.local\bin;%PATH%"
where uv >nul 2>nul
if errorlevel 1 (
  echo   ERROR: could not install uv automatically.
  echo   Install it from https://github.com/astral-sh/uv and run this again.
  pause & exit /b 1
)

echo   [1/3] Installing Python dependencies ^(first run downloads a lot, please wait^)...
call uv sync
if errorlevel 1 ( echo   ERROR: dependency install failed. & pause & exit /b 1 )

REM ---- 2. Node present -> full desktop app; otherwise -> browser ----
where npm >nul 2>nul
if not errorlevel 1 (
  echo   [2/3] Building the interface...
  pushd web
  if not exist node_modules ( call npm install )
  call npm run build
  echo   [3/3] Launching the Overseer desktop app...
  call npm run electron
  popd
  exit /b 0
)

echo   Node.js was not found, starting Overseer in your web browser instead.
echo   ^(Install Node.js from https://nodejs.org if you want the desktop window.^)
echo   [2/3] Opening the browser shortly...
start "" /b powershell -NoProfile -c "Start-Sleep 5; Start-Process 'http://127.0.0.1:8787'"
echo   [3/3] Starting Overseer. Close this window to stop it.
call uv run python -m server
