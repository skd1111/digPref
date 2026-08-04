"""L1 精确缓存测试（Phase 2C v2）。

时间用可注入的 fake clock 控制 TTL 过期，不 sleep，测试快且确定。
"""
from __future__ import annotations

from agent.llm.cache_l1 import L1Cache, make_key


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def test_make_key_normalizes_whitespace():
    k1 = make_key("m", "hello   world")
    k2 = make_key("m", "  hello world  ")
    assert k1 == k2
    # 不同 model → 不同 key
    assert make_key("m1", "x") != make_key("m2", "x")


def test_put_get_hit():
    c = L1Cache()
    k = make_key("mock", "查询订单")
    assert c.get(k) is None  # miss
    c.put(k, "结果")
    assert c.get(k) == "结果"  # hit
    assert c.hits == 1
    assert c.misses == 1


def test_ttl_expiry():
    clock = _Clock()
    c = L1Cache(ttl_sec=300, now_fn=clock)
    k = make_key("mock", "x")
    c.put(k, "v")
    clock.advance(299)
    assert c.get(k) == "v"       # 未过期
    clock.advance(2)             # 总 301 > 300
    assert c.get(k) is None      # 过期 → miss + 淘汰
    assert c.size == 0


def test_lru_eviction():
    c = L1Cache(max_size=2)
    c.put("a", "1")
    c.put("b", "2")
    c.get("a")            # a 变 MRU，b 变 LRU
    c.put("c", "3")       # 超容量 → 淘汰 LRU=b
    assert c.get("a") == "1"
    assert c.get("c") == "3"
    assert c.get("b") is None


def test_hit_rate():
    c = L1Cache()
    c.put("k", "v")
    c.get("k")     # hit
    c.get("k")     # hit
    c.get("miss")  # miss
    assert c.hit_rate == 2 / 3


def test_clear():
    c = L1Cache()
    c.put("k", "v")
    c.clear()
    assert c.size == 0
    assert c.get("k") is None
