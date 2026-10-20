@echo off
setlocal
cd /d "%~dp0\.."
where py >nul 2>nul
if errorlevel 1 (
  echo Python 3 launcher not found. Install Python 3 from python.org.
  pause
  exit /b 1
)
echo Installing free local browser driver...
py -3 -m pip install "playwright>=1.50,<2"
if errorlevel 1 (
  echo ERROR: playwright installation failed.
  pause
  exit /b 1
)
py -3 -m playwright install chromium
if errorlevel 1 (
  echo ERROR: Chromium installation failed.
  pause
  exit /b 1
)
echo Checking WB item 1308438233 for Moscow, no changes to seller prices...
py -3 -m wb_agent.browser_price_probe 1308438233 --dest -1257786 --wallet-pct 3
echo.
echo Results saved in artifacts\wb-browser-price-probe.json
pause
endlocal
