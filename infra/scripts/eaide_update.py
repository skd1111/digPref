"""EAIDE 增量更新应用器（spec: docs/superpowers/specs/2026-11-03-agent-web-incremental-update-design.md §3/§4）。

仅标准库；子命令：gen-manifest / apply / verify / rollback / status / list。
更新模型：安装目录旁 versions/<pkg>/<ver>/ 自包含版本目录 + 单行指针 <pkg>-active.txt，
激活 = 指针原子替换（手动兜底协议：拷目录 + 改指针 + 重启，见 spec §2）。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

SCHEMA_VERSION = 1
FACTORY = "factory"
IS_WIN = sys.platform == "win32"
VERSION_RE = re.compile(r"[0-9A-Za-z._-]+\Z")  # \Z 锁末尾，杜绝 $ 放行尾部换行


class ManifestError(ValueError):
    pass


def _safe_relpath(p: str) -> str:
    """manifest 文件路径必须是相对版本目录根的 POSIX 风格路径，拒绝穿越与
    应用器保留名：.fetch/.staging 仅拦首段（旁路根目录），.filehash.json 任一段
    均拦（哈希缓存是单层文件，出现在任何层级都会撞缓存命名空间，#I-1）。"""
    pp = PurePosixPath(p)
    if (
        not p
        or not pp.parts  # 裸 "." （parts==()）指向版本目录自身，与 version 校验口径对齐
        or "\\" in p
        or pp.is_absolute()
        or re.match(r"^[A-Za-z]:", p)
        or ".." in pp.parts
    ):
        raise ManifestError(f"unsafe manifest path: {p!r}")
    if pp.parts[0] in (".fetch", ".staging") or ".filehash.json" in pp.parts:
        raise ManifestError(f"reserved manifest path: {p!r}")
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
        if not VERSION_RE.match(version) or version in (".", "..", FACTORY):
            raise ManifestError(f"bad version: {version!r}")
        if not doc.get("baseUrl"):
            raise ManifestError("missing baseUrl (required per spec §3)")
        files = doc.get("files") or []
        seen: set[str] = set()
        for f in files:
            if "path" not in f:
                raise ManifestError(f"file entry missing 'path': {f!r}")
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

            return cls.from_dict(json.loads(urlopen(s, timeout=30).read().decode("utf-8")))
        p = _to_local_path(s)
        if p.exists():
            return cls.from_dict(json.loads(p.read_text(encoding="utf-8")))
        raise ManifestError(f"manifest not found: {src}")


def _to_local_path(s: str) -> Path:
    """file URI → 本地 Path：scheme/权威名大小写不敏感，百分号解码，
    盘符仅对 localhost/空权威剥前导斜杠，非空权威拼 UNC（Review 5409219 S-2）。"""
    from urllib.parse import unquote, urlparse

    u = urlparse(s)
    if u.scheme != "file":
        return Path(s)
    path = unquote(u.path)
    localhost = u.hostname in (None, "", "localhost")
    if localhost and re.match(r"^/[A-Za-z]:", path):  # /C:/... -> C:/...
        path = path[1:]
    if localhost:
        return Path(path)
    return Path(f"//{u.netloc}{path}")  # UNC


# ---- 指针 / 历史（spec §2、§4.2-7）----


def read_active(ptr: Path, default: str = FACTORY) -> str:
    """读激活指针；非法字符一律回退 default（与 Rust 侧 §6 口径一致）。"""
    try:
        v = Path(ptr).read_text(encoding="utf-8").strip()
    except OSError:
        return default
    return v if VERSION_RE.match(v) and v not in (".", "..") else default


