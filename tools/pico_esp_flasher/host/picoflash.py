#!/usr/bin/env python3
"""picoflash - Pico ESP Flasher 上位机封装

把「断电 -> 上电 -> 拉 IO0 -> 释放 EN -> esptool 烧录 -> 复位运行」串成一条命令。
依赖：pyserial、esptool（4.x / 5.x 均兼容）

常用：
  python picoflash.py --list                       # 列出 Pico 的两个 COM 口
  python picoflash.py --info                       # 状态
  python picoflash.py --power off|on|cycle         # 手动控 3.3V
  python picoflash.py --boot                       # 手动进下载模式
  python picoflash.py --flash fw.bin --addr 0x0    # 一键烧录（默认 460800）
  python picoflash.py --flash fw.bin --addr 0x0 -b 921600 --erase
  python picoflash.py --monitor                    # 串口监视（Ctrl-C 退出）
  python picoflash.py --flash fw.bin -p COM7 -c COM8   # 手动指定端口
"""

from __future__ import annotations

import argparse
import sys
import time

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    sys.exit("需要 pyserial:  pip install pyserial")

PICO_VID = 0x2E8A
PICO_PID = 0x1001

OK_PREFIX = ("OK", "ERR", "STATUS", "INFO", "PICO-ESP-FLASHER")


# --------------------------------------------------------------------------
# esptool 4.x 用下划线，5.x 改成连字符
# --------------------------------------------------------------------------
def _esptool_major() -> int:
    try:
        import esptool
        return int(str(esptool.__version__).split(".")[0])
    except Exception:
        return 5


def esptool_enum(name: str) -> str:
    """name 用连字符写法，自动适配 4.x 的下划线"""
    return name if _esptool_major() >= 5 else name.replace("-", "_")


def esptool_sub(name: str) -> str:
    return name if _esptool_major() >= 5 else name.replace("-", "_")


# --------------------------------------------------------------------------
# 控制口
# --------------------------------------------------------------------------
class PicoCtl:
    def __init__(self, port: str, quiet: bool = False):
        self.port = port
        self.quiet = quiet
        # pyserial 的 Serial() 不接受 dtr/rts 构造参数，
        # 必须先建空对象、设属性、再 open()，才能保证打开瞬间线状态是释放的
        self.s = serial.Serial()
        self.s.port = port
        self.s.baudrate = 115200
        self.s.timeout = 0.25
        self.s.write_timeout = 2
        self.s.dtr = False
        self.s.rts = False
        self.s.open()
        time.sleep(0.15)
        self.s.reset_input_buffer()
        self.auto(False)          # 上位机接管，别让 DTR/RTS 捣乱

    # ---- 原始读写 ----
    def _readline(self, timeout: float = 1.0) -> str:
        end = time.time() + timeout
        buf = bytearray()
        while time.time() < end:
            b = self.s.read(1)
            if not b:
                if buf:
                    break
                continue
            if b in (b"\r", b"\n"):
                if buf:
                    break
                continue
            buf += b
        return buf.decode(errors="replace")

    def cmd(self, line: str, timeout: float = 1.5) -> str:
        self.s.reset_input_buffer()
        self.s.write((line + "\n").encode())
        self.s.flush()
        end = time.time() + timeout
        last = ""
        while time.time() < end:
            ln = self._readline(max(0.05, end - time.time()))
            if not ln:
                continue
            last = ln
            if ln.startswith(OK_PREFIX):
                if not self.quiet:
                    print(f"  [{line}] -> {ln}")
                return ln
        if last and not self.quiet:
            print(f"  [{line}] -> {last}")
        return last

    def ok(self, line: str, timeout: float = 1.5) -> bool:
        return self.cmd(line, timeout).startswith("OK")

    # ---- 语义封装 ----
    def auto(self, on: bool) -> bool:
        return self.ok(f"AUTO {1 if on else 0}")

    def power(self, on: bool) -> bool:
        return self.ok(f"PWR {1 if on else 0}")

    def cycle(self, off_ms: int = 400) -> bool:
        self.power(False)
        time.sleep(off_ms / 1000.0)
        return self.power(True)

    def io0(self, low: bool) -> bool:
        return self.ok(f"IO0 {1 if low else 0}")

    def en(self, low: bool) -> bool:
        return self.ok(f"EN {1 if low else 0}")

    def boot(self) -> bool:
        return self.ok("BOOT", timeout=3.0)

    def run(self) -> bool:
        return self.ok("RUN", timeout=3.0)

    def status(self) -> str:
        return self.cmd("STATUS")

    def baud(self, rate: int) -> bool:
        return self.ok(f"BAUD {rate}")

    def close(self) -> None:
        try:
            self.s.close()
        except Exception:
            pass


