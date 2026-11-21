# 从设备分片下载 app.mpy 存档（512B/片，避免设备堆压力）
import base64
import hashlib
import sys
import time
import urllib.request

HOST = "http://192.168.31.243"
TOKEN = "534e496df6291f68"
NAME = "app.mpy"
OUT = r"D:\work\phicomm\firmware\build\mpy\app-1.2.13-16205.mpy"
CHUNK = 256
EXPECT = 16205


def get(off, n):
    url = "%s/api/get?name=%s&off=%d&len=%d" % (HOST, NAME, off, n)
    req = urllib.request.Request(url, headers={"x-token": TOKEN})
    with urllib.request.urlopen(req, timeout=20) as r:
        return base64.b64decode(r.read().decode())


blob = b""
off = 0
t0 = time.time()
while off < EXPECT:
    try:
        part = get(off, CHUNK)
    except Exception as e:
        print("off=%d ERR %r" % (off, e))
        sys.exit(1)
    if not part:
        break
    blob += part
    off += len(part)
    print("  %d/%d  %.1fs" % (off, EXPECT, time.time() - t0))
    if len(part) < CHUNK:
        break

print("downloaded %d bytes" % len(blob))
if len(blob) != EXPECT:
    print("SIZE MISMATCH, expected %d" % EXPECT)
    sys.exit(2)

open(OUT, "wb").write(blob)
print("saved %s" % OUT)
print("sha256 %s" % hashlib.sha256(blob).hexdigest())
print("first8 %s" % blob[:8].hex())
