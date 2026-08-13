@echo off
chcp 65001 >nul
setlocal

echo ============================================
echo   EAIDE 全量构建（Agent exe + 桌面安装包）
echo ============================================
echo.

:: 确保 cargo 在 PATH 里
if not exist "%USERPROFILE%\.cargo\bin\cargo.exe" (
    echo [ERROR] 未找到 Rust/Cargo，请先安装: winget install Rustlang.Rustup
    pause
    exit /b 1
)
set "PATH=%USERPROFILE%\.cargo\bin;%PATH%"

:: 确保 UPX 可用（exe 压缩，spec 已开 upx=True；新装的可能还没进当前 shell 的 PATH）
where upx >nul 2>nul
if errorlevel 1 (
    for /d %%i in ("%LOCALAPPDATA%\Microsoft\WinGet\Packages\UPX.UPX_*") do (
        for /d %%j in ("%%i\upx-*-win64") do set "PATH=%%j;%PATH%"
    )
)
where upx >nul 2>nul && echo [INFO] UPX 已启用，产物将被压缩 || echo [WARN] 未找到 UPX，产物不压缩（winget install UPX.UPX 可安装）

:: ---- Step 1: PyInstaller 构建 Agent exe ----
echo [1/2] 构建 eaide-agent.exe ...
cd /d d:\ditPref
call uv run python -m PyInstaller eaide-agent.spec --noconfirm --distpath "apps\desktop\src-tauri\resources" --workpath build
if %ERRORLEVEL% neq 0 (
    echo [ERROR] PyInstaller 构建失败！
    pause
    exit /b 1
)
echo [1/2] Agent exe 构建完成 ✓
echo.

:: ---- Step 2: Tauri 构建桌面安装包 ----
echo [2/2] 构建桌面安装包（pnpm tauri build）...
cd /d d:\ditPref\apps\desktop
call pnpm tauri build
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Tauri 构建失败！
    pause
    exit /b 1
)
echo [2/2] 桌面安装包构建完成 ✓
echo.

echo ============================================
echo   全部完成！
echo   安装包位置: apps\desktop\src-tauri\target\release\bundle\nsis\
echo ============================================
pause
