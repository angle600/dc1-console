#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DC1 出厂烧录：一键完成「烧 MicroPython 内核 + 部署应用文件 + 验证启动」

前置
  1. Pico 已烧 3 路 CDC 版固件（VID 2E8A / PID 1001）
  2. 接线：GP0->ESP RX、GP1<-ESP TX、GP2->IO0、GP4--NMOS-->3V3_EN（反相）
  3. 板上无 CH_PD/EN，复位只靠 IO0 + 3.3V 通断

用法
  python tools/factory_program.py                       # 全流程（擦除+烧内核+部署+验证）
  python tools/factory_program.py --skip-flash           # 只部署应用文件
  python tools/factory_program.py --factory              # 出厂配置：空 SSID，首次上电进 AP 配网
  python tools/factory_program.py --no-verify            # 跳过联网验证
  python tools/factory_program.py --list                 # 只列端口

流程
  [1] 自动识别控制口 / 数据口
  [2] 进下载模式（断电0.5s -> IO0拉低 -> 上电0.55s）
  [3] erase-flash 全片擦除（必须！否则旧 main.py 会跑起来）
  [4] write-flash micropython-1m-*.bin @ 0x0（stub 模式，115200）
  [5] verify-flash
  [6] 复位运行，等 REPL
  [7] mpremote 部署 app.mpy / 依赖模块 / index.html / config.json / main.py
  [8] 复位，等 WiFi，/api/ping 验证
