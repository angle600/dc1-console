#!/usr/bin/env python3
"""抢 REPL 窗口读取设备诊断日志（反复重启时用）。
用法: python tools/repl_readlog.py
"""
import serial
import time
import sys

PORT = "COM8"

s = serial.Serial(PORT, 115200, timeout=0.05)
s.reset_input_buffer()

print("waiting REPL window...", flush=True)
t0 = time.time()
got = False
while time.time() - t0 < 120:
    s.write(b"\x03")
    time.sleep(0.08)
    d = s.read(4096)
    if d and b">>>" in d:
        print("REPL ENTERED @%.1fs" % (time.time() - t0), flush=True)
        got = True
        break
if not got:
    print("NO REPL", flush=True)
    sys.exit(1)

time.sleep(0.3)
s.reset_input_buffer()
s.write(b"\x01")            # Ctrl-A -> raw REPL
time.sleep(0.4)
print("raw resp:", repr(s.read(100)), flush=True)


def run(script, timeout=6):
    s.reset_input_buffer()
    s.write(script.encode())
    s.write(b"\x04")        # Ctrl-D execute
    out = b""
    t0 = time.time()
    while time.time() - t0 < timeout:
        d = s.read(4096)
        if d:
            out += d
            if b"\x04>" in out:     # raw REPL 结束标记
                break
    return out


for label, script in [
    ("BOOT", "print(open('boot.log').read()[-3000:])"),
    ("ERR", "print(open('err.log').read()[-2000:])"),
    ("CFG", "print(open('config.json').read())"),
]:
    out = run(script)
    txt = out.decode("utf-8", "replace")
    print("=== %s ===" % label)
    print(txt)
    print()

s.write(b"\x02")            # exit raw REPL
s.close()
