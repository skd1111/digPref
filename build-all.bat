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

:: PPT Master 运行时：2026-09-03 起不再内置（vendor/ppt-master 已移出 tauri bundle.resources，
:: PPT 技能改由用户经「设置 → 技能」/skills/import 自行上传）；安装包因此瘦身 ~100MB。

:: 确保 bge-reranker ONNX 模型（本地知识库混合检索重排；~266MB，不入 git，构建时拉取；
:: 缺失不阻断——reranker 静默 no-op，混合检索保持 RRF 融合序；bge-small 向量模型已入 git 无需拉取）
if not exist "d:\ditPref\model\bge-reranker-base-onnx\onnx\model_quantized.onnx" (
    echo [INFO] 未发现 bge-reranker 模型，尝试拉取...
    powershell -NoProfile -ExecutionPolicy Bypass -File "d:\ditPref\model\download_bge_reranker_onnx.ps1"
    if not exist "d:\ditPref\model\bge-reranker-base-onnx\onnx\model_quantized.onnx" (
        echo [WARN] bge-reranker 未就位，安装包将不含重排模型（混合检索退化为 RRF 序，功能不中断）
    )
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

:: ---- Step 1b: 精简 config/driver（移除已冻结进 exe 的冗余 wheel + 运行时再生的 _site）----
:: config/driver 作为 tauri 资源落到安装目录；numpy/pandas/cryptography 等已冻结进 exe，
:: 只保留 exe 未冻结的少数驱动（oracledb/clickhouse/aioodbc/pyodbc/lz4/backports_zstd）
echo [1b] 精简 config/driver 冗余离线驱动 ...
call uv run python infra\scripts\prune-driver-bundle.py
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
