# 从 hf-mirror 下载 bge-small-zh-v1.5 ONNX 模型文件（一次性，开发机执行）
$ErrorActionPreference = 'Stop'
$base = 'https://hf-mirror.com/Xenova/bge-small-zh-v1.5/resolve/main'
$dest = 'D:\ditPref\model\bge-small-zh-v1.5-onnx'
New-Item -ItemType Directory -Force -Path $dest, "$dest\onnx" | Out-Null
$files = @(
    @('tokenizer.json', 'tokenizer.json'),
    @('tokenizer_config.json', 'tokenizer_config.json'),
    @('config.json', 'config.json'),
    @('onnx/model_quantized.onnx', 'onnx\model_quantized.onnx')
)
foreach ($f in $files) {
    $url = "$base/$($f[0])"
    $out = Join-Path $dest $f[1]
    Write-Host "Downloading $($f[0]) ..."
    Invoke-WebRequest -Uri $url -OutFile $out -TimeoutSec 600
    Write-Host "  -> $out ($([math]::Round((Get-Item $out).Length / 1MB, 1)) MB)"
}
Write-Host 'Done.'