# --------------------------------------------------------------------------
# 端口发现
# --------------------------------------------------------------------------
def find_ports() -> list[str]:
    hits = [p.device for p in list_ports.comports() if p.vid == PICO_VID]
    return sorted(hits)


def probe(port: str) -> bool:
    """该口是不是我们的控制台"""
    try:
        c = PicoCtl(port, quiet=True)
        r = c.cmd("INFO", timeout=1.0)
        c.close()
        return "pico-esp-flasher" in r.lower()
    except Exception:
        return False


def resolve_ports(args) -> tuple[str, str]:
    """返回 (数据口, 控制口)"""
    if args.port and args.ctrl:
        return args.port, args.ctrl

    cands = find_ports()
    if not cands:
        sys.exit("未找到 Pico（VID=2E8A）。插好 USB，或看 --list 输出后用 -p/-c 手动指定。")

    ctrl = None
    for p in cands:
        if probe(p):
            ctrl = p
            break

    if ctrl is None:
        # 兜底：假设最后一个口是控制台（CDC1 枚举靠后）
        ctrl = args.ctrl or cands[-1]

    data = args.port or next((p for p in cands if p != ctrl), None)
    if data is None:
        sys.exit(f"只找到 {cands}，缺数据口。用 -p/-c 手动指定。")
    return data, ctrl


# --------------------------------------------------------------------------
# 动作
# --------------------------------------------------------------------------
def do_list() -> None:
    print(f"{'DEVICE':<10} {'VID:PID':<12} {'DESC'}")
    for p in list_ports.comports():
        vidpid = f"{p.vid:04X}:{p.pid:04X}" if p.vid is not None else "-"
        mark = "  <== Pico" if p.vid == PICO_VID else ""
        print(f"{p.device:<10} {vidpid:<12} {p.description}{mark}")


def do_info(data: str, ctrl: str) -> None:
    c = PicoCtl(ctrl, quiet=True)
    try:
        print(f"control port : {ctrl}")
        print(f"data port    : {data}")
        print(f"status       : {c.status()}")
    finally:
        c.close()


def do_power(ctrl: str, action: str) -> None:
    c = PicoCtl(ctrl)
    try:
        if action == "on":
            print(c.cmd("PWR 1"))
        elif action == "off":
            print(c.cmd("PWR 0"))
        else:
            print("power off ...")
            print(c.cmd("PWR 0"))
            time.sleep(0.4)
            print("power on ...")
            print(c.cmd("PWR 1"))
        time.sleep(0.1)
        print(c.cmd("STATUS"))
    finally:
        c.close()


def do_boot(ctrl: str) -> None:
    c = PicoCtl(ctrl)
    try:
        print(c.cmd("BOOT", timeout=3.0))
    finally:
        c.close()


def do_run(ctrl: str) -> None:
    c = PicoCtl(ctrl)
    try:
        print(c.cmd("RUN", timeout=3.0))
    finally:
        c.close()


