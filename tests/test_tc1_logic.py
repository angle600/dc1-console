#!/usr/bin/env python3
"""TC1 固件逻辑单测（主机侧，不需要硬件）

做法：用假的 machine / time 模块顶掉 MicroPython 的硬件依赖，
直接测 board.py（继电器极性、按键去抖）、bl0937.py（脉冲→功率换算）
和 store.py（定时/倒计时/电量，与 DC1 同源）。

跑法：python tests/test_tc1_logic.py
"""

import os
import sys
import tempfile
import time as _time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
FW = os.path.join(HERE, "..", "firmware", "tc1")


# ---------------------------------------------------------------- 假硬件
class FakePin:
    IN = 0
    OUT = 1
    PULL_UP = 2
    IRQ_FALLING = 4

    def __init__(self, gpio, mode=None, pull=None, value=None):
        self.gpio = gpio
        self.mode = mode
        self._v = 0 if value is None else int(value)
        self.handlers = []

    def value(self, v=None):
        if v is None:
            return self._v
        self._v = int(v)

    def irq(self, trigger=None, handler=None):
        if handler:
            self.handlers.append(handler)

    def fire(self):
        for h in list(self.handlers):
            h(self)


class FakeADC:
    def __init__(self, pin):
        self.pin = pin
        self.v = 1023

    def read(self):
        return self.v


_machine = types.ModuleType("machine")
_machine.Pin = FakePin
_machine.ADC = FakeADC
sys.modules["machine"] = _machine

# 可控时钟：MicroPython 的 ticks_ms / ticks_diff 在 CPython 里没有
CLOCK = [1000]
_time.ticks_ms = lambda: CLOCK[0]
_time.ticks_diff = lambda a, b: a - b
_time.sleep_ms = lambda ms: None
_time.sleep = lambda s: None

sys.path.insert(0, FW)
import board        # noqa: E402
import bl0937       # noqa: E402
import store        # noqa: E402


PASS = []
FAIL = []


def ok(name):
    PASS.append(name)
    print("PASS  %s" % name)


def bad(name, err):
    FAIL.append((name, err))
    print("FAIL  %s -> %s" % (name, err))


def case(fn):
    try:
        fn()
    except AssertionError as e:
        bad(fn.__name__, e or "assert")
    except Exception as e:
        bad(fn.__name__, repr(e))
    return fn


# ---------------------------------------------------------------- board
@case
def test_relay_active_high():
    r = board.Relays(active_low=False)
    assert len(r) == 6, len(r)
    # 构造即全断（高有效 -> 0）
    assert [p.value() for p in r.pins] == [0] * 6, [p.value() for p in r.pins]
    r.write(0, True)
    assert r.pins[0].value() == 1
    assert r.count_on() == 1
    r.all(True)
    assert r.count_on() == 6
    r.all(False)
    assert r.count_on() == 0
    ok("relay: 高有效极性 / 6 路全开全关")


@case
def test_relay_active_low():
    r = board.Relays(active_low=True)
    assert [p.value() for p in r.pins] == [1] * 6      # 低有效：断电 = 1
    r.write(5, True)
    assert r.pins[5].value() == 0
    ok("relay: 低有效极性")


@case
def test_key_short_press():
    k = board.Key()
    k.adc.v = 1023
    assert k.poll() is None
    # 按下：连续 3 次低值 → 认按下
    k.adc.v = 100
    for i in range(3):
        CLOCK[0] += 20
        ev = k.poll()
    assert k.down is True, "去抖未生效"
    assert ev is None, ev
    # 按住 1 s 后松开（< 3 s）→ short
    CLOCK[0] += 1000
    assert k.poll() is None
    k.adc.v = 1023
    ev = None
    for i in range(3):
        CLOCK[0] += 20
        ev = k.poll()
    assert ev == "short", ev
    ok("key: 短按 → short（去抖 + 时长判定）")


