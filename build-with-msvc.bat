@echo off
REM Wrapper: source vcvars64.bat then add cargo to PATH then run cargo with args.
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat" >nul 2>&1
if errorlevel 1 (
  echo failed to load vcvars64.bat
  exit /b 1
)
set "PATH=C:\Users\79834\.cargo\bin;%PATH%"
cd /d d:\ditPref\apps\desktop\src-tauri
cargo %*