@echo off
REM ====================================================================
REM  Local server launcher
REM
REM  Started by run_allages.cmd, or double-click it directly.
REM
REM  THREE TRAPS THIS FILE EXISTS TO AVOID:
REM
REM  1) NESTED QUOTING.  The old one-liner was
REM         start "srv" /min cmd /c "cd /d \"%~dp0server\" && ... && python server.py"
REM     cmd.exe does NOT treat \" as an escape -- it reads a literal
REM     backslash and then a CLOSING quote, so the inner command became
REM     `cd /d \`, which fails, and `&&` short-circuited everything else.
REM     The server never started; the window just flashed.
REM
REM  2) NO PYTHON ON THE PLAYER'S MACHINE.  This package therefore SHIPS ITS
REM     OWN interpreter in ..\runtime (official python.org embedded build).
REM     Never rely on a system Python: on this very machine `where python`
REM     resolves to
REM         %LOCALAPPDATA%\Microsoft\WindowsApps\python.exe
REM     which is the Microsoft Store alias STUB -- it exits with 9009 and
REM     prints nothing.  There is no real Python directory on PATH at all.
REM
REM  3) EMBEDDED PYTHON ISOLATION.  With a ._pth file present, embedded
REM     Python does NOT add the script's directory to sys.path, so
REM     `from aes_local import cbc_encrypt` silently failed and every
REM     response went out as PLAINTEXT.  runtime\python311._pth now lists
REM     `..\server` so the import resolves.
REM ====================================================================
setlocal
cd /d "%~dp0"

REM -- 1. the interpreter that ships WITH this package (always preferred) ----
set "PY="
if exist "%~dp0..\runtime\python.exe" set "PY=%~dp0..\runtime\python.exe"

REM -- 2. a real system Python, absolute paths only -------------------------
if not defined PY for %%P in (
  "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
  "C:\Python313\python.exe"
  "C:\Python312\python.exe"
  "C:\Python311\python.exe"
  "C:\Program Files\Python313\python.exe"
  "C:\Program Files\Python312\python.exe"
  "C:\Program Files\Python311\python.exe"
) do (
  if not defined PY if exist "%%~P" set "PY=%%~P"
)

REM -- 3. the py launcher (absolute), then PATH minus the Store stub ---------
if not defined PY if exist "%SystemRoot%\py.exe" set "PY=%SystemRoot%\py.exe"
if not defined PY (
  for /f "delims=" %%W in ('where python 2^>nul') do (
    echo %%W | find /i "WindowsApps" >nul || if not defined PY set "PY=%%W"
  )
)

if not defined PY (
  echo.
  echo   [!] No Python found, and the bundled interpreter is missing.
  echo       Expected: %~dp0..\runtime\python.exe
  echo       Re-extract the package so that the runtime\ folder is present.
  echo.
  pause
  exit /b 9009
)

REM -- save / port ----------------------------------------------------------
REM   ANGELICA_USER=new  ->  db_new  (the fresh new-game save)
REM   ANGELICA_USER=     ->  db      (day-one save)
REM   ANGELICA_USER=X    ->  db_X
if not defined ANGELICA_PORT set "ANGELICA_PORT=18080"
if not defined ANGELICA_USER set "ANGELICA_USER=new"

echo.
echo   python   : %PY%
echo   save     : db_%ANGELICA_USER%
echo   port     : %ANGELICA_PORT%
echo   logfile  : %~dp0server.log
echo.
echo   (close this window to stop the server)
echo.
"%PY%" "%~dp0server.py"
set "RC=%ERRORLEVEL%"
echo.
echo   server exited (code %RC%).
pause
endlocal