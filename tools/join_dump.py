#!/usr/bin/env python3
"""join_dump —— 把任意尺寸的分块读回数据按偏移拼成整片镜像，并做覆盖/校验检查

配合 tools/dump_flash.py 使用：断线重连后可以只补缺失区间，
本工具负责把 <out>.chunks/ 里参差不齐的块（8KB/32KB/64KB 混读）拼起来。

用法：
  python tools/join_dump.py --dir backup/dc1_original_1M.bin.chunks --size 1MB \
      --out backup/dc1_original_1M.bin
  # 额外补丁文件：--add 0x1E000:.tmp/gap.bin  0xB0000:.tmp/hi.bin
  # 与参考镜像比对：--compare firmware/original/dc1_original_1M.bin
"""

import argparse
import hashlib
import os
import sys

SIZES = {"512K": 0x80000, "1MB": 0x100000, "2MB": 0x200000, "4MB": 0x400000}
FLASH = {0: "512KB", 1: "256KB", 2: "1MB", 3: "2MB", 4: "4MB"}


def parse_size(s):
    s = s.strip().upper().replace(" ", "")
    return SIZES[s] if s in SIZES else int(s, 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir")
    ap.add_argument("--size", default="1MB")
    ap.add_argument("--out", default="backup/dc1_original_1M.bin")
    ap.add_argument("--add", nargs="*", default=[], help="offset:file 形式的补丁块")
    ap.add_argument("--compare")
    a = ap.parse_args()

    total = parse_size(a.size)
    buf = bytearray(b"\xff" * total)
    filled = bytearray(total)          # 1 = 该字节已写入

    def put(off, blob):
        end = min(off + len(blob), total)
        buf[off:end] = blob[:end - off]
        for i in range(off, end):
            filled[i] = 1

    count = 0
    if a.dir and os.path.isdir(a.dir):
        for f in sorted(os.listdir(a.dir)):
            off = int(f.split(".")[0], 16)
            blob = open(os.path.join(a.dir, f), "rb").read()
            put(off, blob)
            count += 1
    for spec in a.add:
        off_s, path = spec.split(":", 1)
        put(int(off_s, 0), open(path, "rb").read())
        count += 1

    holes = [i for i, v in enumerate(filled) if not v]
    print("块数        :", count)
    if holes:
        # 合并连续空洞
        spans, start, prev = [], holes[0], holes[0]
        for h in holes[1:]:
            if h != prev + 1:
                spans.append((start, prev)); start = h
            prev = h
        spans.append((start, prev))
        print("未覆盖区间  :", ", ".join("0x%X-0x%X (%d B)" % (s, e, e - s + 1) for s, e in spans[:8]))
        print("结论        : 不完整，先补这些区间再拼装")
        sys.exit(2)

    open(a.out, "wb").write(buf)
    print("输出        :", a.out, len(buf), "bytes")
    print("sha256      :", hashlib.sha256(buf).hexdigest())
    print("magic       : 0x%02X  flash=%s" %
          (buf[0], FLASH.get(buf[3] >> 4, "?")))

    if a.compare and os.path.exists(a.compare):
        ref = open(a.compare, "rb").read()
        n = min(len(ref), len(buf))
        diff = [i for i in range(n) if ref[i] != buf[i]]
        print("\n与参考镜像对比：%s" % a.compare)
        print("  长度   : %d vs %d" % (len(buf), len(ref)))
        print("  差异字节: %d (%.2f%%)" % (len(diff), 100.0 * len(diff) / n))
        if diff:
            spans, start, prev = [], diff[0], diff[0]
            for d in diff[1:]:
                if d != prev + 1:
                    spans.append((start, prev)); start = d
                prev = d
            spans.append((start, prev))
            print("  差异区间（前 8 段）:")
            for s, e in spans[:8]:
                print("    0x%06X-0x%06X  %6d B" % (s, e, e - s + 1))
            print("  提示: 0x7C000 之后多为 RF 校准/NVS/用户区，逐台不同属正常")


if __name__ == "__main__":
    main()
