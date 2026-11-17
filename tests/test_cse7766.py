"""CSE7766 帧解析单测（PC 上跑，不需硬件）

帧结构按手册 Rev1.2 表 3-2：
  [0] 0x55 已校准 / 0xAA 未校准, [1] 0x5A,
  [2..4] Uk, [5..7] Ut, [8..10] Ik, [11..13] It,
  [14..16] Pk, [17..19] Pt, [20] Adj, [21..22] CF, [23] checksum
换算：U = Uk/(Ut*V2R)、I = Ik/(It*V1R)、P = Pk/(Pt*V1R*V2R)

运行：python tests/test_cse7766.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "firmware", "micropython"))

from cse7766 import CSE7766  # noqa: E402


class FakeUART:
    def __init__(self, data=b""):
        self.data = bytearray(data)

    def any(self):
        return len(self.data)

    def read(self, n=None):
        if n is None:
            n = len(self.data)
        out = self.data[:n]
        del self.data[:n]
        return bytes(out)


def u24(v):
    return bytes([(v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF])


def make_frame(uk, ut, ik, it, pk, pt, cf=0, header=0x55, adj=0):
    f = bytearray(24)
    f[0] = header
    f[1] = 0x5A
    f[2:5] = u24(uk)
    f[5:8] = u24(ut)
    f[8:11] = u24(ik)
    f[11:14] = u24(it)
    f[14:17] = u24(pk)
    f[17:20] = u24(pt)
    f[20] = adj
    f[21] = (cf >> 8) & 0xFF
    f[22] = cf & 0xFF
    f[23] = sum(f[2:23]) & 0xFF
    return bytes(f)


def test_decode():
    # U = 2_200_000 / 10000 = 220.0 V
    # I = 3600 / 20000      = 0.18 A
    # P = 800_000 / 20000   = 40.0 W
    frame = make_frame(2_200_000, 10000, 3600, 20000, 800_000, 20000, cf=123)
    m = CSE7766(FakeUART(frame))
    assert m.poll() is True
    st = m.state()
    assert abs(st["voltage"] - 220.0) < 0.05, st
    assert abs(st["current"] - 0.18) < 0.001, st
    assert abs(st["power"] - 40.0) < 0.05, st
    assert st["energy_pulses"] == 123
    assert st["calibrated"] == 1
    print("PASS decode")


def test_no_load():
    # 无负载：周期可读但功率为 0（Pk=0），电流 0
    frame = make_frame(2_200_000, 10000, 0, 0, 0, 0)
    m = CSE7766(FakeUART(frame))
    m.poll()
    st = m.state()
    assert st["voltage"] == 220.0, st
    assert st["current"] == 0.0 and st["power"] == 0.0, st
    print("PASS no_load")


def test_gain_applied():
    frame = make_frame(2_200_000, 10000, 3600, 20000, 800_000, 20000)
    m = CSE7766(FakeUART(frame), p_gain=1.5)
    m.poll()
    assert abs(m.state()["power"] - 60.0) < 0.1, m.state()
    print("PASS gain_applied")


def test_resync_and_checksum():
    good = make_frame(2_200_000, 10000, 3600, 20000, 800_000, 20000)
    bad = bytearray(good)
    bad[23] ^= 0xFF                      # 校验错
    stream = b"\x00\x13garbage" + bytes(bad) + good
    m = CSE7766(FakeUART(stream))
    assert m.poll() is True
    assert abs(m.state()["power"] - 40.0) < 0.05, m.state()
    print("PASS resync_and_checksum")


def test_hardware_constants():
    # V2R=2（2MΩ 分压）、V1R=1.5（1.5mΩ 采样）→ 读数按比例缩小
    frame = make_frame(2_200_000, 10000, 3600, 20000, 800_000, 20000)
    m = CSE7766(FakeUART(frame), v1r=1.5, v2r=2.0)
    m.poll()
    st = m.state()
    assert abs(st["voltage"] - 110.0) < 0.05, st
    assert abs(st["current"] - 0.12) < 0.001, st
    assert abs(st["power"] - 13.333) < 0.05, st
    print("PASS hardware_constants")


def test_uncalibrated_header():
    frame = make_frame(2_200_000, 10000, 3600, 20000, 800_000, 20000, header=0xAA)
    m = CSE7766(FakeUART(frame))
    m.poll()
    assert m.state()["calibrated"] == 0, m.state()
    print("PASS uncalibrated_header")


if __name__ == "__main__":
    test_decode()
    test_no_load()
    test_gain_applied()
    test_resync_and_checksum()
    test_hardware_constants()
    test_uncalibrated_header()
    print("all cse7766 tests passed")
