#!/usr/bin/env python3
"""fw_scan —— 分析 ESP8266/ESP32 固件 dump（找版本串、段布局、可疑标定常量）

用途：
  1. 确认 dump 是否完整（ESP 镜像魔数 0xE9 / flash size / 段数）
  2. 找版本号、型号、AT 指令等可读字符串
  3. 扫 32 位 IEEE-754 浮点常量，辅助定位标定系数（V1R/V2R、增益等）

用法：
  python tools/fw_scan.py firmware/original/dc1_original_1M.bin
  python tools/fw_scan.py backup.bin --keywords DC1,CSE7766,phicomm
"""

import argparse
import hashlib
import math
import os
import re
import struct
import sys

FLASH_SIZE = {0: "512KB", 1: "256KB", 2: "1MB", 3: "2MB", 4: "4MB", 5: "2MB", 6: "4MB",
              8: "8MB", 9: "16MB"}


def esp_header(d):
    if not d:
        return {}
    magic = d[0]
    info = {"magic": "0x%02X" % magic,
            "valid": magic in (0xE9, 0xEA),
            "segments": d[1] if len(d) > 1 else 0,
            "flash_mode": d[2] if len(d) > 2 else 0,
            "flash_size": FLASH_SIZE.get(d[3] >> 4, "?"),
            "entry": "0x%08X" % struct.unpack_from("<I", d, 4)[0] if len(d) >= 8 else "?"}
    return info


def segments(d):
    """解析 ESP8266 段表（magic 0xE9）"""
    out = []
    if len(d) < 8 or d[0] != 0xE9:
        return out
    off = 8
    for _ in range(d[1]):
        if off + 8 > len(d):
            break
        addr, size = struct.unpack_from("<II", d, off)
        out.append((addr, size, off + 8))
        off += 8 + size
        if off >= len(d):
            break
    return out


def strings(d, minlen=6, keywords=()):
    pat = re.compile(rb"[\x20-\x7e]{%d,}" % minlen)
    found = [m.group().decode("ascii", "ignore") for m in pat.finditer(d)]
    if keywords:
        kw = [k.lower() for k in keywords]
        found = [s for s in found if any(k in s.lower() for k in kw)]
    return found


def float_hist(d, lo=1e-7, hi=1e7, top=25):
    counts = {}
    n = len(d) - 3
    for i in range(0, n, 4):
        v = struct.unpack_from("<f", d, i)[0]
        if not math.isfinite(v) or v == 0.0:
            continue
        if not (lo <= abs(v) <= hi):
            continue
        key = float("%.6g" % v)
        counts[key] = counts.get(key, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])[:top]
    return ranked


def near_unit(d, lo=0.5, hi=4.0, mincount=2):
    hits = []
    for off in range(0, len(d) - 3, 4):
        v = struct.unpack_from("<f", d, off)[0]
        if math.isfinite(v) and lo <= v <= hi:
            hits.append((off, v))
    return hits


def main():
    ap = argparse.ArgumentParser(description="ESP 固件 dump 分析")
    ap.add_argument("image")
    ap.add_argument("--keywords", default="DC1,phicomm,CSE7766,esp,AT+",
                    help="字符串过滤关键字，逗号分隔，空则不过滤")
    ap.add_argument("--strings", type=int, default=15, help="每类显示条数")
    ap.add_argument("--unit-scan", action="store_true", help="列出 0.5~4.0 的浮点常量偏移")
    a = ap.parse_args()

    if not os.path.exists(a.image):
        sys.exit("文件不存在：%s" % a.image)

    d = open(a.image, "rb").read()
    print("file          :", a.image)
    print("size          :", len(d), "bytes")
    print("sha256        :", hashlib.sha256(d).hexdigest())
    h = esp_header(d)
    for k, v in h.items():
        print("header.%-8s:" % k, v)

    segs = segments(d)
    if segs:
        print("segments      :", len(segs))
        for addr, size, off in segs[:6]:
            print("  0x%06X  %7d B  @0x%06X" % (addr, size, off))

    kws = [k for k in a.keywords.split(",") if k]
    print("\n--- 字符串（关键字：%s）---" % (",".join(kws) or "无"))
    for s in strings(d, keywords=kws)[:max(1, a.strings)]:
        print("  ", s[:110])

    print("\n--- 浮点常量 top %d ---" % 25)
    for v, c in float_hist(d):
        print("  %12.6g  ×%d" % (v, c))

    if a.unit_scan:
        hits = near_unit(d)
        bucket = {}
        for off, v in hits:
            bucket.setdefault(round(v, 4), []).append(off)
        print("\n--- 0.5~4.0 区间常量（可能的分压/采样常量）---")
        for v in sorted(bucket, key=lambda k: -len(bucket[k]))[:12]:
            offs = bucket[v]
            print("  %8.4f  ×%d  偏移 %s%s" %
                  (v, len(offs), ", ".join("0x%X" % o for o in offs[:4]),
                   " …" if len(offs) > 4 else ""))


if __name__ == "__main__":
    main()
