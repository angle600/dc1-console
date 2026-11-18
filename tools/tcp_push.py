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

CHUNK = 128   # 实测阈值：160 可过、192 断连；128 留余量


TOKEN = ""


def _url(ip, path):
    if not TOKEN:
        return "http://%s%s" % (ip, path)
    sep = "&" if "?" in path else "?"
    return "http://%s%s%st=%s" % (ip, path, sep, TOKEN)


def post_raw(ip, path, payload, timeout=8):
    """裸 socket POST：urllib 的请求头多 200 B，1MB 版会直接撞内存上限"""
    import socket as _s
    body = json.dumps(payload).encode()
    c = _s.create_connection((ip, 80), timeout=timeout)
    q = _url(ip, path).split(ip, 1)[1]
    c.sendall(("POST %s HTTP/1.0\r\nContent-Length: %d\r\n\r\n"
               % (q, len(body))).encode() + body)
    c.settimeout(timeout)
    d = b""
    while True:
        try:
            ch = c.recv(1024)
        except Exception:
            break
        if not ch:
            break
        d += ch
    c.close()
    body = d.split(b"\r\n\r\n", 1)[-1]
    try:
        return json.loads(body.decode())
    except Exception:
        return {"ok": 0, "err": body[:80].decode("utf-8", "replace")}


def post(ip, path, payload, timeout=8):
    req = urllib.request.Request(
        _url(ip, path),
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def get_raw(ip, path, timeout=6):
    """裸 socket 读：urllib 在设备侧偶发 RemoteDisconnected，校验用它更稳"""
    import socket as _s
    c = _s.create_connection((ip, 80), timeout=timeout)
    c.sendall(("GET %s HTTP/1.0\r\n\r\n" % _url(ip, path).split(ip, 1)[1]).encode())
    c.settimeout(timeout)
    d = b""
    while True:
        try:
            ch = c.recv(2048)
        except Exception:
            break
        if not ch:
            break
        d += ch
    c.close()
    body = d.split(b"\r\n\r\n", 1)[-1]
    try:
        return json.loads(body.decode())
    except Exception:
        return {"ok": 0, "err": body[:80].decode("utf-8", "replace")}


def get(ip, path, timeout=8):
    with urllib.request.urlopen(_url(ip, path), timeout=timeout) as r:
        return r.read()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ip", required=True)
    ap.add_argument("--file")
    ap.add_argument("--name")
    ap.add_argument("--get")
    ap.add_argument("--out")
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--token", help="设备 api_token（设备设了令牌后必须提供）")
    ap.add_argument("--skip-verify", action="store_true",
                    help="跳过回读校验直接提交（内存极低时用，风险自负）")
    a = ap.parse_args()
    global TOKEN
    TOKEN = a.token or ""

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
        r = post_raw(a.ip, "/api/put", {"name": name, "off": off,
                                    "b64": base64.b64encode(piece).decode()})
        if not r.get("ok"):
            sys.exit("失败于 offset %d: %s" % (off, r))
        off = r["off"]
        sys.stdout.write("\r  %5.1f%%  %d/%d" % (100.0 * off / len(blob), off, len(blob)))
        sys.stdout.flush()
    print("\r  100.0%%  %d/%d  用时 %.1fs" % (len(blob), len(blob), time.time() - t0))

    if a.skip_verify:
        r = post_raw(a.ip, "/api/put", {"name": name, "done": 1})
        print("已提交(跳过校验): %s" % r)
        return

    # 设备端 sha256 校验后提交（低内存下回读必失败，故不再回读）
    sha = hashlib.sha256(blob).hexdigest()
    time.sleep(1.0)
    r = post_raw(a.ip, "/api/put", {"name": name, "done": 1, "sha": sha})
    if r.get("ok") and "committed" in r:
        print("校验通过并已提交 ✓ sha256=%s… size=%s" % (sha[:12], r.get("committed")))
    else:
        print("提交失败：%s" % r)
        sys.exit(3)


if __name__ == "__main__":
    main()
