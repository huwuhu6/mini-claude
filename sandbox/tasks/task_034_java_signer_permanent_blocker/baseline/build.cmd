@echo off
if exist out rmdir /s /q out
mkdir out
javac -d out src\main\java\SignRequest.java
if errorlevel 1 exit /b 1
java -cp out SignRequest
