#!/usr/bin/env python3
"""mkuf2.py - 把 RP2040 的 .bin 转成 .uf2

为什么不用 picotool：pico-sdk 2.1.0 的 pico_add_extra_outputs 会 FetchContent
拉 picotool 源码并用**宿主编译器**（MSVC/MinGW）编译，本机没有宿主工具链会失败。
UF2 格式足够简单，自己转更省事，也不引入额外依赖。

用法: python mkuf2.py in.bin out.uf2 [base_addr]
"""

import struct
import sys

MAGIC_START0 = 0x0A324655
MAGIC_START1 = 0x9E5D5157
MAGIC_END    = 0x0AB16F30

PAYLOAD      = 256
BLOCK_SIZE   = 512

FAMILY_RP2040 = 0xE48BFF56
FLAGS = 0x00002000          # bit13 = familyID 有效

BASE_ADDR = 0x10000000      # XIP flash 起始


def convert(src: str, dst: str, base: int = BASE_ADDR) -> int:
    data = open(src, "rb").read()
    if not data:
        raise SystemExit(f"{src} 是空文件")

    nblocks = (len(data) + PAYLOAD - 1) // PAYLOAD
    out = bytearray()

    for i in range(nblocks):
        chunk = data[i * PAYLOAD:(i + 1) * PAYLOAD]
        if len(chunk) < PAYLOAD:
            chunk = chunk + b"\xff" * (PAYLOAD - len(chunk))

        blk = bytearray(BLOCK_SIZE)
        # 32 字节 header: magic0, magic1, flags, addr, payload, blkno, nblk, family
        struct.pack_into(
            "<IIIIIIII", blk, 0,
            MAGIC_START0, MAGIC_START1, FLAGS,
            base + i * PAYLOAD, PAYLOAD, i, nblocks, FAMILY_RP2040,
        )
        # 数据区固定从 32 开始，magicEnd 固定在块尾最后 4 字节 (508..511)
        blk[32:32 + PAYLOAD] = chunk
        struct.pack_into("<I", blk, BLOCK_SIZE - 4, MAGIC_END)
        out += blk

    with open(dst, "wb") as f:
        f.write(out)

    print(f"uf2: {dst}  {len(data)} B -> {len(out)} B  ({nblocks} blocks)")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    base = int(sys.argv[3], 0) if len(sys.argv) > 3 else BASE_ADDR
    sys.exit(convert(sys.argv[1], sys.argv[2], base))
