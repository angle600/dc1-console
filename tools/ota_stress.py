# OTA 稳定性压测：连续 N 次推 app.mpy，记录成功率
import base64
import hashlib
import json
import sys
import time
import urllib.error
import urllib.request

HOST = "http://192.168.31.243"
TOKEN = "534e496df6291f68"
SRC = r"D:\work\phicomm\firmware\build\mpy\app.mpy"
CHUNK = 256
ROUNDS = 5


def post(payload, timeout=25):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        HOST + "/api/put", data=body,
        headers={"Content-Type": "application/json", "x-token": TOKEN})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, {"raw": e.read().decode("utf-8", "replace")[:70]}
    except Exception as e:
        return 0, {"exc": repr(e)[:70]}


def ping():
    try:
        with urllib.request.urlopen(HOST + "/api/ping", timeout=6) as r:
            return r.read().decode()
    except Exception as e:
        return "ERR " + repr(e)[:50]


blob = open(SRC, "rb").read()
sha = hashlib.sha256(blob).hexdigest()
print("src=%d chunk=%d rounds=%d" % (len(blob), CHUNK, ROUNDS))

ok_n = 0
for rd in range(1, ROUNDS + 1):
    off = 0
    t0 = time.time()
    bad = 0
    while off < len(blob):
        part = blob[off:off + CHUNK]
        st, js = post({"name": "app.mpy", "off": off,
                       "b64": base64.b64encode(part).decode()})
        if st != 200 or not js.get("ok"):
            bad += 1
            print("  rd%d off=%d FAIL st=%s %r" % (rd, off, st, js))
            time.sleep(0.4)
            if bad > 3:
                break
            continue
        off += len(part)
    if bad > 3:
        print("rd%d -> ABORT" % rd)
        continue
    st, js = post({"name": "app.mpy", "off": len(blob), "done": 1, "sha": sha}, 60)
    good = (st == 200 and js.get("ok") == 1 and js.get("committed") == len(blob))
    print("rd%d %s %.1fs committed=%r  ping=%s"
          % (rd, "OK  " if good else "FAIL", time.time() - t0,
             js.get("committed"), ping()))
    sys.stdout.flush()
    if good:
        ok_n += 1
    time.sleep(0.5)
print("RESULT %d/%d" % (ok_n, ROUNDS))
