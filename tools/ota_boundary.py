# 压测 OTA 分块上限：不同 chunk 大小各传 4KB
import base64
import hashlib
import json
import sys
import time
import urllib.error
import urllib.request

HOST = "http://192.168.31.243"
TOKEN = "534e496df6291f68"
SIZE = 4096
CHUNKS = [128, 192, 224, 256, 288, 320, 384, 448, 512]


def post(payload, timeout=25):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        HOST + "/api/put", data=body,
        headers={"Content-Type": "application/json", "x-token": TOKEN})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, {"raw": e.read().decode("utf-8", "replace")[:90]}
    except Exception as e:
        return 0, {"exc": repr(e)[:90]}


blob = bytes((i * 7 + 13) & 0xFF for i in range(SIZE))
sha = hashlib.sha256(blob).hexdigest()

print("%-7s %-6s %-10s %-9s %s" % ("chunk", "blocks", "result", "time", "note"))
for chunk in CHUNKS:
    name = "t%d.bin" % chunk
    off = 0
    t0 = time.time()
    ok = True
    note = ""
    while off < len(blob):
        part = blob[off:off + chunk]
        st, js = post({"name": name, "off": off,
                       "b64": base64.b64encode(part).decode()})
        if st != 200 or not js.get("ok"):
            ok = False
            note = "off=%d st=%s %r" % (off, st, js)[:80]
            break
        off += len(part)
    if ok:
        st, js = post({"name": name, "off": len(blob), "done": 1, "sha": sha}, 60)
        ok = (st == 200 and js.get("ok") == 1)
        if not ok:
            note = "commit st=%s %r" % (st, js)[:80]
        elif js.get("committed") != SIZE:
            ok = False
            note = "size=%r" % js.get("committed")
    print("%-7d %-6d %-10s %-9.2f %s"
          % (chunk, (SIZE + chunk - 1) // chunk,
             "OK" if ok else "FAIL", time.time() - t0, note))
    sys.stdout.flush()
    time.sleep(0.3)
