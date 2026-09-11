@echo off
setlocal
if "%EVAL_FIXTURE_URL%"=="" exit /b 2
curl.exe --silent --show-error --header "X-Fixture-Token: %EVAL_FIXTURE_TOKEN%" "%EVAL_FIXTURE_URL%/state"
exit /b 0
