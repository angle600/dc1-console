"""60 分钟长跑监控：每 20 s 取 /api/state，记录掉线、重启、开关状态。"""
import json
import socket
import time

IP = "192.168.31.243"
DURATION = 3600
INTERVAL = 20

out = open(".tmp/longrun.log", "w")
t0 = time.time()
prev_t = None
reboots = 0
fails = 0
n = 0


def fetch_state():
    c = socket.create_connection((IP, 80), timeout=4)
    c.sendall(b"GET /api/state HTTP/1.0\r\n\r\n")
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
        j = fetch_state()
        t = j.get("time", 0)
        if prev_t and t < prev_t:
            reboots += 1
            out.write("%.0fs REBOOT (t %s -> %s)\n" % (time.time() - t0, prev_t, t))
        prev_t = t
        on = [k for k in ("total", "1", "2", "3", "logo") if j.get(k)]
        out.write("%.0fs ok rssi=%s on=%s\n" % (time.time() - t0, j.get("rssi"), on))
    except Exception as e:
        fails += 1
        out.write("%.0fs FAIL %s\n" % (time.time() - t0, e))
    out.flush()
    time.sleep(INTERVAL)

out.write("DONE n=%d fails=%d reboots=%d\n" % (n, fails, reboots))
out.close()
print("DONE n=%d fails=%d reboots=%d" % (n, fails, reboots))