def do_flash(data: str, ctrl: str, args) -> int:
    import subprocess

    c = PicoCtl(ctrl)
    rc = 1
    try:
        print("[1/5] 接管控制权 (AUTO 0)")
        c.auto(False)

        print("[2/5] 断电 -> 上电")
        c.power(False)
        time.sleep(0.35)
        c.power(True)
        time.sleep(0.15)

        print("[3/5] 进入串口下载模式 (IO0=L, EN pulse)")
        if not c.boot():
            print("  警告: BOOT 未回 OK，继续尝试 esptool")

        cmd = [
            sys.executable, "-m", "esptool",
            "--chip", args.chip,
            "--port", data,
            "--baud", str(args.baud),
            "--before", esptool_enum("no-reset"),
            "--after", esptool_enum("no-reset"),
        ]
        if args.no_stub:
            cmd.append("--no-stub")

        if args.erase:
            print("[4/5] erase-flash")
            rc = subprocess.call(cmd + [esptool_sub("erase-flash")])
            if rc != 0:
                raise RuntimeError("erase 失败")

        print(f"[4/5] write-flash {args.flash} @ {args.addr} @ {args.baud}")
        wcmd = cmd + [esptool_sub("write-flash")]
        if args.verify:
            wcmd.append("--verify")
        wcmd += ["-z", str(args.addr), args.flash]

        t0 = time.time()
        rc = subprocess.call(wcmd)
        dt = time.time() - t0
        if rc != 0:
            raise RuntimeError(f"esptool 返回 {rc}")

        print(f"[5/5] 完成，用时 {dt:.1f}s，复位运行")
        c.run()

        print("      " + c.status())
        return 0
    except Exception as e:
        print(f"失败: {e}")
        print("      设备可能停在复位态，尝试恢复 ...")
        try:
            c.io0(False)
            c.en(False)
            c.power(True)
        except Exception:
            pass
        return rc or 1
    finally:
        try:
            c.auto(True)
        except Exception:
            pass
        c.close()


def do_monitor(data: str, baud: int) -> None:
    print(f"monitor {data} @ {baud}  (Ctrl-C 退出)")
    s = serial.Serial()
    s.port = data
    s.baudrate = baud
    s.timeout = 0.05
    s.dtr = False
    s.rts = False
    s.open()
    try:
        while True:
            chunk = s.read(256)
            if chunk:
                sys.stdout.write(chunk.decode(errors="replace"))
                sys.stdout.flush()
    except KeyboardInterrupt:
        print("\n-- end --")
    finally:
        s.close()


# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(
        description="Pico ESP Flasher 上位机",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("-p", "--port", help="数据口 COM（透传给 ESP）")
    ap.add_argument("-c", "--ctrl", help="控制口 COM")
    ap.add_argument("-b", "--baud", type=int, default=115200,
                    help="烧录波特率，默认 115200（DC1/TC1 模组实测用这个）")
    ap.add_argument("--list", action="store_true", help="列出串口")
    ap.add_argument("--info", action="store_true", help="显示状态")
    ap.add_argument("--power", choices=["on", "off", "cycle"], help="3.3V 控制")
    ap.add_argument("--boot", action="store_true", help="进入下载模式")
    ap.add_argument("--run", action="store_true", help="复位运行")
    ap.add_argument("--flash", help="要烧录的 .bin")
    ap.add_argument("--addr", default="0x0", help="烧录起始地址，默认 0x0")
    ap.add_argument("--chip", default="auto", help="芯片型号，默认 auto")
    ap.add_argument("--erase", action="store_true", help="先整片擦除")
    ap.add_argument("--verify", action="store_true", help="烧后校验")
    ap.add_argument("--no-stub", action="store_true", help="不使用 flasher stub")
    ap.add_argument("--monitor", action="store_true", help="串口监视")

    args = ap.parse_args()

    if args.list:
        do_list()
        return 0

    if args.monitor:
        data, ctrl = ("", "") if not args.port else (args.port, "")
        if not data:
            cands = find_ports()
            if not cands:
                sys.exit("未找到 Pico，用 -p 指定数据口")
            data = cands[0]
        do_monitor(data, args.baud)
        return 0

    data, ctrl = resolve_ports(args)

    if args.info:
        do_info(data, ctrl)
        return 0

    if args.power:
        do_power(ctrl, args.power)
        return 0

    if args.boot:
        do_boot(ctrl)
        return 0

    if args.run:
        do_run(ctrl)
        return 0

    if args.flash:
        return do_flash(data, ctrl, args)

    print(f"数据口 {data} / 控制口 {ctrl}")
    print("没有动作。用 --help 看用法。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
