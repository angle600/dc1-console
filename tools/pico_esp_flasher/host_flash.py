# 经 Pico MicroPython 桥烧录 ESP8266。
# 不调用 mpremote：它连接时会发 Ctrl-C，把刚起来的 main.py 打死。
import os
import subprocess
import sys
import time

LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_flash_log.txt")
with open(LOG, "w", encoding="utf-8") as f:
    f.write("%s boot\n" % time.strftime("%H:%M:%S"))

import serial
import serial.tools.list_ports

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(HERE, "flash_main.py")
CFG = os.path.join(HERE, "esptool.cfg")
PY = r"C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe"
FW = r"D:\work\phicomm\firmware\micropython-1m-v1.29.0.bin"
MAGIC = b"\x00\xffDC1RUN\xff\x00"
PORT = "COM9"


def log(msg):
    line = "%s %s\n" % (time.strftime("%H:%M:%S"), msg)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line)
        f.flush()
    print(line, end="")


def read_for(ser, secs, until=None):
    ser.timeout = 0.05
    end = time.time() + secs
    buf = b""
    next_beat = time.time() + 1.0
    while time.time() < end:
        n = ser.in_waiting
        chunk = ser.read(n if n else 1)
        if chunk:
            buf += chunk
            if until == b"\x04>" and buf.count(b"\x04") >= 2:
                return buf
            if until and until != b"\x04>" and until in buf:
                return buf
        if time.time() >= next_beat:
            log("wait %dB" % len(buf))
            next_beat = time.time() + 1.0
    return buf


def enter_raw(ser):
    ser.reset_input_buffer()
    ser.write(b"\x03\x03")
    time.sleep(0.3)
    ser.write(b"\x03")
    time.sleep(0.4)
    ser.write(b"\x02")  # friendly REPL
    time.sleep(0.3)
    ser.write(b"\r\n")
    pre = read_for(ser, 0.8)
    log("repl-pre %r" % pre[-180:])
    ser.write(b"\x01")  # raw REPL
    banner = read_for(ser, 1.5)
    log("raw-banner %r" % banner[-180:])
    if b">" not in banner and b"raw REPL" not in banner.lower() and b"Raw REPL" not in banner:
        ser.write(b"\x03\x01")
        banner += read_for(ser, 1.5)
        log("raw-retry %r" % banner[-180:])
    return banner


def raw_exec(ser, code, timeout=4.0):
    log("exec send %d" % len(code))
    ser.write(code.encode("utf-8") + b"\x04")
    buf = read_for(ser, timeout, until=b"\x04>")
    ok = buf.startswith(b"OK") or buf.startswith(b"OK\r")
    log("exec ok=%s len=%d tail=%r" % (ok, len(buf), buf[-160:]))
    if not ok and b"OK" not in buf[:8]:
        raise RuntimeError("raw exec failed: %r" % buf[-300:])
    return buf


def upload_main(ser, data):
    import base64
    raw_exec(ser, "f=open('main.py','wb'); f.close()")
    for i in range(0, len(data), 96):
        chunk = base64.b64encode(data[i:i + 96]).decode()
        raw_exec(
            ser,
            "import ubinascii\n"
            "f=open('main.py','ab')\n"
            "f.write(ubinascii.a2b_base64('%s'))\n"
            "f.close()\n" % chunk,
            timeout=3.0,
        )
    raw_exec(ser, "import os\nprint(os.stat('main.py')[6])")


def wait_port(prefer, secs=20):
    end = time.time() + secs
    while time.time() < end:
        hits = []
        for p in serial.tools.list_ports.comports():
            if p.vid == 0x2E8A or p.device == prefer:
                hits.append(p.device)
        if prefer in hits:
            return prefer
        if hits:
            return hits[0]
        time.sleep(0.3)
    return None


def main():
    open(LOG, "w", encoding="utf-8").write("")
    blob = open(MAIN, "rb").read().replace(b"\r\n", b"\n")
    log("main.py bytes %d fw %d" % (len(blob), os.path.getsize(FW)))
    ser = serial.Serial(PORT, 115200, timeout=0.2)
    log("opened %s" % PORT)
    enter_raw(ser)
    upload_main(ser, blob)
    log("reset pico")
    ser.write(b"import machine\nmachine.reset()\n\x04")
    time.sleep(0.2)
    ser.close()

    log("wait reenumerate")
    time.sleep(1.2)
    port = wait_port(PORT, 20)
    if not port:
        raise SystemExit("Pico COM port did not come back")
    log("port back %s" % port)
    # main.py 自己还要 1 秒完成断电/上电，再留余量
    time.sleep(2.2)

    env = os.environ.copy()
    env["ESPTOOL_CFGFILE"] = CFG
    cmd = [
        PY, "-m", "esptool",
        "--port", port,
        "--baud", "115200",
        "--before", "no-reset",
        "--after", "no-reset",
        "--no-stub",
        "--connect-attempts", "30",
        "write_flash",
        "--flash-mode", "keep",
        "--flash-freq", "keep",
        "--flash-size", "1MB",
        "0x0", FW,
    ]
    log("esptool " + " ".join(cmd))
    with open(LOG, "a", encoding="utf-8") as lf:
        rc = subprocess.run(cmd, stdout=lf, stderr=subprocess.STDOUT, env=env).returncode
    log("esptool rc=%s" % rc)
    if rc != 0:
        raise SystemExit(rc)

    log("enter run mode")
    time.sleep(0.4)
    ser = serial.Serial(port, 115200, timeout=0.2)
    ser.write(MAGIC)
    ser.flush()
    time.sleep(1.6)
    boot = read_for(ser, 8.0)
    ser.close()
    log("boot %d bytes %r" % (len(boot), boot[:400]))
    text = boot.decode("utf-8", "replace")
    if "MicroPython" in text or ">>>" in text:
        log("BOOT OK")
    else:
        log("BOOT UNCONFIRMED")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log("EXC %r" % (e,))
        raise
