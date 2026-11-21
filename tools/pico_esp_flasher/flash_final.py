# 全片擦除 + 重烧 MicroPython（stub 模式，115200）
import os
import subprocess
import sys
import time

import serial

CTRL = "COM16"
DATA = "COM15"
CFG = r"D:\work\phicomm\tools\pico_esp_flasher\esptool.cfg"
PY = r"C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe"
FW = r"D:\work\phicomm\firmware\micropython-1m-v1.29.0.bin"
LOG = r"D:\work\phicomm\tools\pico_esp_flasher\_flash_final.txt"


def log(msg):
    line = "%s %s\n" % (time.strftime("%H:%M:%S"), msg)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line)
        f.flush()
    print(line, end="")
    sys.stdout.flush()


def open_port(port, baud=115200, retries=15):
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


def ctl(line, wait=0.35):
    s = open_port(CTRL)
    try:
        s.reset_input_buffer()
        s.write((line + "\n").encode())
        s.flush()
        end = time.time() + wait
        buf = b""
        while time.time() < end:
            n = s.in_waiting
            buf += s.read(n if n else 1)
        return buf.decode("utf-8", "replace").strip().replace("\r\n", " | ")
    finally:
        s.close()
        time.sleep(0.1)


def enter_boot():
    ctl("PWR 0")
    time.sleep(0.5)
    ctl("IO0 1")
    ctl("PWR 1")
    time.sleep(0.55)


def enter_run():
    ctl("PWR 0")
    time.sleep(0.5)
    ctl("IO0 0")
    ctl("PWR 1")
    time.sleep(0.55)


def esptool(args, timeout=900):
    env = os.environ.copy()
    env["ESPTOOL_CFGFILE"] = CFG
    cmd = [PY, "-m", "esptool", "--port", DATA, "--baud", "115200",
           "--before", "no-reset", "--after", "no-reset"] + args
    log("CMD " + " ".join(args))
    p = subprocess.run(cmd, capture_output=True, timeout=timeout, env=env)
    txt = (p.stdout + p.stderr).decode("utf-8", "replace")
    for ln in txt.splitlines():
        s = ln.strip()
        if not s or "Loaded custom" in s or "NOTE: Pre-connection" in s:
            continue
        log("  " + s[:170])
    log("rc=%d" % p.returncode)
    return p.returncode


def main():
    open(LOG, "w", encoding="utf-8").write("")
    log("fw %d bytes" % os.path.getsize(FW))
    ctl("AUTO 0", 0.6)

    ok = False
    for i in range(4):
        log("进下载模式 #%d" % (i + 1))
        enter_boot()
        if esptool(["chip-id"], timeout=90) == 0:
            ok = True
            break
    if not ok:
        log("进不了下载模式")
        return 1

    log("=== erase-flash（全片）===")
    if esptool(["erase-flash"], timeout=300) != 0:
        log("erase 仍失败")
        return 2
    log("erase OK")

    log("=== write-flash 0x0 ===")
    if esptool(["write-flash", "--flash-size", "detect", "0x0", FW], timeout=900) != 0:
        log("write 失败")
        return 3

    log("=== verify-flash ===")
    if esptool(["verify-flash", "--flash-size", "detect", "0x0", FW], timeout=900) != 0:
        log("verify 失败")
        return 4
    log("verify OK")

    log("=== 复位运行 ===")
    enter_run()

    log("=== 抓启动 ===")
    data = open_port(DATA)
    try:
        data.reset_input_buffer()
        end = time.time() + 8.0
        buf = b""
        while time.time() < end:
            n = data.in_waiting
            buf += data.read(n if n else 1)
            if b">>>" in buf:
                break
        log("boot %d bytes" % len(buf))
        printout = "".join(chr(c) if 32 <= c < 127 or c in (10, 13) else "."
                           for c in buf)
        for i in range(0, len(printout), 120):
            log("  " + printout[i:i + 120].replace("\r", "").replace("\n", "\\n"))

        data.write(b"\r\n")
        data.flush()
        time.sleep(0.4)
        data.write(b"import sys; print('MPY_OK', sys.implementation.version)\r\n")
        data.flush()
        end = time.time() + 3.0
        r = b""
        while time.time() < end:
            n = data.in_waiting
            r += data.read(n if n else 1)
        log("repl %d bytes %r" % (len(r), r[:300]))
        log("RESULT " + ("OK" if (b"MPY_OK" in r or b">>>" in buf or b"MicroPython" in buf) else "FAIL"))
    finally:
        data.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
