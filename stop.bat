@echo off
echo Stopping Sims Engine processes...
taskkill /F /IM python.exe /FI "WINDOWTITLE eq __main__*" 2>nul
taskkill /F /IM python.exe 2>nul
echo Done.