"""

import argparse
import glob
import json
import os
import subprocess
import sys
import time

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    sys.exit("需要 pyserial:  pip install pyserial")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = sys.executable
PICO_VID = 0x2E8A
PICO_PID = 0x1001
BAUD = 115200

FW_DEFAULT = os.path.join(ROOT, "firmware", "micropython-1m-v1.29.0.bin")
MPY_DIR = os.path.join(ROOT, "firmware", "build", "mpy")
APP_DIR = os.path.join(ROOT, "firmware", "micropython")
CFG_LOCAL = os.path.join(APP_DIR, "config.json")

# 部署清单：顺序有讲究 —— main.py 必须最后，否则中途 soft reset 会拉起不完整的 app
DEPLOY = [
    (os.path.join(MPY_DIR, "cat9554.mpy"), "cat9554.mpy"),
    (os.path.join(MPY_DIR, "store.mpy"), "store.mpy"),
    (os.path.join(MPY_DIR, "webui.mpy"), "webui.mpy"),
    (os.path.join(MPY_DIR, "meter_ext.mpy"), "meter_ext.mpy"),
    (os.path.join(MPY_DIR, "cse7766.mpy"), "cse7766.mpy"),
    (os.path.join(MPY_DIR, "app.mpy"), "app.mpy"),
    (os.path.join(APP_DIR, "index.html"), "index.html"),
    (os.path.join(APP_DIR, "main.py"), "main.py"),
]


def log(msg):
    print("%s %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def open_port(port, retries=10, baud=BAUD):
    last = None
    for _ in range(retries):
        try:
            s = serial.Serial()
            s.port = port
            s.baudrate = baud
            s.timeout = 0.05
            s.write_timeout = 2
            s.dtr = False
            s.rts = False
            s.open()
            time.sleep(0.12)
            return s
        except Exception as e:
            last = e
            time.sleep(0.6)
    raise last


def pick_ports():
    """返回 (数据口, 控制口)。控制口靠 INFO 应答识别，数据口靠 CH0 tx 计数确认。"""
    cands = [p.device for p in serial.tools.list_ports.comports()
             if p.vid == PICO_VID]
    if not cands:
        raise SystemExit("未找到 Pico（VID=2E8A）。检查 USB 与固件。")
    log("发现 Pico 串口: %s" % ", ".join(sorted(cands)))

    ctrl = None
    for p in sorted(cands):
        try:
            s = open_port(p, retries=2)
        except Exception:
            continue
        try:
            s.reset_input_buffer()
            s.write(b"INFO\n")
            s.flush()
            end = time.time() + 1.0
            buf = b""
            while time.time() < end:
                n = s.in_waiting
                buf += s.read(n if n else 1)
        finally:
            s.close()
        if b"pico-esp-flasher" in buf.lower():
            ctrl = p
            log("控制口 = %s" % p)
            break
    if not ctrl:
        raise SystemExit("没找到控制口（INFO 无应答）")

    others = [p for p in sorted(cands) if p != ctrl]

    def ch0_tx():
        s = open_port(ctrl, retries=3)
        try:
            s.reset_input_buffer()
            s.write(b"STATUS\n")
            s.flush()
            end = time.time() + 1.0
            buf = b""
            while time.time() < end:
                n = s.in_waiting
                buf += s.read(n if n else 1)
        finally:
            s.close()
        txt = buf.decode("utf-8", "replace")
        for line in txt.splitlines():
            if "CH0" in line:
                for tok in line.split():
                    if tok.startswith("tx="):
                        return int(tok[3:])
        return -1

    data = None
    for p in others:
        before = ch0_tx()
        try:
            s = open_port(p, retries=2)
            s.write(b"\x00CHPROBE\x00")
            s.flush()
            time.sleep(0.25)
            s.close()
        except Exception:
            continue
        after = ch0_tx()
        if before >= 0 and after > before:
            data = p
            log("数据口 = %s（CH0 tx %d -> %d）" % (p, before, after))
            break
    if not data:
        data = others[0] if others else None
        log("数据口按顺序推断 = %s（tx 计数未变化）" % data)
    return data, ctrl


def ctl_cmd(ctrl, line, wait=0.5):
    s = open_port(ctrl)
    try:
        s.reset_input_buffer()
        s.write((line + "\n").encode())
        s.flush()
        end = time.time() + wait
        buf = b""
        while time.time() < end:
            n = s.in_waiting
            buf += s.read(n if n else 1)
        return buf.decode("utf-8", "replace").strip()
    finally:
        s.close()
        time.sleep(0.1)


def enter_boot(ctrl):
    """断电 0.5s -> IO0 拉低 -> 上电 0.5s"""
    ctl_cmd(ctrl, "AUTO 0", 0.4)
    ctl_cmd(ctrl, "PWR 0", 0.4)
    time.sleep(0.5)
    ctl_cmd(ctrl, "IO0 1", 0.4)
    ctl_cmd(ctrl, "PWR 1", 0.4)
    time.sleep(0.55)


def enter_run(ctrl):
    """断电 0.5s -> IO0 释放 -> 上电 0.5s"""
    ctl_cmd(ctrl, "PWR 0", 0.4)
    time.sleep(0.5)
    ctl_cmd(ctrl, "IO0 0", 0.4)
    ctl_cmd(ctrl, "PWR 1", 0.4)
    time.sleep(0.55)


def esptool(data, args, timeout=900):
    env = os.environ.copy()
    cfg = os.path.join(HERE, "pico_esp_flasher", "esptool.cfg")
    if os.path.exists(cfg):
        env["ESPTOOL_CFGFILE"] = cfg
    cmd = [PY, "-m", "esptool", "--port", data, "--baud", str(BAUD),
           "--before", "no-reset", "--after", "no-reset"] + args
    log("esptool " + " ".join(args))
    p = subprocess.run(cmd, capture_output=True, timeout=timeout, env=env)
    out = (p.stdout + p.stderr).decode("utf-8", "replace")
    for ln in out.splitlines():
        s = ln.strip()
        if s and "Loaded custom" not in s and "NOTE: Pre-connection" not in s:
            print("    " + s[:150])
    return p.returncode


def wait_repl(data, secs=25):
    """等 MicroPython REPL 出现 >>>"""
    end = time.time() + secs
    s = open_port(data)
    try:
        buf = b""
        while time.time() < end:
            n = s.in_waiting
            if n:
                buf += s.read(n)
                if b"MicroPython" in buf or b">>>" in buf:
                    return True, buf
            else:
                time.sleep(0.05)
            if int((end - time.time()) * 4) % 8 == 0:
                s.write(b"\r\n")
                s.flush()
        return False, buf
    finally:
        s.close()


def mpremote(data, *cmds):
    cmd = [PY, "-m", "mpremote", "connect", data] + list(cmds)
    p = subprocess.run(cmd, capture_output=True, timeout=600)
    out = (p.stdout + p.stderr).decode("utf-8", "replace").strip()
    if p.returncode != 0:
        log("  mpremote 失败 rc=%d %s" % (p.returncode, out[:300]))
    return p.returncode, out


def deploy(data, factory_cfg):
    log("部署应用文件")
    pairs = []
    for src, dst in DEPLOY:
        if not os.path.exists(src):
            raise SystemExit("缺文件: %s" % src)
        pairs.append((src, dst))

    # config.json：factory 模式用空 SSID（首次上电进 AP 配网）
    if factory_cfg:
        cfg = json.loads(open(CFG_LOCAL, encoding="utf-8").read())
        cfg["ssid"] = ""
        cfg["password"] = ""
        tmp = os.path.join(HERE, "_factory_config.json")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=1)
        pairs.insert(len(pairs) - 1, (tmp, "config.json"))
        log("  使用出厂配置（空 SSID，首次上电进 AP）")
    else:
        pairs.insert(len(pairs) - 1, (CFG_LOCAL, "config.json"))

    # 先传除 main.py 外的所有文件
    cmds = []
    for src, dst in pairs:
        if dst == "main.py":
            continue
        if cmds:
            cmds.append("+")
        cmds += ["fs", "cp", src, ":" + dst]
    rc, out = mpremote(data, *cmds)
    if rc != 0:
        return False

    # 最后传 main.py
    rc, out = mpremote(data, "fs", "cp", os.path.join(APP_DIR, "main.py"), ":main.py")
    if rc != 0:
        return False

    rc, out = mpremote(data, "fs", "ls")
    log("  设备文件: %s" % " ".join(out.split()))
    return True


def verify(host_token, host, secs=60):
    """等设备联网并应答 /api/ping"""
    import urllib.request
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    url = "http://%s/api/ping" % host
    end = time.time() + secs
    while time.time() < end:
        try:
            with opener.open(url, timeout=5) as r:
                if r.status == 200:
                    return True, r.read().decode()
        except Exception:
            pass
        time.sleep(3)
    return False, ""


def main():
    ap = argparse.ArgumentParser(description="DC1 出厂烧录")
    ap.add_argument("--fw", default=FW_DEFAULT)
    ap.add_argument("--data-port")
    ap.add_argument("--ctrl-port")
    ap.add_argument("--skip-flash", action="store_true", help="跳过擦除+烧内核")
    ap.add_argument("--factory", action="store_true", help="出厂配置（空 SSID）")
    ap.add_argument("--no-verify", action="store_true")
    ap.add_argument("--host", default="192.168.31.243", help="验证用 IP（STA 模式）")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    if args.list:
        for p in serial.tools.list_ports.comports():
            vp = "%04X:%04X" % (p.vid, p.pid) if p.vid else "-"
            print("%-8s %-10s %s" % (p.device, vp, p.description))
        return 0

    log("=== [1/8] 识别端口 ===")
    if args.data_port and args.ctrl_port:
        data, ctrl = args.data_port, args.ctrl_port
        log("使用指定端口 data=%s ctrl=%s" % (data, ctrl))
    else:
        data, ctrl = pick_ports()

    if not args.skip_flash:
        log("=== [2/8] 进下载模式 ===")
        enter_boot(ctrl)
        log("  " + ctl_cmd(ctrl, "STATUS", 0.8))

        if not os.path.exists(args.fw):
            raise SystemExit("固件不存在: %s" % args.fw)
        log("=== [3/8] 全片擦除（必须）===")
        if esptool(data, ["erase-flash"], timeout=300) != 0:
            raise SystemExit("erase 失败")

        log("=== [4/8] 写内核 %d B ===" % os.path.getsize(args.fw))
        if esptool(data, ["write-flash", "--flash-size", "detect",
                          "0x0", args.fw]) != 0:
            raise SystemExit("write 失败")

        log("=== [5/8] 校验 ===")
        if esptool(data, ["verify-flash", "--flash-size", "detect",
                          "0x0", args.fw]) != 0:
            raise SystemExit("verify 失败")

    log("=== [6/8] 复位运行 ===")
    enter_run(ctrl)
    ok, raw = wait_repl(data, 25)
    log("  REPL %s" % ("OK" if ok else "未出现"))

    log("=== [7/8] 部署应用 ===")
    if not deploy(data, args.factory):
        raise SystemExit("部署失败")

    log("=== [8/8] 复位并验证 ===")
    enter_run(ctrl)
    if args.no_verify:
        log("  已跳过验证")
        return 0
    if args.factory:
        log("  出厂配置为空 SSID：设备应进入 AP 模式 DC1-Setup / 192.168.4.1")
        log("  请连热点后访问 http://192.168.4.1 配网")
        return 0
    ok, body = verify(None, args.host, 90)
    log("  ping %s %s" % ("OK" if ok else "FAIL", body))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