def write_active(ptr: Path, version: str) -> None:
    """单行版本号 + LF（§2）；tmp 内容 fsync 后 os.replace 同卷原子替换（§4.2-7）。"""
    ptr = Path(ptr)
    ptr.parent.mkdir(parents=True, exist_ok=True)
    tmp = ptr.with_name(f"{ptr.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(version + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, ptr)


def _append_history(vroot: Path, package: str, old: str, new: str, **extra) -> None:
    import time

    rec = {"ts": int(time.time()), "package": package, "from": old, "to": new, **extra}
    with open(vroot / "history.jsonl", "a", encoding="utf-8") as h:
        h.write(json.dumps(rec) + "\n")


def _ensure_writable(root: Path) -> Path:
    """versions 根可写性前置检查；不可写明确报错不静默兜底（spec §4.2-2/§9）。"""
    vroot = Path(root) / "versions"
    try:
        vroot.mkdir(parents=True, exist_ok=True)
        probe = vroot / ".write-probe"
        probe.write_text("x")
        probe.unlink()
    except OSError as e:
        raise SystemExit(
            f"[apply] versions 根不可写: {vroot} —— {e}\n"
            "请设置 EAIDE_VERSIONS_DIR 指向可写目录（spec §2/§9）"
        ) from e
    return vroot


def cleanup_staging(vroot: Path, max_age_s: int = 24 * 3600) -> None:
    """清扫 .staging 下超龄垃圾目录（spec §4.2-9）；在用暂存不动。

    目录 mtime 只随直接子项增删刷新，长下载可能顶层冻结而深层仍在写，
    故按「整棵树最新 mtime，短路早退」判龄（Review d9c52cc #I-3）。
    """
    st = vroot / ".staging"
    if not st.is_dir():
        return
    now = time.time()
    for entry in st.iterdir():
        try:
            if entry.is_dir() and _tree_newest(entry) < now - max_age_s:
                shutil.rmtree(entry, ignore_errors=True)
        except OSError:
            continue


def _tree_newest(d: Path) -> float:
    """目录树最新 mtime（任一新鲜条目即提前返回，免全量遍历 400MB 暂存）。"""
    newest = d.stat().st_mtime
    for root_dir, dirs, files in os.walk(d):
        for name in (*dirs, *files):
            m = os.stat(os.path.join(root_dir, name)).st_mtime
            if m > newest:
                return m
    return newest


def agent_process_running() -> bool:
    """Windows 下 eaide-agent.exe 是否在跑（tasklist 按镜像名，大小写不敏感，
    PE 头镜像名可能全大写）；非 Win/探测失败保守返 False。"""
    if not IS_WIN:
        return False
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq eaide-agent.exe"],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return False
    return "eaide-agent.exe" in out.lower()


def _reuse_sources(root: Path, vroot: Path, package: str, reuse_from: Path | None) -> list:
    """本地复用候选源：显式 --reuse-from > 当前激活版本目录 > factory 根平铺布局。"""
    sources = []
    if reuse_from:
        sources.append(Path(reuse_from))
    active = read_active(vroot / f"{package}-active.txt")
    if active != FACTORY:
        sources.append(vroot / package / active)
    exe_name = "eaide-agent.exe" if IS_WIN else "eaide-agent"
    if (root / exe_name).exists() or (root / "_internal").is_dir():
        sources.append(root)
    return [s for s in sources if s.is_dir()]


def _try_reuse(sources: list, rel: str, want_sha: str, dest: Path) -> bool:
    """命中候选源同路径且哈希一致 → 硬链（失败退 copy）；否则 False 走取数。"""
    parts = PurePosixPath(rel).parts
    for s in sources:
        cand = s.joinpath(*parts)
        if not cand.is_file():
            continue
        try:
            _check_sha(cand, want_sha)
        except ManifestError:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(cand, dest)
        except OSError:
            shutil.copy2(cand, dest)
        return True
    return False


# ---- 下载/校验（spec §4.2-4/5）----


def _fetch(base_url: str, rel: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    quoted = "/".join(urllib.parse.quote(seg) for seg in PurePosixPath(rel).parts)
    if base_url.startswith(("http://", "https://")):
        src = urllib.parse.urljoin(base_url.rstrip("/") + "/", quoted)
        with urllib.request.urlopen(src, timeout=30) as r, open(dest, "wb") as f:
            shutil.copyfileobj(r, f)
        return
    # 非 http → 本地基目录：file URI 走解析，裸路径直接用（§3 允许两种写法，#5）
    if base_url.startswith("file:"):
        base = _to_local_path(base_url)
    else:
        base = Path(base_url).expanduser().absolute()
    src = base.joinpath(*PurePosixPath(rel).parts)
    shutil.copyfile(src, dest)


def _check_sha(path: Path, want: str) -> None:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    if h.hexdigest() != want:
        raise ManifestError(f"sha256 mismatch: {path} want {want} got {h.hexdigest()}")


def verify_version(vdir: Path, doc: dict) -> None:
    """不读缓存，对版本目录全量重算哈希（spec §4.1）。"""
    m = Manifest.from_dict(json.loads(json.dumps(doc)))  # 深拷贝：from_dict 会原地规范 path
    vdir = Path(vdir)
    for f in m.files:
        p = vdir.joinpath(*PurePosixPath(f["path"]).parts)
        if not p.is_file():
            raise ManifestError(f"missing: {f['path']}")
        try:
            _check_sha(p, f["sha256"])
        except ManifestError:
            raise ManifestError(f"{f['path']}: hash mismatch") from None


def apply_manifest(
    doc: dict,
    root: Path,
    dry_run: bool = False,
    source_dir: Path | None = None,
    reuse_from: Path | str | None = None,
) -> None:
    """spec §4.2 主流程。source_dir 为 --adopt 本地整目录 adopt；None 时走 base_url；
    未变文件优先从 reuse 候选源（激活版/factory 根）硬链复用（§4.2-4）。"""
    m = Manifest.from_dict(json.loads(json.dumps(doc)))
    if dry_run:
        print(f"[apply dry-run] {m.package} {m.version}: {len(m.files)} files")
        return
    root = Path(root)
    vroot = _ensure_writable(root)
    cleanup_staging(vroot)
    final = vroot / m.package / m.version
    ptr = vroot / f"{m.package}-active.txt"
    old = read_active(ptr)
    # Windows 进程锁检查前置（覆盖新建/修复两条分支：修复分支的 os.replace
    # 同样会被运行中 agent 锁住，不拦则用户只能看 PermissionError，#5）；
    # 首次安装（old==factory，出厂 agent 可能正跑但本次只是预热目录）不拦
    if IS_WIN and m.package == "agent" and old not in (m.version, FACTORY):
        if agent_process_running():
            raise SystemExit(
                f"[apply] 检测到 eaide-agent 正在运行（激活 {old}）\n"
                "请先退出 EAIDE 再执行 apply/rollback（不自动杀进程）"
            )
    if final.is_dir():
        # 幂等/修复：逐文件实算校验，损坏/缺失经 .staging 旁路重拉后验，
        # 文件级 os.replace 保证任一时刻 final 内都是完整旧/新文件（#4）；
        # 旁路在 .staging 下（同卷原子），失败时随 except 整删不留垃圾（#I-1/M-1）
        repair = vroot / ".staging" / f"repair-{m.package}-{m.version}-{os.getpid()}"
        try:
            repaired = False
            for f in m.files:
                local = final.joinpath(*PurePosixPath(f["path"]).parts)
                if local.is_file():
                    try:
                        _check_sha(local, f["sha256"])
                        continue
                    except ManifestError:
                        pass
                tmp = repair / PurePosixPath(f["path"])
                base = _dir_to_file_uri(Path(source_dir)) if source_dir else m.base_url
                _fetch(base, f["path"], tmp)
                _check_sha(tmp, f["sha256"])
                local.parent.mkdir(parents=True, exist_ok=True)
                os.replace(tmp, local)
                repaired = True
            if repaired:
                (final / ".filehash.json").write_text(
                    json.dumps({f["path"]: f["sha256"] for f in m.files}), encoding="utf-8"
                )
                print(f"[apply] {m.package} {m.version}: 修复损坏文件后激活")
        except BaseException:
            shutil.rmtree(repair, ignore_errors=True)
            raise
        shutil.rmtree(repair, ignore_errors=True)
    else:
        sources = [] if source_dir else _reuse_sources(root, vroot, m.package, reuse_from)
        staging = vroot / ".staging" / f"{m.package}-{m.version}-{os.getpid()}"
        reused = 0
        try:
            for f in m.files:
                dest = staging.joinpath(*PurePosixPath(f["path"]).parts)
                if sources and _try_reuse(sources, f["path"], f["sha256"], dest):
                    reused += 1
                    continue
                if source_dir is not None:
                    _fetch(_dir_to_file_uri(Path(source_dir)), f["path"], dest)
                else:
                    _fetch(m.base_url, f["path"], dest)
                _check_sha(dest, f["sha256"])
            if reused:
                print(f"[apply] reused {reused}/{len(m.files)} files from local sources")
            final.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging, final)  # 同卷原子落位
            (final / ".filehash.json").write_text(
                json.dumps({f["path"]: f["sha256"] for f in m.files}), encoding="utf-8"
            )
        except BaseException:  # 含 KeyboardInterrupt：400MB 下载期间 Ctrl-C 也不留垃圾
            shutil.rmtree(staging, ignore_errors=True)  # 崩溃不留垃圾（§4.2-9）
            raise
    if old != m.version:
        write_active(ptr, m.version)
        _append_history(vroot, m.package, old, m.version, manifest=str(m.base_url))
    print(f"[apply] activated {m.package} {old} -> {m.version}")


def _dir_to_file_uri(d: Path) -> str:
    """绝对化 + as_uri 百分号编码（相对 --adopt 不可静默解析到盘根，#5）。"""
    return Path(d).expanduser().absolute().as_uri()


def _check_filehash_cache(vdir: Path) -> None:
    """rollback 目标的轻量校验：有 .filehash.json 就逐条实算比对（#7）。"""
    cache = vdir / ".filehash.json"
    if not cache.exists():
        return
    try:
        entries = json.loads(cache.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise SystemExit(f"[rollback] .filehash.json 损坏，拒绝指向 {vdir}") from None
    if not isinstance(entries, dict):
        raise SystemExit(f"[rollback] .filehash.json 损坏（非对象），拒绝指向 {vdir}")
    for rel, want in entries.items():
        p = vdir.joinpath(*PurePosixPath(rel).parts)
        if not p.is_file():
            raise SystemExit(f"[rollback] verify: missing {rel} in {vdir}")
        try:
            _check_sha(p, want)
        except ManifestError:
            raise SystemExit(f"[rollback] verify: {rel} hash mismatch in {vdir}") from None


def rollback(root: Path, package: str, version: str | None = None) -> None:
    """显式版本或 history 倒数一条有效 from；不删坏版本目录（spec §4.4）。"""
    vroot = Path(root) / "versions"
    ptr = vroot / f"{package}-active.txt"
    old = read_active(ptr)
    if version is None:
        hist_file = vroot / "history.jsonl"
        entries = []
        if hist_file.exists():
            try:
                raw = hist_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                raw = ""
            for line in raw.splitlines():
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue  # 断电可留半行，逐行容错（#1）
                if isinstance(rec, dict) and rec.get("package") == package:
                    entries.append(rec)
        # factory 终态：已是出厂版不得继续回退（#I-2）
        if old == FACTORY:
            raise SystemExit(f"[rollback] {package} 已在 factory，无更旧状态")
        # 回滚链语义：取最后一次 forward 切换为止的 apply 记录构成激活链
        # （更早的 rollback 已被新 apply 重置）；撤销“首次切入当前值”的那次
        # 切换即回其 from（保守：降级环里一步退到 factory，不提供 redo）；链上
        # 找不到当前值（手动改过指针）则对齐链尾。factory 为终态；
        # 连续 rollback 逐跳：8→7→factory（#I-2/#2）
        last_forward = max((i for i, r in enumerate(entries) if not r.get("rollback")), default=-1)
        steps = [r for r in entries[: last_forward + 1] if not r.get("rollback")]
        src = None
        if steps:
            hit = next((r for r in steps if r.get("to") == old), None) or steps[-1]
            src = hit.get("from")
        version = src if isinstance(src, str) and src else FACTORY  # 非字符串归一（#I-3）
    if version != FACTORY and (not VERSION_RE.match(version) or version in (".", "..")):
        raise SystemExit(f"[rollback] bad version: {version!r}")
    if version == old:
        print(f"[rollback] {package} 已处于 {version}，无需回退")
        return
    # spec §4.4：回滚与激活同等的进程锁检查（#I-2）
    if IS_WIN and package == "agent" and old != FACTORY and agent_process_running():
        raise SystemExit(
            f"[rollback] 检测到 eaide-agent 正在运行（激活 {old}）\n"
            "请先退出 EAIDE 再执行 apply/rollback（不自动杀进程）"
        )
    if version != FACTORY:
        vd = vroot / package / version
        if not vd.is_dir():
            raise SystemExit(f"[rollback] version not found: {vd}")
        _check_filehash_cache(vd)
    write_active(ptr, version)
    _append_history(vroot, package, old, version, rollback=True)
    print(f"[rollback] {package} {old} -> {version}")


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
    Path(args.out).write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[gen-manifest] {len(files)} files -> {args.out}")


def _cmd_apply(args):
    if args.adopt and args.reuse_from:
        raise SystemExit("[apply] --adopt 与 --reuse-from 互斥（adopt 整目录即全量源，无复用语义）")
    m = Manifest.load(args.manifest)
    apply_manifest(
        m.raw,
        Path(args.root),
        dry_run=args.dry_run,
        source_dir=args.adopt,
        reuse_from=args.reuse_from,
    )
    return 0


def _cmd_verify(args):
    m = Manifest.load(args.manifest)
    vroot = Path(args.root) / "versions"
    version = args.version or read_active(vroot / f"{m.package}-active.txt")
    if version == FACTORY:
        raise SystemExit("[verify] 当前激活为 factory，无版本目录可验")
    if not VERSION_RE.match(version) or version in (".", ".."):
        raise SystemExit(f"[verify] bad version: {version!r}")
    verify_version(vroot / m.package / version, m.raw)
    print(f"[verify] {m.package} {version}: OK ({len(m.files)} files)")
    return 0


def _cmd_rollback(args):
    rollback(Path(args.root), args.package, version=args.version)
    return 0


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
    a = sub.add_parser("apply", help="下载/adopt 变化文件并原子激活")
    a.add_argument("--manifest", required=True)
    a.add_argument("--root", required=True, help="安装目录（versions 的父目录）")
    a.add_argument("--dry-run", action="store_true", dest="dry_run")
    a.add_argument("--adopt", default=None, help="本地已备好的版本目录（无渠道手动主路径）")
    a.add_argument("--reuse-from", default=None, dest="reuse_from", help="本地复用候选目录")
    a.set_defaults(fn=_cmd_apply)
    v = sub.add_parser("verify", help="全量重算激活/指定版本哈希")
    v.add_argument("--manifest", required=True)
    v.add_argument("--root", required=True)
    v.add_argument("--version", default=None)
    v.set_defaults(fn=_cmd_verify)
    r = sub.add_parser("rollback", help="指针回退（不删坏版本目录）")
    r.add_argument("--root", required=True)
    r.add_argument("--package", required=True, choices=["agent", "web"])
    r.add_argument("--version", default=None)
    r.set_defaults(fn=_cmd_rollback)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main() or 0)
