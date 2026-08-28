@echo off
REM ===========================================================================
REM  hunter-v2 setup launcher.
REM
REM  RUN AS ADMINISTRATOR. Keep this file in the repository root: the
REM  PowerShell script next to it derives the project path from its own folder.
REM
REM  This .bat is deliberately ASCII-only and does nothing but launch the
REM  PowerShell script. Reason, found by testing on 2026-08-28: cmd.exe parses
REM  a batch file in the console code page, so Cyrillic text inside a .bat is
REM  executed as garbage commands ("'e' is not recognized") and escaped && in
REM  echo lines breaks the parser. PowerShell handles UTF-8 correctly.
REM ===========================================================================

setlocal
set "PS1=%~dp0setup-windows.ps1"

if not exist "%PS1%" (
  echo ERROR: setup-windows.ps1 not found next to this file.
  echo Keep both files together in the repository root.
  pause
  exit /b 1
)

net session >nul 2>&1
if not %errorlevel%==0 (
  echo NOTE: not running as administrator - the power-settings step will be skipped.
  echo Right-click this file and choose "Run as administrator" to include it.
  echo.
)

REM Prefer PowerShell 7 when present (better UTF-8), fall back to Windows PowerShell.
where pwsh >nul 2>&1
if %errorlevel%==0 (
  pwsh -NoProfile -ExecutionPolicy Bypass -File "%PS1%"
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%"
)
set "RC=%errorlevel%"
echo.
if not "%RC%"=="0" echo Script exited with code %RC%.
pause
endlocal
exit /b %RC%
