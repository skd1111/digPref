"""EAIDE 增量更新应用器（spec: docs/superpowers/specs/2026-11-03-agent-web-incremental-update-design.md §3/§4）。

仅标准库；子命令：gen-manifest / apply / verify / rollback / status / list。
更新模型：安装目录旁 versions/<pkg>/<ver>/ 自包含版本目录 + 单行指针 <pkg>-active.txt，
激活 = 指针原子替换（手动兜底协议：拷目录 + 改指针 + 重启，见 spec §2）。
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

SCHEMA_VERSION = 1
VERSION_RE = re.compile(r"^[0-9A-Za-z._-]+$")


class ManifestError(ValueError):
    pass


def _safe_relpath(p: str) -> str:
    """manifest 文件路径必须是相对版本目录根的 POSIX 风格路径，拒绝穿越。"""
    pp = PurePosixPath(p)
    if (
        not p
        or pp.is_absolute()
        or re.match(r"^[A-Za-z]:", p)
        or ".." in pp.parts
    ):
        raise ManifestError(f"unsafe manifest path: {p!r}")
    return pp.as_posix()


def gen_manifest(root: Path) -> list[dict]:
    """遍历目录产出 files 列表（path 为 POSIX 相对路径；跳过目录与应用器自身缓存）。"""
    root = Path(root)
    out = []
    for p in sorted(root.rglob("*")):
        if p.is_dir() or p.name == ".filehash.json":
            continue
        data = p.read_bytes()
        out.append(
            {
                "path": p.relative_to(root).as_posix(),
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
            }
        )
    return out


@dataclass
class Manifest:
    package: str
    version: str
    base_url: str
    files: list[dict]
    min_shell: str | None = None
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, doc: dict) -> Manifest:
        if doc.get("schema") != SCHEMA_VERSION:
            raise ManifestError(f"unsupported schema: {doc.get('schema')!r}")
        if doc.get("package") not in ("agent", "web"):
            raise ManifestError(f"bad package: {doc.get('package')!r}")
        version = doc.get("version", "")
        if not VERSION_RE.match(version):
            raise ManifestError(f"bad version: {version!r}")
        files = doc.get("files") or []
        seen: set[str] = set()
        for f in files:
            f["path"] = _safe_relpath(f["path"])
            if f["path"] in seen:
                raise ManifestError(f"duplicate path: {f['path']}")
            seen.add(f["path"])
            if not re.match(r"^[0-9a-f]{64}$", f.get("sha256", "")):
                raise ManifestError(f"bad sha256 for {f['path']}")
            if not isinstance(f.get("size"), int) or f["size"] < 0:
                raise ManifestError(f"bad size for {f['path']}")
        return cls(
            package=doc["package"],
            version=version,
            base_url=doc.get("baseUrl", ""),
            files=files,
            min_shell=doc.get("minShell"),
            raw=doc,
        )

    @classmethod
    def load(cls, src) -> Manifest:
        """src 可为本地路径 / file:/// URI / http(s) URL。"""
        s = str(src)
        if s.startswith(("http://", "https://")):
            from urllib.request import urlopen

            return cls.from_dict(json.loads(urlopen(s).read().decode("utf-8")))
        p = Path(s.replace("file:///", "/").replace("file://", ""))
        if p.exists():
            return cls.from_dict(json.loads(p.read_text(encoding="utf-8")))
        raise ManifestError(f"manifest not found: {src}")


def _cmd_gen_manifest(args):
    files = gen_manifest(Path(args.dir))
    doc = {
        "schema": SCHEMA_VERSION,
        "package": args.package,
        "version": args.version,
        "baseUrl": args.base_url,
        "files": files,
    }
    if args.min_shell:
        doc["minShell"] = args.min_shell
    Path(args.out).write_text(
        json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[gen-manifest] {len(files)} files -> {args.out}")


def main(argv=None):
    import argparse

    ap = argparse.ArgumentParser(prog="eaide_update")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gen-manifest", help="从 one-dir 产物/dist 目录生成 manifest")
    g.add_argument("--dir", required=True)
    g.add_argument("--package", required=True, choices=["agent", "web"])
    g.add_argument("--version", required=True)
    g.add_argument("--base-url", required=True, dest="base_url")
    g.add_argument("--min-shell", dest="min_shell", default=None)
    g.add_argument("--out", required=True)
    g.set_defaults(fn=_cmd_gen_manifest)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main() or 0)
