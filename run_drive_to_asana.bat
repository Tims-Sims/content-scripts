@echo off
setlocal
cd /d "%~dp0"
set "PIP_DISABLE_PIP_VERSION_CHECK=1"
set "PYTHON_EXE=.venv\Scripts\python.exe"

if exist "%PYTHON_EXE%" goto check_dependencies

echo Creating the Python environment...
where py >nul 2>&1
if errorlevel 1 goto use_python
py -3 -m venv .venv
goto environment_created

:use_python
python -m venv .venv

:environment_created
if errorlevel 1 goto setup_failed

:check_dependencies
"%PYTHON_EXE%" -c "import googleapiclient, google_auth_oauthlib, keyring, requests" >nul 2>&1
if not errorlevel 1 goto run_wizard

echo Installing required packages...
"%PYTHON_EXE%" -m pip install -r requirements.txt
if errorlevel 1 goto setup_failed

:run_wizard
echo.
"%PYTHON_EXE%" drive_to_asana.py
set "RESULT=%ERRORLEVEL%"
echo.
pause
exit /b %RESULT%

:setup_failed
echo.
echo Setup failed. Confirm that Python 3.10 or newer is installed and try again.
echo.
pause
exit /b 1
