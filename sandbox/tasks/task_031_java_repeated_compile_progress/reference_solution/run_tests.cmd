@echo off
setlocal
if exist out rmdir /s /q out
mkdir out
javac -d out src\main\java\com\example\Invoice.java src\test\java\com\example\InvoiceTest.java
if errorlevel 1 exit /b 1
java -cp out com.example.InvoiceTest
