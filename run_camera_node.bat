@echo off
title Presently Edge Camera Node (OpenCV + MediaPipe)
echo ===================================================================
echo   PRESENTLY : EDGE CAMERA NODE
echo   OpenCV Video Capture + MediaPipe Anti-Proxy Liveness Verification
echo ===================================================================
echo.
echo Connecting to Hub Server at http://localhost:3000...
echo Press 'q' inside video window to quit.
echo Press 's' to refresh active session.
echo ===================================================================

python camera_node.py --hub http://localhost:3000 --camera 0
pause
:: Presently Biometric Attendance System
