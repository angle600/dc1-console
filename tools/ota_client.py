# DC1 OTA 客户端：分块 POST /api/put 推送固件
import base64
import hashlib
import json
import os
import sys
import time
import urllib.request

HOST = sys.argv[1] if len(sys.argv) > 1 else "http://192.168.31.243"
TOKEN = "534e496df6291f68"
NAME = sys.argv[2] if len(sys.argv) > 2 else "app.mpy"
SRC = sys.argv[3] if len(sys.argv) > 3 else r"D:\work\phicomm\firmware\build\mpy\app.mpy"
CHUNK = int(sys.argv[4]) if len(sys.argv) > 4 else 256
# 实测边界：请求体 <=597B 稳定，>=609B 会被设备端截断（返回 bad name）。
# 256B 分块 -> b64 344 + 字段约 41 = 385B，留足余量，5/5 稳定通过。
SAFE_CHUNK = 128


def post(payload, timeout=20):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        HOST + "/api/put", data=body,
        headers={"Content-Type": "application/json", "x-token": TOKEN})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        return e.code, {"ok": 0, "http_err": detail}


def main():
    blob = open(SRC, "rb").read()
    sha = hashlib.sha256(blob).hexdigest()
    cur = CHUNK
    print("src=%s size=%d chunk=%d sha=%s"
          % (os.path.basename(SRC), len(blob), cur, sha[:16]))
    off = 0
    t0 = time.time()
    fails = 0
    while off < len(blob):
        part = blob[off:off + cur]
        b64 = base64.b64encode(part).decode()
        try:
            st, js = post({"name": NAME, "off": off, "b64": b64})
        except Exception as e:
            st, js = 0, {"exc": repr(e)}
        if st != 200 or not js.get("ok"):
            # 大分块会被设备端截断请求体（返回 bad name）。自动降级重发该段。
            if cur > SAFE_CHUNK:
                cur = SAFE_CHUNK
                print("  off=%d refail %r -> 降级到 %d" % (off, js, cur))
                continue
            fails += 1
            print("  off=%d BAD %r" % (off, js))
            if fails > 3:
                print("FAIL too many errors")
                return 2
            time.sleep(0.5)
            continue
        fails = 0
        off += len(part)
        if off % (cur * 8) == 0 or off >= len(blob):
            dt = time.time() - t0
            print("  %d/%d %5.1f%%  %.1fs  %.0f B/s"
                  % (off, len(blob), off * 100.0 / len(blob), dt, off / max(dt, 0.01)))
    print("commit ...")
    st, js = post({"name": NAME, "off": len(blob), "done": 1, "sha": sha}, timeout=60)
    print("commit -> %s %r" % (st, js))
    return 0 if js.get("ok") else 3


if __name__ == "__main__":
    sys.exit(main())
