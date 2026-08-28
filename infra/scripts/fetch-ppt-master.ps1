# 拉取 PPT Master 运行时（V9.7 · 随安装包内置，2026-08-26）
#
# PPT Master（hugohe3/ppt-master，MIT）：路由式演示文稿工作流技能包，
# 由内置种子 office_pptx_designer 驱动，生成原生可编辑 PPTX。
#
# 拉取三件套（全部离线随包分发，产物目录已入 .gitignore）：
#   1. vendor/ppt-master/            技能包（SKILL.md / workflows / scripts / templates）
#   2. vendor/python/                嵌入式 CPython 3.12（win-amd64 embeddable）
#   3. vendor/ppt-master/deps/       离线依赖 wheel（cp312 win_amd64）
#
# 用法：
#   .\infra\scripts\fetch-ppt-master.ps1
#   .\infra\scripts\fetch-ppt-master.ps1 -Ref main            # 锁定分支/提交
#   .\infra\scripts\fetch-ppt-master.ps1 -SkipDeps            # 只拉技能包与 Python
#
# 仅 Windows 产物（嵌入式 Python 与 wheel 均为 win_amd64）；非 Windows 直接退出 0，
# 由调用方（release.yml / build-all.bat）退占位。缺失时运行时友好降级，不阻断启动。

param(
    [string]$Ref = "main",
    [switch]$SkipDeps
)

$ErrorActionPreference = "Stop"

if ($IsMacOS -or $IsLinux) {
    Write-Host "[fetch-ppt-master] non-Windows host, skip (runtime is win_amd64 only)"
    exit 0
}

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$skillDir = Join-Path $repoRoot "vendor\ppt-master"
$pyDir = Join-Path $repoRoot "vendor\python"
$tmp = Join-Path $env:TEMP "fetch-ppt-master"
New-Item -ItemType Directory -Force -Path $tmp, $skillDir, $pyDir | Out-Null

function Resolve-Python {
    foreach ($cand in @("py", "python", "python3")) {
        $exe = Get-Command $cand -ErrorAction SilentlyContinue
        if ($exe) { return $exe.Source }
    }
    throw "未找到可用 Python（拉取脚本解压 wheel 依赖需要）"
}

# ---- 1. 技能包（tarball；ghfast 代理优先，失败回退直连）----
$tarball = Join-Path $tmp "ppt-master.tar.gz"
$urls = @(
    "https://ghfast.top/https://github.com/hugohe3/ppt-master/archive/refs/heads/$Ref.tar.gz",
    "https://github.com/hugohe3/ppt-master/archive/refs/heads/$Ref.tar.gz"
)
if (-not (Test-Path (Join-Path $skillDir "SKILL.md"))) {
    $ok = $false
    foreach ($u in $urls) {
        try {
            Write-Host "[fetch-ppt-master] downloading skill tarball: $u"
            Invoke-WebRequest -Uri $u -OutFile $tarball -UseBasicParsing
            $ok = $true
            break
        } catch {
            Write-Warning "[fetch-ppt-master] download failed: $($_.Exception.Message)"
        }
    }
    if (-not $ok) { throw "ppt-master 技能包下载失败（内网可手动下载后解压 skills/ppt-master 到 $skillDir）" }

    Write-Host "[fetch-ppt-master] extracting skill package..."
    tar -xzf $tarball -C $tmp
    $extracted = Get-ChildItem $tmp -Directory | Where-Object { $_.Name -like "ppt-master-*" } | Select-Object -First 1
    if (-not $extracted) { throw "解压后未找到 ppt-master-* 目录" }
    # 保留本地 deps/（wheel 单独拉取，上游包不含）
    $src = Join-Path $extracted.FullName "skills\ppt-master"
    Copy-Item (Join-Path $src "*") $skillDir -Recurse -Force
    Write-Host "[fetch-ppt-master] skill ready: $skillDir"
} else {
    Write-Host "[fetch-ppt-master] SKILL.md exists, skip skill download"
}

# ---- 2. 嵌入式 Python 3.12（最后一个带二进制的 3.12 版本）----
if (-not (Test-Path (Join-Path $pyDir "python.exe"))) {
    $pyUrl = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip"
    $pyZip = Join-Path $tmp "py312embed.zip"
    Write-Host "[fetch-ppt-master] downloading embeddable python: $pyUrl"
    Invoke-WebRequest -Uri $pyUrl -OutFile $pyZip -UseBasicParsing
    Expand-Archive -Path $pyZip -DestinationPath $pyDir -Force
    Write-Host "[fetch-ppt-master] embedded python ready: $pyDir"
} else {
    Write-Host "[fetch-ppt-master] python.exe exists, skip python download"
}

# ---- 3. 离线依赖 wheel（cp312 win_amd64）----
if ($SkipDeps) {
    Write-Host "[fetch-ppt-master] -SkipDeps, done"
    exit 0
}
$depsDir = Join-Path $skillDir "deps"
New-Item -ItemType Directory -Force -Path $depsDir | Out-Null
$packages = @(
    "PyYAML>=6.0", "python-pptx>=0.6.21", "XlsxWriter>=3.0.0", "skia-pathops>=0.9.2",
    "uharfbuzz>=0.50.0", "Pillow>=9.0.0", "numpy>=1.20.0", "lxml",
    "PyMuPDF>=1.23.0", "mammoth>=1.6.0", "markdownify>=0.11.6", "beautifulsoup4>=4.12.0",
    "ebooklib>=0.18", "nbconvert>=7.0.0", "openpyxl>=3.1.0", "requests>=2.31.0",
    "flask>=3.0.0", "edge-tts>=7.2.8"
)
$py = Resolve-Python
Write-Host "[fetch-ppt-master] downloading wheels via $py ..."
& $py -m pip download --dest $depsDir --only-binary=:all: `
    --platform win_amd64 --platform any --python-version 312 `
    --implementation cp --abi cp312 --abi none @packages
if ($LASTEXITCODE -ne 0) { throw "wheel 下载失败（内网可手动把 cp312 wheel 放入 $depsDir）" }

$whlCount = (Get-ChildItem $depsDir -Filter *.whl).Count
Write-Host "[fetch-ppt-master] done: skill + python + $whlCount wheels"
