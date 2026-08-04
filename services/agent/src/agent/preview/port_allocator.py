"""Phase 15 V0 · 端口分配器（位图 O(1)）。

设计文档 §5.3：端口范围 5173-5200（128 槽位），位图 bytearray(128)。
全占时扩展范围到 5300（设计 §9 风险缓解）。重启 Agent 时位图清空。
"""

from __future__ import annotations

import socket

from agent.preview.models import PORT_RANGE_EXTENDED_END, PORT_RANGE_START


class PortAllocator:
    """端口位图分配器。

    allocate() 找第一个空闲位；release() 回收。
    内部先查位图，再对候选端口做 socket bind 探测（外部进程占用检测）。
    """

    def __init__(self, start: int = PORT_RANGE_START, end: int = PORT_RANGE_EXTENDED_END) -> None:
        if end < start:
            raise ValueError("end must be >= start")
        self._start = start
        self._size = end - start + 1
        self._bitmap = bytearray(self._size)

    @property
    def start(self) -> int:
        return self._start

    @property
    def size(self) -> int:
        return self._size

    def allocate(self, preferred: int | None = None) -> int | None:
        """分配一个端口。preferred 指定时优先（已被占则跳过）。

        返回端口号；范围全占返回 None。
        """
        if preferred is not None and self._start <= preferred <= self._start + self._size - 1:
            idx = preferred - self._start
            if self._bitmap[idx] == 0 and _port_free(preferred):
                self._bitmap[idx] = 1
                return preferred
        for i in range(self._size):
            if self._bitmap[i] == 0:
                port = self._start + i
                if _port_free(port):
                    self._bitmap[i] = 1
                    return port
        return None

    def release(self, port: int) -> bool:
        """释放端口。范围外 / 未占用返回 False。"""
        if not (self._start <= port <= self._start + self._size - 1):
            return False
        idx = port - self._start
        if self._bitmap[idx] == 0:
            return False
        self._bitmap[idx] = 0
        return True

    def is_allocated(self, port: int) -> bool:
        if not (self._start <= port <= self._start + self._size - 1):
            return False
        return self._bitmap[port - self._start] == 1

    def allocated_ports(self) -> list[int]:
        return [self._start + i for i in range(self._size) if self._bitmap[i]]

    def reset(self) -> None:
        """清空位图（Agent 重启 / 测试隔离用）。"""
        self._bitmap = bytearray(self._size)

    def used_slots(self) -> int:
        return sum(1 for b in self._bitmap if b)


def _port_free(port: int) -> bool:
    """探测端口是否被外部进程占用（TCP bind 探测）。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False


_default_allocator: PortAllocator | None = None


def get_default_allocator() -> PortAllocator:
    global _default_allocator
    if _default_allocator is None:
        _default_allocator = PortAllocator()
    return _default_allocator


def reset_default_allocator() -> None:
    """测试隔离：重置单例。"""
    global _default_allocator
    _default_allocator = None
