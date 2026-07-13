@echo off
setlocal
if "%~1"=="" (
  ecu-release-gui
) else (
  ecu-release %*
)
exit /b %errorlevel%