@case
def test_key_long_press():
    k = board.Key()
    k.adc.v = 1023
    k.poll()
    k.adc.v = 100
    for i in range(3):
        CLOCK[0] += 20
        k.poll()
    CLOCK[0] += board.LONG_PRESS_MS + 10
    assert k.poll() == "long", "长按未触发"
    CLOCK[0] += 100
    assert k.poll() is None, "长按重复触发"
    ok("key: 长按 → long，且只报一次")


# ---------------------------------------------------------------- bl0937
@case
def test_bl0937_power():
    CLOCK[0] = 5000
    m = bl0937.BL0937(k_imp=1600.0, window_ms=1000, alpha=1.0)
    assert m.pin is not None
    for _ in range(16):                 # 1 秒 16 个脉冲
        m.pin.fire()
    CLOCK[0] += 1000
    assert m.poll() is True
    # f = 16 Hz → P = 1000*16/1600 = 10 W
    assert abs(m.freq - 16.0) < 1e-6, m.freq
    assert abs(m.power - 10.0) < 1e-6, m.power
    st = m.state()
    assert st["power"] == 10.0 and st["pulses"] == 16
    assert st["voltage"] == 0 and st["current"] == 0
    ok("bl0937: 16 脉冲/秒 @K=1600 → 10 W")


@case
def test_bl0937_cal():
    CLOCK[0] = 9000
    m = bl0937.BL0937(k_imp=1000.0, window_ms=1000, alpha=1.0)
    for _ in range(50):                 # 50 Hz
        m.pin.fire()
    CLOCK[0] += 1000
    m.poll()
    # 外部功率计读到 100 W：K = 1000*50/100 = 500 imp/kWh
    k = m.cal_by_ref(100)
    assert abs(k - 500.0) < 1e-6, k
    assert abs(m.power - 100.0) < 1e-6
    try:
        m.cal_by_ref(1)
    except ValueError:
        pass
    else:
        raise AssertionError("低功率应拒绝标定")
    ok("bl0937: 单点标定 K=1000·f/P，低功率拒绝")


@case
def test_bl0937_idle():
    CLOCK[0] = 12000
    m = bl0937.BL0937(k_imp=1600.0, window_ms=1000, alpha=1.0)
    CLOCK[0] += 1000
    assert m.poll() is True
    assert m.power == 0.0, m.power
    ok("bl0937: 无脉冲 → 0 W")


# ---------------------------------------------------------------- store
@case
def test_store_sched_and_countdown():
    d = tempfile.mkdtemp()
    cwd = os.getcwd()
    os.chdir(d)
    try:
        s = store.Sched()
        r = s.add({"ch": "6", "on": 1, "hh": 8, "mm": 0, "days": 0})
        assert r["ch"] == "6" and r["days"] == 0
        assert len(s.all()) == 1
        assert s.rm(r["id"]) is True
        assert len(s.all()) == 0

        cd = store.Countdown()
        it = cd.add("total", 1, 60)
        hits = cd.tick(30000)
        assert hits == [] and abs(cd.all()[0]["left"] - 30) < 1e-6
        hits = cd.tick(31000)
        assert len(hits) == 1 and hits[0][0] == "total" and hits[0][1] is True, hits
        assert cd.all() == []
    finally:
        os.chdir(cwd)
    ok("store: 6 号插孔定时 + total 倒计时到期")


@case
def test_store_energy():
    d = tempfile.mkdtemp()
    cwd = os.getcwd()
    os.chdir(d)
    try:
        e = store.Energy()
        e.add(1000.0, 3600000)          # 1000 W 跑 1 小时 = 1 kWh
        st = e.state()
        assert abs(st["total_kwh"] - 1.0) < 0.01, st
        e.reset()
        assert e.state()["total_kwh"] == 0.0
    finally:
        os.chdir(cwd)
    ok("store: 电量积分 1000W×1h = 1 kWh")


print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
if FAIL:
    for n, e in FAIL:
        print("  - %s: %s" % (n, e))
    sys.exit(1)
