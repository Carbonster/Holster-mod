@echo off
cd /d "%~dp0"
pymhf run holster_live.py
if not "%errorlevel%"=="0" pause
