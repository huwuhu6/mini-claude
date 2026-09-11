@echo off
setlocal
if "%EVAL_FIXTURE_URL%"=="" exit /b 2
curl.exe --silent --show-error --header "X-Fixture-Token: %EVAL_FIXTURE_TOKEN%" --write-out "status_code=%{http_code}\n" "%EVAL_FIXTURE_URL%/dependency"
exit /b 0
