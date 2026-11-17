"""60 分钟长跑监控：每 20 s 取 /api/state + /api/sys

关注三件事：
  1. 掉线（FAIL 计数）
  2. 重启（up 变小时刻）
  3. 内存趋势（free/min_free 是否持续下降）
"""
import json
import socket
import time

IP = "192.168.31.243"
DURATION = 3600
INTERVAL = 20

out = open(".tmp/longrun.log", "w")
t0 = time.time()
prev_up = None
reboots = 0
fails = 0
n = 0


def get_json(path):
    c = socket.create_connection((IP, 80), timeout=4)
    c.sendall(("GET %s HTTP/1.0\r\n\r\n" % path).encode())
    c.settimeout(4)
    d = b""
    while True:
        ch = c.recv(2048)
        if not ch:
            break
        d += ch
    c.close()
    return json.loads(d.split(b"\r\n\r\n", 1)[-1])


while time.time() - t0 < DURATION:
    n += 1
    try:
        st = get_json("/api/state")
        sy = get_json("/api/sys")
        up = sy.get("up", 0)
        if prev_up is not None and up < prev_up:
            reboots += 1
            out.write("%.0fs REBOOT (up %s -> %s)\n" % (time.time() - t0, prev_up, up))
        prev_up = up
        on = [k for k in ("total", "1", "2", "3", "logo") if st.get(k)]
        out.write("%.0fs ok rssi=%s free=%s min=%s up=%.0f on=%s\n" % (
            time.time() - t0, st.get("rssi"), sy.get("free"),
            sy.get("min_free"), up, on))
    except Exception as e:
        fails += 1
        out.write("%.0fs FAIL %s\n" % (time.time() - t0, e))
    out.flush()
    time.sleep(INTERVAL)

out.write("DONE n=%d fails=%d reboots=%d\n" % (n, fails, reboots))
out.close()
print("DONE n=%d fails=%d reboots=%d" % (n, fails, reboots))
