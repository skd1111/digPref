"""Phase 18 SSE 红线：mode_routed / repair_attempt / auto_decision 三处同步。

断言 graph/stream.py、src-tauri/src/stream/sse_bridge.rs、src/ipc/events.ts
三处都声明了新增的三个事件通道（CLAUDE.md §4）。
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

STREAM_PY = REPO_ROOT / "services" / "agent" / "src" / "agent" / "graph" / "stream.py"
SSE_BRIDGE_RS = REPO_ROOT / "apps" / "desktop" / "src-tauri" / "src" / "stream" / "sse_bridge.rs"
EVENTS_TS = REPO_ROOT / "apps" / "desktop" / "src" / "ipc" / "events.ts"

NEW_CHANNELS = ["mode_routed", "repair_attempt", "auto_decision"]


def _extract_agent_channels(text: str) -> set[str]:
    """抽出文本中所有 agent://xxx 通道名（不含 agent://）。"""
    return set(re.findall(r"agent://([a-z_]+)", text))


def test_stream_py_declares_phase18_channels():
    channels = _extract_agent_channels(STREAM_PY.read_text(encoding="utf-8"))
    for name in NEW_CHANNELS:
        assert name in channels, f"graph/stream.py 缺少 agent://{name}"


def test_sse_bridge_rs_declares_phase18_channels():
    text = SSE_BRIDGE_RS.read_text(encoding="utf-8")
    channels = _extract_agent_channels(text)
    for name in NEW_CHANNELS:
        assert name in channels, f"sse_bridge.rs 缺少 agent://{name}"
    # map_event_to_channel 必须有短名映射分支
    for name in NEW_CHANNELS:
        assert f'"{name}"' in text, f'sse_bridge.rs map_event_to_channel 缺少 "{name}" 分支'


def test_events_ts_declares_phase18_channels():
    channels = _extract_agent_channels(EVENTS_TS.read_text(encoding="utf-8"))
    for name in NEW_CHANNELS:
        assert name in channels, f"ipc/events.ts 缺少 agent://{name}"


def test_three_layers_consistent():
    py = _extract_agent_channels(STREAM_PY.read_text(encoding="utf-8"))
    rs = _extract_agent_channels(SSE_BRIDGE_RS.read_text(encoding="utf-8"))
    ts = _extract_agent_channels(EVENTS_TS.read_text(encoding="utf-8"))
    for name in NEW_CHANNELS:
        assert name in py and name in rs and name in ts
