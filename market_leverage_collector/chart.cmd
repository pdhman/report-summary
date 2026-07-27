@echo off
setlocal
rem 수집된 데이터로 차트 페이지(leverage.html)를 만들고 브라우저로 연다.
set "PYTHON_EXE=C:\Users\SAMSUNG\AppData\Local\Programs\Python\Python311\python.exe"

if not exist "%PYTHON_EXE%" (
  echo Python executable not found: %PYTHON_EXE%
  exit /b 1
)

"%PYTHON_EXE%" "%~dp0make_chart.py"
if errorlevel 1 exit /b 1
start "" "%~dp0leverage.html"
