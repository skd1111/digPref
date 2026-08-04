"""Whitelist-based shell command safety check.

Design: blacklists based on substring matching are trivially bypassed
(rm -rf / vs rm -rf /*, shutdown vs systemctl poweroff, etc.).
Instead, we only allow an explicit set of safe commands and patterns.
"""
from __future__ import annotations

import re

from mcp_server_ssh.config import Settings


class UnsafeCommandError(Exception):
    pass


# ---- Whitelist of safe command prefixes ----
# Only commands starting with one of these prefixes are permitted.
# Each entry is a tuple of (prefix, description).
_SAFE_COMMANDS: tuple[tuple[str, str], ...] = (
    # File inspection
    ("ls ", "list directory"),
    ("dir ", "list directory (Windows)"),
    ("cat ", "print file content"),
    ("head ", "print first lines"),
    ("tail ", "print last lines"),
    ("less ", "pager"),
    ("more ", "pager"),
    ("find ", "search files"),
    ("locate ", "search files"),
    ("which ", "locate command"),
    ("whereis ", "locate command"),
    ("file ", "detect file type"),
    ("stat ", "file metadata"),
    ("du ", "disk usage"),
    ("df ", "disk free"),
    ("wc ", "word count"),
    ("md5sum ", "checksum"),
    ("sha256sum ", "checksum"),
    ("sha1sum ", "checksum"),
    ("grep ", "search text"),
    ("egrep ", "search text"),
    ("fgrep ", "search text"),
    ("awk ", "text processing"),
    ("sed ", "stream editor"),
    ("sort ", "sort lines"),
    ("uniq ", "unique lines"),
    ("cut ", "cut fields"),
    ("tr ", "translate chars"),
    ("diff ", "diff files"),
    ("cmp ", "compare files"),
    ("xxd ", "hex dump"),
    ("od ", "octal dump"),
    ("strings ", "print strings"),
    # Process inspection
    ("ps ", "process status"),
    ("top ", "process monitor (read-only)"),
    ("htop ", "process monitor (read-only)"),
    ("pgrep ", "find process"),
    ("pidof ", "find process PID"),
    ("lsof ", "list open files"),
    ("fuser ", "identify processes using files"),
    ("uptime ", "system uptime"),
    ("free ", "memory usage"),
    ("vmstat ", "virtual memory stats"),
    ("iostat ", "I/O stats"),
    ("mpstat ", "CPU stats"),
    ("netstat ", "network stats"),
    ("ss ", "socket stats"),
    ("ip addr ", "IP addresses"),
    ("ip link ", "network links"),
    ("ip route ", "routing table"),
    ("ifconfig ", "network interfaces"),
    ("route ", "routing table"),
    ("ping ", "network reachability"),
    ("traceroute ", "trace route"),
    ("nslookup ", "DNS lookup"),
    ("dig ", "DNS lookup"),
    ("host ", "DNS lookup"),
    ("whois ", "WHOIS lookup"),
    ("curl ", "HTTP client"),
    ("wget ", "HTTP downloader"),
    # System info
    ("uname ", "system info"),
    ("hostname ", "hostname"),
    ("date ", "current date"),
    ("env ", "environment variables"),
    ("printenv ", "print environment"),
    ("echo ", "print text"),
    ("printf ", "formatted print"),
    ("id ", "user identity"),
    ("whoami ", "current user"),
    ("who ", "logged-in users"),
    ("w ", "logged-in users"),
    ("last ", "login history"),
    ("history ", "shell history"),
    ("dmesg ", "kernel log"),
    ("journalctl ", "systemd journal"),
    ("systemctl status ", "service status"),
    ("systemctl list-", "list units"),
    ("systemctl show ", "show unit properties"),
    ("systemctl is-", "check unit state"),
    ("service ", "service status"),
    # Package managers (read-only queries)
    ("dpkg -l", "list packages"),
    ("dpkg -s", "package status"),
    ("dpkg-query ", "query packages"),
    ("rpm -q", "query RPM"),
    ("rpm -V", "verify RPM"),
    ("apt list ", "list apt packages"),
    ("apt-cache ", "apt cache query"),
    ("yum list ", "list yum packages"),
    ("yum info ", "yum package info"),
    ("pip list ", "list pip packages"),
    ("pip show ", "pip package info"),
    ("npm list ", "list npm packages"),
    ("npm view ", "npm package info"),
    # Git (read-only)
    ("git status", "git status"),
    ("git log", "git log"),
    ("git diff", "git diff"),
    ("git show", "git show"),
    ("git branch", "git branch list"),
    ("git remote", "git remote list"),
    ("git tag", "git tag list"),
    ("git config", "git config read"),
    ("git rev-", "git revision"),
    ("git blame", "git blame"),
    # Docker (read-only)
    ("docker ps", "docker container list"),
    ("docker images", "docker image list"),
    ("docker inspect", "docker inspect"),
    ("docker logs", "docker logs"),
    ("docker stats", "docker stats"),
    ("docker info", "docker info"),
    ("docker version", "docker version"),
    ("docker network ls", "docker network list"),
    ("docker volume ls", "docker volume list"),
    ("docker compose ps", "docker compose ps"),
    ("docker compose logs", "docker compose logs"),
    ("docker-compose ps", "docker compose ps"),
    ("docker-compose logs", "docker compose logs"),
    # Kubernetes (read-only)
    ("kubectl get ", "kubectl get"),
    ("kubectl describe ", "kubectl describe"),
    ("kubectl logs ", "kubectl logs"),
    ("kubectl top ", "kubectl top"),
    ("kubectl explain ", "kubectl explain"),
    ("kubectl api-resources", "kubectl API resources"),
    ("kubectl api-versions", "kubectl API versions"),
    ("kubectl cluster-info", "kubectl cluster info"),
    ("kubectl config view", "kubectl config view"),
    ("helm list ", "helm list"),
    ("helm status ", "helm status"),
    ("helm history ", "helm history"),
    ("helm get ", "helm get"),
    # Java / JVM (read-only)
    ("jps ", "JVM process status"),
    ("jstat ", "JVM statistics"),
    ("jstack ", "JVM thread dump"),
    ("jmap -histo", "JVM heap histogram"),
    ("jinfo ", "JVM config info"),
    ("jcmd ", "JVM diagnostic command"),
    # File transfer (explicitly allowed because HITL-gated)
    ("scp ", "secure copy"),
    ("rsync ", "remote sync"),
    ("sftp ", "SFTP client"),
)


