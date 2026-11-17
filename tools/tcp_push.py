#!/usr/bin/env python3
"""tcp_push —— 通过设备 HTTP 接口无线推送文件（无需拆机、无需串口）

原理：POST /api/put {"name","off","b64"} 分片写入；随后 GET /api/get 读回校验 sha256。

用法：
  python tools/tcp_push.py --ip 192.168.31.243 --file ..\\build\\mpy\\app.mpy --name app.mpy
  python tools/tcp_push.py --ip 192.168.31.243 --file main.py            # 名字同源文件
  python tools/tcp_push.py --ip 192.168.31.243 --get app.mpy --out chk.bin
  python tools/tcp_push.py --ip 192.168.31.243 --reset                   # 软重启设备（走 /api/reboot）
"""

import argparse
import base64
import hashlib
import json
import os
import sys
import time
import urllib.request

CHUNK = 512   # 1024 会让 1MB 版 ESP8266 撞内存上限（实测 400）


def post(ip, path, payload, timeout=8):
    req = urllib.request.Request(
        "http://%s%s" % (ip, path),
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def get(ip, path, timeout=8):
    with urllib.request.urlopen("http://%s%s" % (ip, path), timeout=timeout) as r:
        return r.read()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ip", required=True)
    ap.add_argument("--file")
    ap.add_argument("--name")
    ap.add_argument("--get")
    ap.add_argument("--out")
    ap.add_argument("--reset", action="store_true")
    a = ap.parse_args()

    if a.get:
        blob = base64.b64decode(get(a.ip, "/api/get?name=%s" % a.get))
        if a.out:
            open(a.out, "wb").write(blob)
        print("读到 %s  %d B  sha256=%s" % (a.get, len(blob), hashlib.sha256(blob).hexdigest()[:16]))
        return

    if a.reset:
        try:
            print(post(a.ip, "/api/reboot", {}))
        except Exception as e:
            print("reboot 请求已发出（设备可能已重启）：%s" % e)
        return

    if not a.file:
        sys.exit("需要 --file 或 --get/--reset")

    blob = open(a.file, "rb").read()
    name = a.name or os.path.basename(a.file)
    print("推送 %s -> %s  %d B" % (a.file, name, len(blob)))

    off = 0
    t0 = time.time()
    while off < len(blob):
        piece = blob[off:off + CHUNK]
        r = post(a.ip, "/api/put", {"name": name, "off": off,
                                    "b64": base64.b64encode(piece).decode()})
        if not r.get("ok"):
            sys.exit("失败于 offset %d: %s" % (off, r))
        off = r["off"]
        sys.stdout.write("\r  %5.1f%%  %d/%d" % (100.0 * off / len(blob), off, len(blob)))
        sys.stdout.flush()
    print("\r  100.0%%  %d/%d  用时 %.1fs" % (len(blob), len(blob), time.time() - t0))

    try:
        back = base64.b64decode(get(a.ip, "/api/get?name=%s" % name))
    except Exception as e:
        print("回读校验跳过（设备读大文件会 OOM）：%s" % e)
        return
    
    ok = hashlib.sha256(back).digest() == hashlib.sha256(blob).digest()
    print("回读校验: %s (%d B)" % ("一致 ✓" if ok else "不一致 ✗", len(back)))
    if not ok:
        sys.exit(3)


if __name__ == "__main__":
    main()
