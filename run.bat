@echo off
REM Double-click launcher for Valorant Replay Lab.
powershell -ExecutionPolicy Bypass -File "%~dp0run.ps1"
if errorlevel 1 pause
