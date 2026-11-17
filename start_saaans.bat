@echo off
rem Double-click after a restart to start the SAAANS server in the background.
rem Does nothing if the server is already running.
powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0start_saaans.ps1"
