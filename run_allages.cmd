@echo off
REM ====================================================================
REM  One-click offline launch
REM
REM  Starts the local server (18080, save db_new), waits until it really
REM  accepts connections, then launches the client.
REM
REM  The server is started via server\start_new.cmd instead of an inline
REM  `cd /d \"%~dp0server\" && ...` line -- cmd.exe does not treat \" as an
REM  escape, so that form silently failed and the server never came up.
REM
REM  The port check uses `netstat` rather than PowerShell: it is always
REM  present, starts instantly, and avoids a nested-quoting minefield.
REM ====================================================================
setlocal
cd /d "%~dp0"

echo.
echo   [1/2] starting the local server ...
start "angelica-server" /min "%~dp0server\start_new.cmd"

REM -- wait for 18080 (max ~40s) --------------------------------------------
set /a _n=0
:wait
set /a _n+=1
set "_up=0"
netstat -ano | findstr /r /c:":18080 " | findstr /i "LISTENING" >nul 2>&1 && set "_up=1"
if "%_up%"=="1" goto ready
if %_n% GEQ 40 goto slow
timeout /t 1 /nobreak >nul
goto wait

:ready
echo         listening on 18080  (after %_n%s)
echo.
echo   [2/2] launching the game ...
cd /d "%~dp0client"
start "" angelica.exe
echo.
echo   server log : %~dp0server\server.log
echo   plugin log : %~dp0client\LabDump\local.log
echo   game log   : %%USERPROFILE%%\AppData\LocalLow\ ^<- the game's own folder, Player.log
echo.
echo   Close the minimised "angelica-server" window to stop the server.
echo.
endlocal
exit /b 0

:slow
echo.
echo   [!] 18080 did not open within 40 seconds.
echo       Run server\start_new.cmd by hand -- that window shows the reason.
echo       Also check %~dp0server\server.log
echo.
pause
endlocal
exit /b 1