"""Phase 1B V2 · 原生工具层性能压测脚本。

用法：
    uv run python services/agent/tests/bench_builtin.py              # 默认 256MB
    uv run python services/agent/tests/bench_builtin.py --size-gb 1  # 1GB
    uv run python services/agent/tests/bench_builtin.py --size-gb 100 --hash-only

覆盖：
    - builtin_stat_file  文件元数据（瞬时）
    - 流式 sha256       大文件 hash 吞吐（MB/s；流式实现，内存 O(1)）
    - builtin_write_file / move_file / delete_file 往返
    - builtin_shell      echo 延迟（已过 HITL 语义，直接调执行器）
    - builtin_glob       1000 文件 glob 匹配

说明：
    - `builtin_hash`（Rust/Python）会把整个文件读入内存，100GB 级压测应使用
      流式 hash（本脚本默认）；100GB 随机访问归 Phase 2F+ logviewer 层。
    - 报告输出 JSON：bench_builtin_report.json。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import tempfile
import time
from pathlib import Path


def _mbps(bytes_count: int, seconds: float) -> float:
    return (bytes_count / (1024 * 1024)) / seconds if seconds > 0 else 0.0


async def bench_hash_streaming(path: Path) -> dict:
    """流式 sha256 —— 大文件 hash 吞吐（内存 O(1)）。"""
    start = time.perf_counter()
    size = path.stat().st_size
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    elapsed = time.perf_counter() - start
    return {
        "op": "hash_sha256_streaming",
        "size_bytes": size,
        "elapsed_sec": round(elapsed, 3),
        "throughput_mbps": round(_mbps(size, elapsed), 1),
        "hash_prefix": h.hexdigest()[:16],
    }


async def bench_round_trip(base: Path) -> dict:
    """write_file + move_file + delete_file 往返。"""
    from agent.builtin.files import (
        builtin_delete_file,
        builtin_move_file,
        builtin_write_file,
    )

    src = base / "bench_src.txt"
    dest = base / "bench_dest.txt"
    content = "x" * 1024 * 1024

    t0 = time.perf_counter()
    r = await builtin_write_file(str(src), content, overwrite=True)
    t_write = time.perf_counter() - t0
    assert r.ok, r.error

    t0 = time.perf_counter()
    r = await builtin_move_file(str(src), str(dest), overwrite=True)
    t_move = time.perf_counter() - t0
    assert r.ok, r.error

    t0 = time.perf_counter()
    r = await builtin_delete_file(str(dest))
    t_delete = time.perf_counter() - t0
    assert r.ok, r.error

    return {
        "op": "write_move_delete_round_trip",
        "payload_bytes": len(content),
        "write_sec": round(t_write, 3),
        "move_sec": round(t_move, 3),
        "delete_sec": round(t_delete, 3),
    }


async def bench_glob(base: Path) -> dict:
    """glob 1000 文件匹配。"""
    import glob as _glob

    tree = base / "tree"
    tree.mkdir(exist_ok=True)
    for i in range(1000):
        (tree / f"file_{i:04d}.log").write_text("line\n" * 10)

    t0 = time.perf_counter()
    hits = _glob.glob(str(tree / "**" / "*.log"), recursive=True)
    elapsed = time.perf_counter() - t0
    return {
        "op": "glob_1000_files",
        "matches": len(hits),
        "elapsed_sec": round(elapsed, 3),
    }


async def bench_shell() -> dict:
    """shell echo 延迟（走内置执行器，不含 HITL）。"""
    from agent.builtin.shell import builtin_shell

    t0 = time.perf_counter()
    r = await builtin_shell("echo hi", allowed_prefixes=["echo"], timeout_sec=10)
    elapsed = time.perf_counter() - t0
    assert r.ok, r.error
    return {
        "op": "shell_echo",
        "elapsed_sec": round(elapsed, 3),
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description="EAIDE builtin 工具性能压测")
    parser.add_argument("--size-gb", type=float, default=0.25, help="大文件压测大小（GB）")
    parser.add_argument("--hash-only", action="store_true", help="只跑大文件 hash 压测")
    args = parser.parse_args()

    base = Path(tempfile.mkdtemp(prefix="eaide_bench_"))
    report: dict = {"size_gb": args.size_gb}
    try:
        big = base / "big.bin"
        size_bytes = int(args.size_gb * 1024 * 1024 * 1024)
        if size_bytes > 0:
            report["big_file"] = await bench_hash_streaming_generate(big, size_bytes)
        if not args.hash_only:
            report["round_trip"] = await bench_round_trip(base)
            report["glob"] = await bench_glob(base)
            report["shell"] = await bench_shell()
    finally:
        import shutil

        shutil.rmtree(base, ignore_errors=True)

    out = Path(__file__).with_name("bench_builtin_report.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


async def bench_hash_streaming_generate(path: Path, size_bytes: int) -> dict:
    """生成大文件并做流式 hash（分块生成 + 分块更新，双份 I/O）。"""
    start = time.perf_counter()
    h = hashlib.sha256()
    chunk = b"a" * (1024 * 1024)
    written = 0
    with open(path, "wb") as f:
        while written < size_bytes:
            n = min(len(chunk), size_bytes - written)
            f.write(chunk[:n])
            h.update(chunk[:n])
            written += n
    elapsed = time.perf_counter() - start
    return {
        "op": "generate_and_hash",
        "size_bytes": size_bytes,
        "elapsed_sec": round(elapsed, 3),
        "throughput_mbps": round(_mbps(size_bytes, elapsed), 1),
        "hash_prefix": h.hexdigest()[:16],
    }


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
