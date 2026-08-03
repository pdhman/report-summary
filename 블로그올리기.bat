@echo off
chcp 65001 >nul
echo 블로그(주도섹터 리포트) 수집을 시작합니다...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "C:\Users\SAMSUNG\Desktop\클로드코드\run_blog.ps1"
echo.
echo 완료되었습니다. 새 글이 없으면 아무것도 올라가지 않습니다.
echo 자세한 기록: logs 폴더의 blog_*.log
pause
