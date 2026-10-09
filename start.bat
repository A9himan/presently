@echo off
title Presently: Attendance That Takes Itself (Flask Hub Server)
echo ===================================================================
echo   PRESENTLY : ATTENDANCE THAT TAKES ITSELF
echo   Python Flask + SQLAlchemy + ReportLab + LMS Webhooks
echo ===================================================================

where python >nul 2>nul
if %ERRORLEVEL% equ 0 (
    set PYTHON_EXEC=python
) else (
    set PYTHON_EXEC=py
)

echo.
echo Launching Central Hub (Admin / Faculty Dashboard)...
start "" http://localhost:3000

echo Launching Camera Edge Node Web Interface...
start "" http://localhost:3000/node

echo.
echo Central Hub running on: http://localhost:3000
echo Camera Node running on: http://localhost:3000/node
echo Admin Login: username: admin123 | password: admin123
echo Faculty Login: username: 123 | password: 123 (Er. Gagandeep Kaur, DS)
echo ===================================================================

%PYTHON_EXEC% app.py
pause
:: Presently Biometric Attendance System
