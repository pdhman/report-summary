@echo off
setlocal
rem 프로젝트 표준 Python(3.11) 사용. 필요한 패키지(pandas/lxml/bs4/
rem html5lib/playwright)는 이 Python 에 설치되어 있다.
rem Codex 번들 런타임(.cache)과 .deps 의존을 제거했다.
set "PYTHON_EXE=C:\Users\SAMSUNG\AppData\Local\Programs\Python\Python311\python.exe"

if not exist "%PYTHON_EXE%" (
  echo Python executable not found: %PYTHON_EXE%
  exit /b 1
)

"%PYTHON_EXE%" "%~dp0collector.py"
