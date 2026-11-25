@echo off
REM Runs MediaGrab from source (needs Python 3.9+ from python.org)
cd /d "%~dp0"
pythonw mediagrab.py || python mediagrab.py
