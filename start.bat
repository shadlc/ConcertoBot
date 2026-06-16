@echo off
setlocal

where uv >nul 2>nul
if errorlevel 1 (
    echo uv is required. Install it first: https://docs.astral.sh/uv/getting-started/installation/
    exit /b 1
)

call uv sync
if errorlevel 1 exit /b %errorlevel%

:startbot
call uv run python main.py
if errorlevel 1 goto startbot
