@echo off
cd /d "%~dp0"
echo Starting Sims Engine (Pygame)...
python pygame_app/main.py %*
pause
