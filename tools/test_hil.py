#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DC1 板级在环测试（HIL）—— 经 Pico 直接操作按键 / 继电器 / LOGO 灯

接线（Pico 侧）
    KEY1=GP11  RE1=GP12
    KEY2=GP20  RE2=GP21
    KEY3=GP22  RE3=GP26(A0)
    KEY4=GP18  RE4=GP19
    LOGO=GP27(A1)

只用 Pico 固件自带的 GP / GPIN / GPREAD / PWR / IO0 / STATUS 命令，
不改固件、不需要额外烧录。

用法
    python tools/test_hil.py                    # 自动项
    python tools/test_hil.py --interactive      # 含按键交互（按提示按键）
    python tools/test_hil.py --groups 1,2       # 只测继电器和灯
    python tools/test_hil.py --list             # 列用例

注意
  G4（电源时序）会切断 ESP 供电，默认放最后执行；`--no-power` 可跳过。
"""

import argparse
import sys
import time

try:
    import serial
except ImportError:
    sys.exit("需要 pyserial:  pip install pyserial")

KEYS = [("KEY1", 11), ("KEY2", 20), ("KEY3", 22), ("KEY4", 18)]
RES = [("RE1", 12), ("RE2", 21), ("RE3", 26), ("RE4", 19)]
LOGO = ("LOGO", 27)


class Result:
    def __init__(self):
        self.rows = []
        self.t0 = time.time()

    def add(self, group, name, ok, detail=""):
        self.rows.append((group, name, bool(ok), detail))
        print("  [%s] %-44s %s" % ("PASS" if ok else "FAIL", name, detail))

    def summary(self):
        total = len(self.rows)
        fails = [r for r in self.rows if not r[2]]
        print("\n" + "=" * 72)
        print("总计 %d 项，通过 %d，失败 %d，耗时 %.1fs"
              % (total, total - len(fails), len(fails), time.time() - self.t0))
        for g, n, _, d in fails:
            print("  FAIL [%s] %s :: %s" % (g, n, d))
        print("=" * 72)
        return len(fails)


class Pico:
    def __init__(self, port):
        self.port = port
        self.s = serial.Serial()
        self.s.port = port
        self.s.baudrate = 115200
        self.s.timeout = 0.05
        self.s.write_timeout = 2
        self.s.dtr = False
        self.s.rts = False
        self.s.open()
        time.sleep(0.15)
        self.s.reset_input_buffer()

    def cmd(self, line, wait=0.3):
        self.s.reset_input_buffer()
        self.s.write((line + "\n").encode())
        self.s.flush()
        end = time.time() + wait
        buf = b""
        while time.time() < end:
            n = self.s.in_waiting
            buf += self.s.read(n if n else 1)
        return buf.decode("utf-8", "replace").strip()

    def ok(self, line, wait=0.3):
        return self.cmd(line, wait).startswith("OK")

    def read(self, pin):
        r = self.cmd("GPREAD %d" % pin, 0.25)
        try:
            return int(r.split()[-1])
        except Exception:
            return -1

    def write(self, pin, val):
        return self.ok("GP %d %d" % (pin, val))

    def pin_in(self, pin):
        return self.ok("GPIN %d" % pin)

    def status(self):
        return self.cmd("STATUS", 0.5)

    def close(self):
        try:
            self.s.close()
        except Exception:
            pass


def group_res(p, r, g="H1 继电器"):
    print("\n--[ %s ]--" % g)
    all_ok = True
    for name, pin in RES:
        w0 = p.write(pin, 0)
        r0 = p.read(pin)
        w1 = p.write(pin, 1)
        r1 = p.read(pin)
        p.write(pin, 0)
        ok = w0 and w1 and r0 == 0 and r1 == 1
        all_ok = all_ok and ok
        r.add(g, "%s(GP%d) 写0读0/写1读1" % (name, pin), ok,
              "r0=%d r1=%d" % (r0, r1))

    # 顺序扫描：模拟逐个吸合，检查互不干扰
    seq_ok = True
    for i, (name, pin) in enumerate(RES):
        for j, (_, pin2) in enumerate(RES):
            p.write(pin2, 1 if i == j else 0)
        time.sleep(0.05)
        vals = [p.read(x[1]) for x in RES]
        expect = [1 if k == i else 0 for k in range(len(RES))]
        if vals != expect:
            seq_ok = False
            r.add(g, "顺序扫描第 %d 步" % (i + 1), False, "got %s want %s" % (vals, expect))
            break
    if seq_ok:
        r.add(g, "四路顺序扫描互不干扰", True, "逐路独亮")

    for _, pin in RES:
        p.write(pin, 0)
    r.add(g, "全部归零后可读回 0",
          all(p.read(x[1]) == 0 for x in RES),
          str([p.read(x[1]) for x in RES]))
    return all_ok


def group_logo(p, r, g="H2 指示灯"):
    print("\n--[ %s ]--" % g)
    name, pin = LOGO
    if not p.ok("GP %d 1" % pin):
        r.add(g, "GP%d 可设为输出" % pin, False, "GP 命令失败")
        return
    r.add(g, "GP%d 设为输出" % pin, True, "")

    ok1 = p.write(pin, 1) and p.read(pin) == 1
    r.add(g, "写 1 -> 读回 1（灭）", ok1, "read=%d" % p.read(pin))

    ok0 = p.write(pin, 0) and p.read(pin) == 0
    r.add(g, "写 0 -> 读回 0（亮）", ok0, "read=%d" % p.read(pin))

    # 快速翻转 10 次，检查有无卡死
    flip_ok = True
    for i in range(10):
        v = i & 1
        p.write(pin, v)
        if p.read(pin) != v:
            flip_ok = False
            break
    r.add(g, "连续翻转 10 次无异常", flip_ok, "")

    p.write(pin, 1)
    r.add(g, "结束后恢复灭", p.read(pin) == 1, "")


def group_keys(p, r, interactive, g="H3 按键"):
    print("\n--[ %s ]--" % g)
    base = {}
    for name, pin in KEYS:
        p.pin_in(pin)
        vals = [p.read(pin) for _ in range(8)]
        stable = len(set(vals)) == 1
        base[name] = vals[-1]
        r.add(g, "%s(GP%d) 设为输入且读数稳定" % (name, pin), stable,
              "值=%d 采样=%s" % (vals[-1], "".join(str(v) for v in vals)))

    lows = [k for k, v in base.items() if v == 0]
    highs = [k for k, v in base.items() if v == 1]
    r.add(g, "空闲电平记录", True,
          "低=%s 高=%s" % (",".join(lows) or "无", ",".join(highs) or "无"))

    if not interactive:
        r.add(g, "交互按键检测", True, "跳过（--interactive 开启）")
        return

    print("\n  >>> 请依次按下 KEY1~KEY4，每个按 1 秒，间隔 1 秒（共 20 秒窗口）")
    print("  >>> 脚本会自动捕获每一路的电平翻转")
    end = time.time() + 20
    seen = {}
    prev = dict(base)
    while time.time() < end:
        for name, pin in KEYS:
            v = p.read(pin)
            if v >= 0 and v != prev.get(name):
                seen.setdefault(name, []).append(v)
                prev[name] = v
        time.sleep(0.05)

    for name, _ in KEYS:
        got = seen.get(name, [])
        r.add(g, "%s 检测到电平翻转" % name, len(got) > 0,
              "变化序列=%s" % (got or "无"))


def group_power(p, r, g="H4 电源时序"):
    print("\n--[ %s ]--" % g)
    st = p.status()
    ok = "STATUS" in st
    r.add(g, "STATUS 可读", ok, st[:70].replace("\r\n", " | "))

    p.ok("PWR 0", 0.5)
    time.sleep(0.6)
    st = p.cmd("STATUS", 0.5)
    r.add(g, "PWR 0 -> 断电状态", "pwr=0" in st, st.split("|")[0].strip())

    p.ok("IO0 1", 0.4)
    st = p.cmd("STATUS", 0.5)
    r.add(g, "IO0 1 -> 拉低(下载模式)", "io0=1" in st, st.split("|")[0].strip())

    p.ok("PWR 1", 0.4)
    time.sleep(0.6)
    st = p.cmd("STATUS", 0.5)
    r.add(g, "PWR 1 -> 上电(下载模式)", "pwr=1" in st and "io0=1" in st,
          st.split("|")[0].strip())

    # 恢复正常运行：IO0 释放
    p.ok("PWR 0", 0.4)
    time.sleep(0.5)
    p.ok("IO0 0", 0.4)
    p.ok("PWR 1", 0.4)
    time.sleep(0.6)
    st = p.cmd("STATUS", 0.5)
    r.add(g, "恢复运行态(io0=0,pwr=1)", "io0=0" in st and "pwr=1" in st,
          st.split("|")[0].strip())


def main():
    ap = argparse.ArgumentParser(description="DC1 板级 HIL 测试")
    ap.add_argument("--ctrl", default="COM16", help="Pico 控制口")
    ap.add_argument("--groups", default="1,2,3,4")
    ap.add_argument("--interactive", action="store_true", help="含按键交互")
    ap.add_argument("--no-power", action="store_true", help="跳过电源时序测试")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    if args.list:
        print("  1  继电器 RE1~RE4（GP12/21/26/19）")
        print("  2  LOGO 灯（GP27）")
        print("  3  按键 KEY1~KEY4（GP11/20/22/18）")
        print("  4  电源/IO0 时序（会切断 ESP）")
        return 0

    p = Pico(args.ctrl)
    r = Result()
    try:
        info = p.cmd("INFO", 0.8)
        print("Pico: %s" % info)
        if "pico-esp-flasher" not in info.lower():
            print("控制口未应答，检查 --ctrl")
            return 2

        sel = [x.strip() for x in args.groups.split(",") if x.strip()]
        if "1" in sel:
            group_res(p, r)
        if "2" in sel:
            group_logo(p, r)
        if "3" in sel:
            group_keys(p, r, args.interactive)
        if "4" in sel and not args.no_power:
            group_power(p, r)
        return r.summary()
    finally:
        p.close()


if __name__ == "__main__":
    sys.exit(0 if main() == 0 else 1)
