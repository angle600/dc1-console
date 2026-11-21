# 精确找 OTA 分块边界，并抓失败的完整响应
import base64
import json
import sys
import time
import urllib.error
import urllib.request

HOST = "http://192.168.31.243"
TOKEN = "534e496df6291f68"
SIZE = 2048
CHUNKS = [384, 400, 416, 424, 432, 440, 448]


def post(payload, timeout=20):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        HOST + "/api/put", data=body,
        headers={"Content-Type": "application/json", "x-token": TOKEN})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode(), len(body), time.time() - t0
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), len(body), time.time() - t0
    except Exception as e:
        return 0, repr(e), len(body), time.time() - t0


blob = bytes((i * 11 + 5) & 0xFF for i in range(SIZE))

print("%-6s %-7s %-5s %-6s %s" % ("chunk", "reqB", "st", "time", "resp"))
for chunk in CHUNKS:
    name = "b%d.bin" % chunk
    part = blob[:chunk]
    st, resp, reqlen, dt = post({"name": name, "off": 0,
                                 "b64": base64.b64encode(part).decode()})
    ok = '"ok": 1' in resp or '"ok":1' in resp
    print("%-6d %-7d %-5d %-6.2f %s"
          % (chunk, reqlen, st, dt, (resp[:80] if not ok else "ok")))
    sys.stdout.flush()
    time.sleep(0.4)
