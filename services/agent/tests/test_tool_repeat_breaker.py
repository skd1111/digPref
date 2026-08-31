"""根治 BUGFIX #165 的回归测试：重复调用熔断 + shell ok 语义。

## 背景

一次 PPT 任务里模型对着同一个 python 脚本换了 22 种写法（换解释器 / 写 .bat
包装 / cmd /c / ``^`` 转义空格），把 24 轮编排预算烧光，一页 PPT 都没做。

两层缺陷叠加：

1. ``builtin/shell.py`` 只要进程**成功启动**就返 ``ok=True``，退出码埋在
   ``content`` 里 —— 于是「成功地启动了一个失败的命令」也算成功。
2. ``tools/loop.py`` 的停滞熔断判据是 ``any(r.get("ok") for r in executed)``，
   被缺陷 1 一直喂 ``ok=True``，计数器一次都没涨过。

缺陷 1 已在 ``test_builtin_v2.py`` 覆盖。本文件覆盖新增的第二道闸门 ——
**重复调用熔断**：只看「是不是在原地打转」，不依赖任何工具的 ``ok`` 语义。
即使将来某个工具的 ``ok`` 再出问题，重复调用也拦得住。
"""

from __future__ import annotations

from agent.tools.loop import (
    _REPEAT_CALL_LIMIT,
    _call_fingerprint,
    _count_trailing_repeats,
    _repeat_msg,
)

# ---- 调用指纹 ---------------------------------------------------------------


def test_fingerprint_identical_calls_match() -> None:
    a = {"name": "shell", "args": {"command": "python x.py", "timeout_sec": 30}}
    b = {"name": "shell", "args": {"command": "python x.py", "timeout_sec": 30}}
    assert _call_fingerprint(a) == _call_fingerprint(b)


def test_fingerprint_ignores_arg_order() -> None:
    """dict 顺序不同但内容相同 → 同一指纹（否则模型换个键序就绕过熔断）。"""
    a = {"name": "shell", "args": {"command": "x", "timeout_sec": 30}}
    b = {"name": "shell", "args": {"timeout_sec": 30, "command": "x"}}
    assert _call_fingerprint(a) == _call_fingerprint(b)


def test_fingerprint_differs_on_args() -> None:
    """真的换了命令 → 不同指纹 → 不该被熔断（有进展的任务不受影响）。"""
    a = {"name": "shell", "args": {"command": "python x.py"}}
    b = {"name": "shell", "args": {"command": "python3 x.py"}}
    assert _call_fingerprint(a) != _call_fingerprint(b)


def test_fingerprint_differs_on_tool_name() -> None:
    a = {"name": "shell", "args": {"command": "x"}}
    b = {"name": "read_file", "args": {"command": "x"}}
    assert _call_fingerprint(a) != _call_fingerprint(b)


def test_fingerprint_ignores_call_id() -> None:
    """call_id 每次不同，不能进指纹 —— 否则永远判不出重复。"""
    a = {"name": "shell", "command": "x", "call_id": "c-1"}
    b = {"name": "shell", "command": "x", "call_id": "c-2"}
    assert _call_fingerprint(a) == _call_fingerprint(b)


def test_fingerprint_accepts_flat_call_shape() -> None:
    """有的调用把参数平铺在 call 顶层（无 args 键）—— 也要能算指纹。"""
    flat = {"name": "shell", "command": "x", "timeout_sec": 5}
    assert "shell" in _call_fingerprint(flat)


def test_fingerprint_survives_unserializable_args() -> None:
    """不可序列化参数退化成 repr —— 宁可指纹保守，也不能抛异常打断工具循环。"""

    class Weird:
        pass

    call = {"name": "shell", "args": {"obj": Weird()}}
    assert _call_fingerprint(call)  # 不抛异常即通过


def test_fingerprint_uses_tool_key_alias() -> None:
    """decompose 下发的调用用 `tool` 键而非 `name`。"""
    assert _call_fingerprint({"tool": "shell", "args": {}}).startswith("shell|")


# ---- 末尾连续重复计数 -------------------------------------------------------


def test_trailing_repeats_empty() -> None:
    assert _count_trailing_repeats([]) == 0


def test_trailing_repeats_counts_only_tail() -> None:
    """只数末尾连续段 —— 早先出现过的相同调用不该累加。"""
    assert _count_trailing_repeats(["a", "b", "a", "a", "a"]) == 3


def test_trailing_repeats_resets_on_change() -> None:
    assert _count_trailing_repeats(["a", "a", "a", "b"]) == 1


def test_trailing_repeats_single() -> None:
    assert _count_trailing_repeats(["a"]) == 1


# ---- 阈值与文案 -------------------------------------------------------------


def test_repeat_limit_is_three() -> None:
    """试三次还不换路子就是死循环（与 _STAGNANT_LIMIT 对齐）。"""
    assert _REPEAT_CALL_LIMIT == 3


def test_two_repeats_do_not_trip() -> None:
    """第二次重试是正常行为（瞬时失败重试），不该被掐断。"""
    assert _count_trailing_repeats(["a", "a"]) < _REPEAT_CALL_LIMIT


def test_three_repeats_trip() -> None:
    assert _count_trailing_repeats(["a", "a", "a"]) >= _REPEAT_CALL_LIMIT


def test_repeat_msg_is_actionable() -> None:
    """熔断文案必须给出路，不能只说「我停了」——否则用户和模型都不知道下一步。"""
    msg = _repeat_msg(3, {"name": "shell", "args": {"command": "python x.py"}})
    assert "shell" in msg
    assert "error" in msg, "应提示先读 error 字段"
    assert "builtin_list_dir" in msg, "应给出不依赖 shell 引号的替代工具"


def test_repeat_msg_handles_missing_name() -> None:
    assert _repeat_msg(3, {}).strip()


# ---- 端到端：复现那 22 轮空转 ------------------------------------------------


def test_reproduces_the_22_round_burn() -> None:
    """事故场景：同一条失败命令被反复调用。

    旧行为：shell 每次返 ok=True → 停滞计数器不涨 → 一路烧到 24 轮预算耗尽。
    新行为：指纹连续相同达 3 次即掐断，与 ok 无关。
    """
    same_call = {
        "name": "shell",
        "args": {"command": "python attribution_guard.py", "timeout_sec": 30},
    }
    fingerprints: list[str] = []
    tripped_at = None
    for round_no in range(1, 25):
        fingerprints.append(_call_fingerprint(same_call))
        if _count_trailing_repeats(fingerprints) >= _REPEAT_CALL_LIMIT:
            tripped_at = round_no
            break
    assert tripped_at == 3, f"应在第 3 轮掐断，实际 {tripped_at}"


def test_varied_commands_are_not_penalised() -> None:
    """模型真的在换方法（每次不同命令）→ 不该被熔断，只受预算约束。

    这条守住「别把熔断做成误杀」—— 事故里模型确实在换写法，但换的是**同一个
    目标的等价写法**；真正有进展的任务每次调用参数都不同，指纹自然不连续。
    """
    fingerprints = [
        _call_fingerprint({"name": "shell", "args": {"command": c}})
        for c in ("dir a", "dir b", "dir c", "dir d", "dir e")
    ]
    assert _count_trailing_repeats(fingerprints) == 1
