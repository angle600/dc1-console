"""长跑 v2：每 10 s 打一次 /api/state + /api/sys，记录延迟、失败、内存趋势。

目的：定位昨晚 17 次 4 s 超时的成因
  - 若 h_max 很大 → 设备在处理慢请求（GC/文件）
  - 若 req_n 增长而客户端超时 → 单线程排队
  - 若 req_n 不增长 → 网络层（WiFi/lwIP）丢包
"""
import json
import socket
import statistics
import time

IP = "192.168.31.243"
DURATION = 1800
INTERVAL = 10

out = open(".tmp/longrun2.log", "w")
t0 = time.time()
fails = 0
n = 0
lat = []
prev_up = None
prev_req = None


def get(path, timeout=4.0):
    t = time.time()
    c = socket.create_connection((IP, 80), timeout=timeout)
    try:
        c.sendall(("GET %s HTTP/1.0\r\n\r\n" % path).encode())
        c.settimeout(timeout)
        d = b""
        while True:
            ch = c.recv(2048)
            if not ch:
                break
            d += ch
    finally:
        c.close()
    body = d.split(b"\r\n\r\n", 1)[-1]
    return json.loads(body), time.time() - t


while time.time() - t0 < DURATION:
    n += 1
    try:
        st, d1 = get("/api/state")
        lat.append(d1)
        # 精简固件已移除 /api/sys：用 state.time 判定重启
        up = st.get("time", 0)
        req = 0
        note = ""
        if prev_up is not None and up < prev_up:
            note += " REBOOT"
        if prev_req is not None and req <= prev_req:
            note += " REQ_STUCK"
        prev_up, prev_req = up, req
        out.write("%.0fs ok lat=%.2fs sched=%s%s\n" % (
            time.time() - t0, d1, st.get("sched"), note))
    except Exception as e:
        fails += 1
        out.write("%.0fs FAIL %s\n" % (time.time() - t0, e))
    out.flush()
    time.sleep(INTERVAL)

lat.sort()
summary = "DONE n=%d fails=%d P50=%.2fs P95=%.2fs max=%.2fs" % (
    n, fails, lat[len(lat) // 2], lat[int(len(lat) * 0.95)] if lat else 0, lat[-1] if lat else 0)
out.write(summary + "\n")
out.close()
print(summary)
