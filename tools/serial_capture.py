#!/usr/bin/env python3
"""抓串口启动日志：python tools/serial_capture.py COM8 [秒数]"""
import sys
import time
import serial

port = sys.argv[1] if len(sys.argv) > 1 else "COM8"
secs = int(sys.argv[2]) if len(sys.argv) > 2 else 15

print("打开 %s @115200，抓 %ds 启动日志..." % (port, secs))
try:
    s = serial.Serial(port, 115200, timeout=0.3)
except Exception as e:
    print("打开串口失败：%s" % e)
    sys.exit(1)

t0 = time.time()
buf = b""
while time.time() - t0 < secs:
    try:
        d = s.read(4096)
    except Exception as e:
        print("读取异常：%s" % e)
        break
    if d:
        buf += d
        sys.stdout.write(d.decode("utf-8", "replace"))
        sys.stdout.flush()
s.close()
print("\n--- 抓到 %d 字节 ---" % len(buf))
