@echo off
REM Wrapper: source vcvars64.bat then add cargo to PATH then run cargo with args.
call "E:\env\VS2022BuildTools\VC\Auxiliary\Build\vcvars64.bat" >nul 2>&1
if errorlevel 1 (
  echo failed to load vcvars64.bat
  exit /b 1
)
set "PATH=%USERPROFILE%\.cargo\bin;%PATH%"
cd /d e:\digPref2\digPref\apps\desktop\src-tauri
cargo %*