@echo off
rem يفتح كروميوم بتاع Playwright على البروفايل الثابت profile\ جوه فولدر المشروع
rem مع منفذ CDP 127.0.0.1:9333
setlocal
set "ROOT=%~dp0"
set "PY=%ROOT%.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo البيئة الافتراضية مش موجودة: %PY%
  echo شغّل:  python -m venv .venv ^&^& .venv\Scripts\python.exe -m pip install -r requirements.txt
  exit /b 1
)
"%PY%" "%ROOT%launch_browser.py" %*
endlocal