def assert_safe(cmd: str) -> None:
    """Check that `cmd` starts with one of the whitelisted safe prefixes.

    Also rejects commands containing shell metacharacters used for chaining
    (;, &&, ||, |, `, $()), which could be used to append dangerous commands
    after a whitelisted prefix.
    """
    stripped = cmd.strip()

    if not stripped:
        raise UnsafeCommandError("empty command")

    # Reject shell chaining / command injection metacharacters
    _assert_no_chaining(stripped)

    # Check against whitelist
    for prefix, _desc in _SAFE_COMMANDS:
        if stripped.startswith(prefix):
            return

    # Allow custom commands if explicitly configured (e.g. deployment scripts)
    extra_allowed = Settings().extra_allowed_commands
    for prefix in extra_allowed:
        if stripped.startswith(prefix):
            return

    raise UnsafeCommandError(
        f"command not in whitelist: {stripped[:120]!r}. "
        f"Only read-only inspection commands are permitted by default."
    )


# Shell chaining / injection patterns
_CHAINING_RE = re.compile(r'[;&|`$]|\$\(')


def _assert_no_chaining(cmd: str) -> None:
    """Reject commands that contain shell chaining metacharacters."""
    # Allow && and || only inside single-quoted strings (rare, but safe)
    # Simple approach: reject if any chaining character exists outside quotes
    in_single = False
    in_double = False
    i = 0
    while i < len(cmd):
        ch = cmd[i]
        nxt = cmd[i + 1] if i + 1 < len(cmd) else ""

        if in_single:
            if ch == "'":
                in_single = False
            i += 1
            continue
        if in_double:
            if ch == '"':
                in_double = False
            elif ch == '\\' and nxt:
                i += 2  # skip escaped char
                continue
            i += 1
            continue

        if ch == "'":
            in_single = True
            i += 1
            continue
        if ch == '"':
            in_double = True
            i += 1
            continue

        # Check for chaining operators outside quotes
        if ch == ';':
            raise UnsafeCommandError("shell chaining ';' not allowed")
        if ch == '|':
            raise UnsafeCommandError("shell pipe '|' not allowed")
        if ch == '&' and nxt == '&':
            raise UnsafeCommandError("shell chaining '&&' not allowed")
        if ch == '|' and nxt == '|':
            raise UnsafeCommandError("shell chaining '||' not allowed")
        if ch == '`':
            raise UnsafeCommandError("command substitution backtick not allowed")
        if ch == '$' and nxt == '(':
            raise UnsafeCommandError("command substitution '$()' not allowed")

        i += 1