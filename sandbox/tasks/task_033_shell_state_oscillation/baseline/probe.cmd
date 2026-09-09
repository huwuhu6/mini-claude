@echo off
if not exist business_state.txt echo A>business_state.txt
set /p state=<business_state.txt
if "%state%"=="A" (echo B>business_state.txt) else (echo A>business_state.txt)
echo OBSERVED:%state%
