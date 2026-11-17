"""store.py 的逻辑单测（在 PC 上用 CPython 跑，不需要硬件）

覆盖：定时"单次 vs 每周"、倒计时到期、电量累计与跨天清零。
运行：python tests/test_store.py
"""

import json
import os
import sys
import tempfile
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "firmware", "micropython"))

import store  # noqa: E402


class FakeTime:
    """可拨动的假时间：localtime 返回固定时刻，ticks 单调递增。"""

    def __init__(self):
        self.now = 0            # ticks 毫秒
        self.lt = (2026, 11, 18, 7, 30, 0, 2, 322, 0)   # 周三 07:30

    def ticks_ms(self):
        return self.now

    def ticks_diff(self, a, b):
        return a - b

    def localtime(self):
        return self.lt

    def set_hm(self, hh, mm, dow=None, doy=None):
        y, mo, d, _, _, s, _, dy, _ = self.lt
        self.lt = (y, mo, d, hh, mm, s, dow if dow is not None else self.lt[6],
                   doy if doy is not None else dy, 0)


def with_tmp(func):
    def wrapper():
        d = tempfile.mkdtemp()
        old = store.time
        fake = FakeTime()
        store.time = fake
        try:
            func(d)
        finally:
            store.time = old
        print("PASS", func.__name__)
    return wrapper


@with_tmp
def test_weekly(tmp):
    s = store.Sched(os.path.join(tmp, "s.json"))
    # 周三=bit2 → mask 4
    s.add({"ch": "1", "on": 1, "hh": 7, "mm": 30, "days": 4})
    fired = s.due()
    assert fired == [("1", True, 1)], fired
    # 同一分钟只触发一次
    assert s.due() == []
    # 规则仍在
    assert len(s.all()) == 1
    # 周四同一时刻不触发
    store.time.set_hm(7, 30, dow=3)
    store.time.now = 70000
    s._last_key = ""
    assert s.due() == []


@with_tmp
def test_once(tmp):
    s = store.Sched(os.path.join(tmp, "s.json"))
    s.add({"ch": "total", "on": 0, "hh": 7, "mm": 30, "days": 0})
    fired = s.due()
    assert fired == [("total", False, 1)], fired
    # 单次：触发后自动删除
    assert s.all() == []
    # 持久化也应为空
    assert json.load(open(os.path.join(tmp, "s.json")))["rules"] == []


@with_tmp
def test_countdown(tmp):
    cd = store.Countdown(os.path.join(tmp, "c.json"))
    it = cd.add("2", False, 2)                 # 2 秒后关插孔2
    assert it["left"] == 2
    assert cd.tick(1000) == []                 # 还剩 1s
    fired = cd.tick(1200)                      # 到期
    assert fired == [("2", False, it["id"])], fired
    assert cd.all() == []


@with_tmp
def test_energy(tmp):
    e = store.Energy(os.path.join(tmp, "e.json"))
    # 1000W 跑 1 小时 = 1 kWh（分 60 次 1 分钟喂）
    for _ in range(60):
        e.add(1000.0, 60000)
    assert abs(e.state()["total_kwh"] - 1.0) < 1e-6, e.state()
    # 跨天：doy 变化后 today 归零，total 保留
    store.time.lt = (2026, 11, 19, 0, 1, 0, 3, 323, 0)
    e.add(1000.0, 60000)
    st = e.state()
    assert st["today_kwh"] < 0.02, st
    assert st["total_kwh"] > 0.99, st


if __name__ == "__main__":
    test_weekly()
    test_once()
    test_countdown()
    test_energy()
    print("all store tests passed")
