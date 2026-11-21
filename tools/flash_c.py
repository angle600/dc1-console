#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""烧录 DC1 C 版固件（ESP8266_RTOS_SDK 产物，3 个 bin）

与 MicroPython 版的区别
  MP 版只有 1 个内核 bin（0x0）+ 文件系统部署
  C 版是三段：bootloader@0x0 / partition-table@0x8000 / app@0x10000

用法
  python tools/flash_c.py                # 全片擦除 + 烧录 + 校验 + 复位
  python tools/flash_c.py --no-erase     # 不擦除（保留 NVS 里的配置）
  python tools/flash_c.py --no-verify    # 跳过校验

注意
  * 首次烧 C 固件必须擦除：MP 版的文件系统残留会干扰
  * 之后重烧可用 --no-erase，保留 NVS 里的 ssid/token
"""

import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import factory_program as fp          # 复用端口识别 / 下载模式 / esptool 封装

ROOT = os.path.dirname(HERE)
BUILD = os.path.join(ROOT, "firmware", "c", "build")

OFF_BOOT = "0x0"
OFF_PT = "0x8000"
OFF_APP = "0x10000"


def main():
    ap = argparse.ArgumentParser(description="烧录 DC1 C 版固件")
    ap.add_argument("--data-port")
    ap.add_argument("--ctrl-port")
    ap.add_argument("--no-erase", action="store_true")
    ap.add_argument("--no-verify", action="store_true")
    args = ap.parse_args()

    boot = os.path.join(BUILD, "bootloader", "bootloader.bin")
    pt = os.path.join(BUILD, "partition_table", "partition-table.bin")
    app = os.path.join(BUILD, "dc1.bin")
    for p in (boot, pt, app):
        if not os.path.exists(p):
            sys.exit("缺产物: %s（先跑 firmware/c/build.sh）" % p)

    fp.log("=== [1/6] 识别端口 ===")
    if args.data_port and args.ctrl_port:
        data, ctrl = args.data_port, args.ctrl_port
    else:
        data, ctrl = fp.pick_ports()

    fp.log("=== [2/6] 进下载模式 ===")
    fp.enter_boot(ctrl)
    time.sleep(1.5)                     # 给 ESP 内部 bootloader 起来的时间
    fp.log("  " + fp.ctl_cmd(ctrl, "STATUS", 0.8))

    # 先探测一次；连不上就重进一次下载模式（实测首次偶发 No serial data）
    if fp.esptool(data, ["chip-id"], timeout=60) != 0:
        fp.log("  首次连接失败，重进下载模式")
        fp.enter_boot(ctrl)
        time.sleep(2.0)
        fp.log("  " + fp.ctl_cmd(ctrl, "STATUS", 0.8))

    # 从进下载模式开始就必须保证退出时释放 IO0 —— 否则设备停在 bootloader 里
    # 静默不运行，表现为「烧完 ping 不通、串口 0 字节」。
    try:
        if not args.no_erase:
            fp.log("=== [3/6] 全片擦除 ===")
            if fp.esptool(data, ["erase-flash"], timeout=300) != 0:
                fp.log("erase 失败")
                return 1
        else:
            fp.log("=== [3/6] 跳过擦除（保留 NVS）===")

        fp.log("=== [4/6] 写 3 段 ===")
        fp.log("  bootloader %d B @%s" % (os.path.getsize(boot), OFF_BOOT))
        fp.log("  part-table %d B @%s" % (os.path.getsize(pt), OFF_PT))
        fp.log("  app        %d B @%s" % (os.path.getsize(app), OFF_APP))
        rc = fp.esptool(data, [
            "write-flash",
            OFF_BOOT, boot,
            OFF_PT, pt,
            OFF_APP, app,
        ], timeout=900)
        if rc != 0:
            fp.log("write 失败")
            return 1

        if not args.no_verify:
            fp.log("=== [5/6] 校验 ===")
            rc = fp.esptool(data, [
                "verify-flash",
                OFF_BOOT, boot,
                OFF_PT, pt,
                OFF_APP, app,
            ], timeout=600)
            if rc != 0:
                fp.log("verify 失败")
                return 1
        else:
            fp.log("=== [5/6] 跳过校验 ===")
    finally:
        fp.log("=== [6/6] 复位运行（无论成败都要释放 IO0）===")
        fp.enter_run(ctrl)
        time.sleep(3)

    ok, raw = fp.wait_repl(data, 20)
    txt = raw.decode("utf-8", "replace")
    for line in txt.replace("\r", "").splitlines():
        if line.strip():
            fp.log("  | " + line.strip()[:120])
    fp.log("启动日志 %s" % ("有输出" if txt.strip() else "无输出"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
