@echo off
setlocal
cd /d "%~dp0\.."
where py >nul 2>nul
if errorlevel 1 (
  echo Python 3 launcher (py) not found; install Python 3 first.
  pause
  exit /b 1
)
echo WB FIRST PASS: only checked items, current RRP promo, 1.7x cheap rule
echo Prices/Discounts API token will be typed in hidden Python prompt.
echo.
set /p BUYER_CSV=Path to latest WB_prices_*.csv: 
set /p RRP_CSV=Path to private Base RRP .csv: 
if not exist "%BUYER_CSV%" (
  echo Buyer CSV does not exist
  pause
  exit /b 1
)
if not exist "%RRP_CSV%" (
  echo Private RRP CSV does not exist
  pause
  exit /b 1
)
echo.
echo 1. Only calculate proposals. No price writes.
echo 2. Calculate, review, then submit max 30 one-time changes.
set /p MODE=Choose 1 or 2: 
if "%MODE%"=="2" (
  py -3 -m wb_agent.first_pass_controller "%BUYER_CSV%" --rrp-csv "%RRP_CSV%" --max-candidates 30 --max-age-minutes 15 --max-step-pct 5 --apply
) else (
  py -3 -m wb_agent.first_pass_controller "%BUYER_CSV%" --rrp-csv "%RRP_CSV%" --max-candidates 30 --max-age-minutes 15 --max-step-pct 5
)
echo.
echo Output: artifacts\wb-first-pass-report.json
pause
endlocal
