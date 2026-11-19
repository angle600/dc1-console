#!/usr/bin/env python3
"""抢 main.py 的 3s REPL 窗口，通过 raw REPL 写 .mpy 到设备。
用法: python tools/repl_flash.py
启动后请在 180s 内把设备断电再上电（IO0 不要接地）。
"""
import serial
import time
import base64
import sys
import json

PORT = "COM8"
WAIT = 180

# 要写入的文件：本地路径 -> 设备文件名
FILES = [
    ("firmware/build/mpy/app.mpy", "app.mpy"),
]

# 要合并进设备 config.json 的键值（恢复计量、关调试口）
CFG_PATCH = {"debug_log_tx": False, "meter_enabled": True}

blobs = []
for src, name in FILES:
    data = open(src, "rb").read()
    blobs.append((name, data))
    print("SRC %s -> %s %d B" % (src, name, len(data)), flush=True)

s = serial.Serial(PORT, 115200, timeout=0.05)
s.reset_input_buffer()

# 持续发 Ctrl-C 抢 REPL 窗口
print("waiting REPL window (power-cycle now)...", flush=True)
t0 = time.time()
got = False
while time.time() - t0 < WAIT:
    s.write(b"\x03")
    time.sleep(0.08)
    d = s.read(4096)
    if d:
        txt = d.decode("utf-8", "replace")
        if ">>>" in txt:
            print("REPL ENTERED @%.1fs" % (time.time() - t0), flush=True)
            got = True
            break
if not got:
    print("NO REPL after %ds" % WAIT, flush=True)
    sys.exit(1)

time.sleep(0.3)
s.reset_input_buffer()

# 进 raw REPL
s.write(b"\x01")            # Ctrl-A
time.sleep(0.4)
resp = s.read(200)
print("raw mode resp:", repr(resp[:60]), flush=True)


def run(script, expect, timeout=25):
    s.write(script.encode())
    s.write(b"\x04")        # Ctrl-D execute
    out = b""
    t0 = time.time()
    while time.time() - t0 < timeout:
        d = s.read(4096)
        if d:
            out += d
            if expect in out:
                break
    return out


import ubinascii as _u
for name, data in blobs:
    b64 = _u.b64encode(data).decode()
    CH = 480
    chunks = [b64[i:i + CH] for i in range(0, len(b64), CH)]
    script = "import ubinascii\nf=open('%s','wb')\n" % name
    for c in chunks:
        script += "f.write(ubinascii.a2b_base64('%s'))\n" % c
    script += "f.close()\nprint('WLEN=%d')\n" % len(data)
    out = run(script, b"WLEN=")
    ok = b"WLEN=" in out
    print("write %-10s %s  %s" % (name, "OK" if ok else "FAIL",
                                  out[-120:].decode("utf-8", "replace")), flush=True)
    if not ok:
        sys.exit(2)

# 合并 config.json
cfg_js = json.dumps(CFG_PATCH)
script = (
    "import json\n"
    "try:\n"
    " c=json.load(open('config.json'))\n"
    "except Exception:\n"
    " c={}\n"
    "c.update(%s)\n"
    "json.dump(c,open('config.json','w'))\n"
    "print('CFGOK')\n" % cfg_js
)
out = run(script, b"CFGOK")
print("patch config %s" % ("OK" if b"CFGOK" in out else "FAIL"), flush=True)

# 复位
s.write(b"\x02")            # Ctrl-B exit raw REPL
time.sleep(0.2)
s.write(b"\x04")            # soft reset
time.sleep(0.5)
s.close()
print("DONE", flush=True)
