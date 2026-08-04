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
