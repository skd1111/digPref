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

:: 确保 OfficeCLI 二进制（V9 Office 能力，随安装包内置；缺失不阻断，运行时降级）
if not exist "d:\ditPref\vendor\officecli\officecli-win-x64.exe" (
    echo [INFO] 未发现 OfficeCLI 二进制，尝试拉取...
    powershell -NoProfile -ExecutionPolicy Bypass -File "d:\ditPref\infra\scripts\fetch-officecli.ps1"
    if not exist "d:\ditPref\vendor\officecli\officecli-win-x64.exe" (
        echo [WARN] OfficeCLI 未就位，安装包将不含 Office 引擎（运行时报 officecli_not_installed）
    )
)

:: 确保 PPT Master 运行时（V9.7：技能包 + 嵌入式 Python + 离线依赖；缺失不阻断，运行时友好降级）
if not exist "d:\ditPref\vendor\ppt-master\SKILL.md" (
    echo [INFO] 未发现 PPT Master 运行时，尝试拉取...
    powershell -NoProfile -ExecutionPolicy Bypass -File "d:\ditPref\infra\scripts\fetch-ppt-master.ps1"
)
if not exist "d:\ditPref\vendor\ppt-master\SKILL.md" (
    echo [WARN] PPT Master 运行时未就位，安装包将不含 PPT 生成技能（运行时种子提示技能包缺失）
)

:: 构建 eaide-executor（Rust 本地执行器独立二进制；随 Agent exe / 安装包分发，
:: 缺失不阻断 —— Agent 保持 Python 原生兜底）
if not exist "d:\ditPref\vendor\executor\eaide-executor.exe" (
    echo [INFO] 构建 eaide-executor.exe ...
    call "d:\ditPref\build-with-msvc.bat" build --release --bin eaide-executor
    if exist "d:\ditPref\apps\desktop\src-tauri\target\release\eaide-executor.exe" (
        mkdir "d:\ditPref\vendor\executor" 2>nul
        copy /y "d:\ditPref\apps\desktop\src-tauri\target\release\eaide-executor.exe" "d:\ditPref\vendor\executor\" >nul
        echo [INFO] eaide-executor.exe 已拷入 vendor\executor
    ) else (
        echo [WARN] eaide-executor 构建失败，Agent 将保持 Python 原生兜底（不影响主流程）
    )
)

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
