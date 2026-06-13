@echo off
setlocal

set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

:startbot
"%PYTHON_EXE%" main.py
if errorlevel 1 goto startbot
