@echo off
rem Thin wrapper around scripts\build.ps1 so it can be double-clicked or run
rem from cmd.exe. All arguments are passed through (e.g. -Clean -Reindex).
chcp 65001 >nul
setlocal
set "SCRIPT_DIR=%~dp0"

where pwsh >nul 2>nul
if errorlevel 1 (
    echo [info] pwsh not found, falling back to Windows PowerShell 5.1.
    powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%build.ps1" %*
) else (
    pwsh -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%build.ps1" %*
)

set "RC=%errorlevel%"
if not "%RC%"=="0" (
    echo.
    echo [error] build failed with exit code %RC%.
    exit /b %RC%
)
endlocal
exit /b 0
