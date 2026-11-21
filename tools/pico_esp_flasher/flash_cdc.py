# 烧录 ESP8266（Pico C 版 3 路 CDC 固件）
# 数据口 COM15 = CH0 GP0/GP1；控制口 COM16
# 时序（主人规定，波特率 115200）：
#   下载: PWR 断开 -> 0.5s -> IO0 拉低 -> PWR 上电 -> 0.55s
#   运行: PWR 断开 -> 0.5s -> IO0 拉高 -> PWR 上电 -> 0.55s
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
LOG = r"D:\work\phicomm\tools\pico_esp_flasher\_flash_c.txt"


def log(msg):
    line = "%s %s\n" % (time.strftime("%H:%M:%S"), msg)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line)
        f.flush()
    print(line, end="")
    sys.stdout.flush()


def open_port(port, retries=12):
    last = None
    for _ in range(retries):
        try:
            s = serial.Serial()
            s.port = port
            s.baudrate = 115200
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
           "--before", "no-reset", "--after", "no-reset", "--no-stub"] + args
    log("CMD esptool " + " ".join(args))
    with open(LOG, "a", encoding="utf-8") as lf:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, env=env,
                             universal_newlines=True, bufsize=1)
        for line in p.stdout:
            line = line.rstrip("\r\n")
            if line and not line.startswith("Writing at"):
                lf.write("    " + line + "\n")
                lf.flush()
        rc = p.wait(timeout=timeout)
    log("rc=%d" % rc)
    return rc


def main():
    open(LOG, "w", encoding="utf-8").write("")
    log("fw %d bytes" % os.path.getsize(FW))
    log("STATUS " + ctl("STATUS", 0.9))
    ctl("AUTO 0", 0.6)

    ok = False
    for attempt in range(1, 4):
        log("=== 进下载模式 #%d ===" % attempt)
        enter_boot()
        log("  " + ctl("STATUS", 0.9))
        if esptool(["--connect-attempts", "2", "chip-id"], timeout=120) == 0:
            ok = True
            break
    if not ok:
        log("进下载模式失败")
        return 1

    log("=== erase-flash ===")
    if esptool(["--connect-attempts", "3", "erase-flash"], timeout=300) != 0:
        log("erase 失败，改用覆盖写")
    log("=== write-flash 0x0 ===")
    rc = esptool(["write-flash", "--flash-size", "detect", "0x0", FW], timeout=900)
    if rc != 0:
        log("write 失败")
        return 3

    log("=== 复位运行 ===")
    enter_run()
    log("STATUS " + ctl("STATUS", 0.9))

    log("=== 读启动日志 ===")
    s = open_port(DATA)
    try:
        s.reset_input_buffer()
        end = time.time() + 9.0
        buf = b""
        while time.time() < end:
            n = s.in_waiting
            buf += s.read(n if n else 1)
        log("boot %d bytes %r" % (len(buf), buf[:300]))
        log("BOOT OK" if (b"MicroPython" in buf or b">>>" in buf) else "BOOT UNCONFIRMED")
    finally:
        s.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
