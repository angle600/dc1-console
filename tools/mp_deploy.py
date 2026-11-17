#!/usr/bin/env python3
"""mp_deploy —— 用 WiFi（HTTP）把 MicroPython 代码推到 DC1，无需串口/WebREPL

设备端由 firmware/micropython/main.py 提供：
    PUT /api/file?name=main.py   （body = 文件内容，需 X-Token）

用法：
    python tools/mp_deploy.py --host 192.168.4.1 --token change-me-token
    python tools/mp_deploy.py --host dc1.local --token xxx --only main.py
    python tools/mp_deploy.py --host 192.168.4.1 --state
    python tools/mp_deploy.py --serial COM5 --flash-img ESP8266_GENERIC-FLASH_1M-*.bin

首次仍需串口烧 MicroPython 内核（--flash-img）；之后改代码全走 WiFi。
"""

import argparse
import json
import os
import pathlib
import subprocess
import sys
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "firmware" / "micropython"
DEFAULT_FILES = ["main.py", "cat9554.py", "cse7766.py", "webui.py", "config.json"]


def put(host, name, data, token, timeout=20):
    url = "http://%s/api/file?name=%s" % (host, urllib.parse.quote(name))
    req = urllib.request.Request(url, data=data, method="PUT")
    req.add_header("X-Token", token)
    req.add_header("Content-Type", "application/octet-stream")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode()


def get(host, path, token="", timeout=10):
    req = urllib.request.Request("http://%s%s" % (host, path))
    if token:
        req.add_header("X-Token", token)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode()


def main():
    ap = argparse.ArgumentParser(description="DC1 MicroPython WiFi 部署")
    ap.add_argument("--host", default="192.168.4.1", help="设备 IP 或主机名")
    ap.add_argument("--token", default=os.environ.get("DC1_TOKEN", ""),
                    help="config.json 里的 api_token")
    ap.add_argument("--only", nargs="*", help="只传指定文件")
    ap.add_argument("--state", action="store_true", help="读 /api/state 后退出")
    ap.add_argument("--serial", help="首次烧内核用：串口，如 COM5")
    ap.add_argument("--flash-img", help="首次烧内核用：MicroPython 1M 固件 .bin")
    ap.add_argument("--erase", action="store_true", help="烧内核前整片擦除")
    args = ap.parse_args()

    if args.flash_img:
        if not args.serial:
            sys.exit("--flash-img 需要同时给 --serial")
        img = args.flash_img
        if not os.path.isabs(img) and not os.path.exists(img):
            cand = SRC / img
            img = str(cand) if cand.exists() else img
        cmds = []
        if args.erase:
            cmds.append(["esptool.py", "--port", args.serial, "erase_flash"])
        cmds.append(["esptool.py", "--port", args.serial, "--baud", "460800",
                     "write_flash", "--flash_size=1MB", "0x0", img])
        for c in cmds:
            print("+", " ".join(c))
            subprocess.run(c, check=True)
        print("内核烧好；上电后连 DC1-Setup 配网，再跑本脚本传代码。")
        return

    if args.state:
        d = json.loads(get(args.host, "/api/state"))
        print(json.dumps(d, ensure_ascii=False, indent=2))
        return

    files = args.only or DEFAULT_FILES
    for name in files:
        p = SRC / name
        if not p.exists():
            if name == "config.json":
                print("skip config.json（请先按 config.example.json 生成）")
                continue
            print("skip 缺失:", name)
            continue
        data = p.read_bytes()
        try:
            r = put(args.host, name, data, args.token)
            print("OK %-12s %6d B  %s" % (name, len(data), r.strip()))
        except Exception as e:
            print("FAIL %-12s %r" % (name, e))

    print("\n提示：main.py 改动需重启设备生效（拔插一次或 GET /api/config 触发复位逻辑）。")


if __name__ == "__main__":
    main()
