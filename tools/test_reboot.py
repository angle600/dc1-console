# 连续 /api/reboot N 次，统计恢复率
import json
import sys
import time
import urllib.request

HOST = "http://192.168.31.243"
TOKEN = "534e496df6291f68"
N = int(sys.argv[1]) if len(sys.argv) > 1 else 5
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(path, method="GET", body=None, timeout=8):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(HOST + path, data=data, method=method,
                                 headers={"x-token": TOKEN,
                                          "Content-Type": "application/json"})
    with OPENER.open(req, timeout=timeout) as r:
        return r.status, r.read().decode()


ok_n = 0
for i in range(1, N + 1):
    try:
        st, body = call("/api/reboot", "POST", {})
        print("第 %d 次 reboot -> %s %s" % (i, st, body), flush=True)
    except Exception as e:
        print("第 %d 次 reboot 请求异常 %r" % (i, e), flush=True)

    t0 = time.time()
    back = False
    while time.time() - t0 < 75:
        time.sleep(3)
        try:
            st, body = call("/api/ping", timeout=4)
            if st == 200 and '"ok":1' in body.replace(" ", ""):
                back = True
                print("   -> %.0fs 恢复 %s" % (time.time() - t0, body.strip()),
                      flush=True)
                break
        except Exception:
            pass
    if back:
        ok_n += 1
    else:
        print("   -> 75s 未恢复（挂死）", flush=True)
        break
    time.sleep(2)

print("\n恢复 %d/%d" % (ok_n, N))
sys.exit(0 if ok_n == N else 1)
