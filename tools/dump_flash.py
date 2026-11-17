#!/usr/bin/env python3
"""dump_flash —— 分段读取 ESP8266 Flash（容忍链路抖动，可续读）

为什么不用一条 read-flash 读完：
  DC1 模组常由 USB-TTL 的 3.3V 供电，1MB 连续读容易在某个点断流
  （esptool 报 "Read more than expected" / "No more data to read"）。
  分段 + 重试 + 断点续读能把「一次失败 = 重来」变成「只重读这一块」。

用法：
  python tools/dump_flash.py --port COM8 --size 1MB --out backup/dc1_original_1M.bin
  python tools/dump_flash.py --port COM8 --chunk 0x8000 --baud 115200

产物：
  <out>            完整镜像
  <out>.chunks/    分块缓存（可续读；成功后自动删除）
"""

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import time

SIZES = {"512K": 0x80000, "1MB": 0x100000, "2MB": 0x200000, "4MB": 0x400000}


def parse_size(s):
    s = s.strip().upper().replace(" ", "")
    if s in SIZES:
        return SIZES[s]
    return int(s, 0)


def read_chunk(port, baud, off, size, dst, no_stub=True, tries=4):
    cmd = [sys.executable, "-m", "esptool", "--port", port, "--baud", str(baud),
           "--before", "no-reset", "--after", "no-reset"]
    if no_stub:
        cmd.append("--no-stub")
    cmd += ["read-flash", hex(off), hex(size), dst]
    for i in range(1, tries + 1):
        if os.path.exists(dst):
            os.remove(dst)
        p = subprocess.run(cmd, capture_output=True, text=True)
        ok = (p.returncode == 0 and os.path.exists(dst)
              and os.path.getsize(dst) == size)
        if ok:
            return True
        tail = [l for l in (p.stdout + p.stderr).strip().splitlines() if l.strip()]
        print("    重试 %d/%d：%s" % (i, tries, tail[-1][:120] if tail else "?"))
        # 芯片可能被复位，稍等再试（若已退出 bootloader 需人工重新上电）
        time.sleep(1.5)
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM8")
    ap.add_argument("--size", default="1MB")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--chunk", default="0x10000")
    ap.add_argument("--out", default="backup/dc1_original_1M.bin")
    ap.add_argument("--stub", action="store_true", help="用 flasher stub（更快但更易断）")
    a = ap.parse_args()

    total = parse_size(a.size)
    chunk = parse_size(a.chunk)
    parts = a.out + ".chunks"
    os.makedirs(parts, exist_ok=True)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)

    print("读 Flash：%s  0x0 ~ 0x%X  块=%dKB  波特率=%d  路径=%s" %
          (a.port, total, chunk // 1024, a.baud, a.out))

    failed = []
    t0 = time.time()
    for off in range(0, total, chunk):
        size = min(chunk, total - off)
        dst = os.path.join(parts, "%06X.bin" % off)
        if os.path.exists(dst) and os.path.getsize(dst) == size:
            print("  0x%06X  已缓存" % off)
            continue
        print("  0x%06X  读取 %d B ..." % (off, size), flush=True)
        if not read_chunk(a.port, a.baud, off, size, dst, no_stub=not a.stub):
            failed.append(off)
            print("      ✗ 失败（可重新上电后重跑本脚本，已读块会跳过）")

    if failed:
        print("\n失败块：%s" % ", ".join("0x%06X" % x for x in failed))
        print("处理：给模组重新上电（IO0 仍接 GND）后重跑同一命令即可续读。")
        sys.exit(2)

    with open(a.out, "wb") as f:
        for off in range(0, total, chunk):
            with open(os.path.join(parts, "%06X.bin" % off), "rb") as p:
                f.write(p.read())

    data = open(a.out, "rb").read()
    print("\n完成：%s  %d bytes  %.1fs" % (a.out, len(data), time.time() - t0))
    print("sha256:", hashlib.sha256(data).hexdigest())
    print("magic :", "0x%02X%s" % (data[0], "（合法 ESP 镜像）" if data[0] in (0xE9, 0xEA) else "（可疑！）"))
    shutil.rmtree(parts, ignore_errors=True)


if __name__ == "__main__":
    main()
