# 拉取 OfficeCLI 二进制（V9 Office 能力 · 随安装包内置，2026-08-25）
#
# OfficeCLI（iOfficeAI，Apache 2.0）：单二进制 Office 引擎，无需安装 Office，
# 供 builtin_office_* 工具族与 /office/preview 预览端点使用。
#
# 用法：
#   .\infra\scripts\fetch-officecli.ps1                              # 最新版 win-x64
#   .\infra\scripts\fetch-officecli.ps1 -Version 1.0.150             # 锁定版本
#   .\infra\scripts\fetch-officecli.ps1 -Version 1.0.150 -Sha256 ABC # 带校验
#
# 产物落 vendor/officecli/（该目录已入 .gitignore，仅本地 / 构建机持有；
# PyInstaller spec 与 tauri.conf.json 均按「缺失自动跳过」处理）。

param(
    [string]$Version = "latest",
    [ValidateSet("win-x64", "win-arm64", "linux-x64", "linux-arm64", "mac-x64", "mac-arm64")]
    [string]$Platform = "win-x64",
    [string]$Sha256 = ""
)

$ErrorActionPreference = "Stop"

$repo = "iOfficeAI/OfficeCLI"

# 仓库根 = 脚本位置向上两级（infra/scripts → repo root）
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$destDir = Join-Path $repoRoot "vendor\officecli"

if ($Version -eq "latest") {
    # Releases 制品名带完整版本号（如 officecli-win-x64-1.0.150.exe），
    # latest 时经 API 查真实 asset 名；指定版本时按规范名直链尝试。
    Write-Host "[fetch-officecli] resolving latest release assets..."
    $rel = Invoke-RestMethod -Uri "https://api.github.com/repos/$repo/releases/latest" -UseBasicParsing
    $asset = $rel.assets | Where-Object { $_.name -like "officecli-$Platform*" } | Select-Object -First 1
    if (-not $asset) {
        Write-Error "[fetch-officecli] release $($rel.tag_name) 未找到 $Platform 制品"
    }
    $url = $asset.browser_download_url
    $binaryName = $asset.name
} else {
    $tag = if ($Version.StartsWith("v")) { $Version } else { "v$Version" }
    $binaryName = if ($Platform -like "win-*") { "officecli-$Platform.exe" } else { "officecli-$Platform" }
    $url = "https://github.com/$repo/releases/download/$tag/$binaryName"
}

$dest = Join-Path $destDir $binaryName

New-Item -ItemType Directory -Force -Path $destDir | Out-Null
Write-Host "[fetch-officecli] downloading: $url"
try {
    Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing
} catch {
    Write-Error "[fetch-officecli] download failed: $($_.Exception.Message)`n" +
        "内网环境可手动下载 $binaryName 放入 $destDir"
}

$actual = (Get-FileHash -Path $dest -Algorithm SHA256).Hash
if ($Sha256) {
    if ($actual -ne $Sha256.ToUpper()) {
        Remove-Item $dest -Force
        Write-Error "[fetch-officecli] SHA256 mismatch! expected=$Sha256 actual=$actual"
    }
    Write-Host "[fetch-officecli] SHA256 verified: $actual"
} else {
    Write-Warning "[fetch-officecli] no -Sha256 provided; computed hash (pin it for reproducible builds): $actual"
}

Write-Host "[fetch-officecli] done: $dest ($([math]::Round((Get-Item $dest).Length / 1MB, 1)) MB)"
