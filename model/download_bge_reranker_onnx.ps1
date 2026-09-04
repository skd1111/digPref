# 从 hf-mirror 下载 bge-reranker-base ONNX 模型文件（一次性，开发/打包机执行）。
# 下载后目录结构：model/bge-reranker-base-onnx/{tokenizer.json,config.json,onnx/model_quantized.onnx}
# Agent 运行时由 knowledge/reranker.py 按 local_reranker_onnx_dir 解析（cwd > _MEIPASS > 仓库根）。
# 未下载时 reranker 静默 no-op，混合检索保持 RRF 融合序，功能不中断。
$ErrorActionPreference = 'Stop'
$base = 'https://hf-mirror.com/Xenova/bge-reranker-base/resolve/main'
$dest = Join-Path $PSScriptRoot 'bge-reranker-base-onnx'
New-Item -ItemType Directory -Force -Path $dest, "$dest\onnx" | Out-Null

# 优先量化版（体积小、纯 CPU 快）；无量化版则回退 fp32 model.onnx
$files = @(
    @('tokenizer.json', 'tokenizer.json'),
    @('config.json', 'config.json')
)
foreach ($f in $files) {
    $url = "$base/$($f[0])"
    $out = Join-Path $dest $f[1]
    Write-Host "Downloading $($f[0]) ..."
    Invoke-WebRequest -Uri $url -OutFile $out -TimeoutSec 600
    Write-Host "  -> $out ($([math]::Round((Get-Item $out).Length / 1MB, 1)) MB)"
}

$onnxCandidates = @('onnx/model_quantized.onnx', 'onnx/model.onnx')
$got = $false
foreach ($rel in $onnxCandidates) {
    $out = Join-Path $dest ($rel -replace '/', '\')
    try {
        Write-Host "Downloading $rel ..."
        Invoke-WebRequest -Uri "$base/$rel" -OutFile $out -TimeoutSec 900
        Write-Host "  -> $out ($([math]::Round((Get-Item $out).Length / 1MB, 1)) MB)"
        $got = $true
        break
    } catch {
        Write-Host "  skip $rel（不存在或下载失败）"
    }
}
if (-not $got) { throw '未找到可用的 onnx 模型文件（model_quantized.onnx / model.onnx）' }
Write-Host 'Done.'
