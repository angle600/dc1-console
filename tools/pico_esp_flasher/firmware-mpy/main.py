"""
pico_esp_flasher - MicroPython 备选实现（零工具链，立即可用）

适用：不想装 pico-sdk/arm-gcc 时。功能比 C 版少两点：
  * 无 DTR/RTS 自动复位（MicroPython 不暴露 modem 状态线）
    -> esptool 必须加 --before no_reset --after no_reset
  * 建议波特率 <= 460800（Python 转发有开销）

用法
  1. 把本文件存成 Pico 上的 main.py，插 USB 自动运行
  2. 打开串口终端（REPL），执行：
         import main            # 若已作为 main.py 自动运行则跳过
         main.info()            # 看当前状态
         main.pwr(1)            # 上 3.3V
         main.boot()            # 进下载模式（IO0 低 + EN 脉冲）
         main.passthru()        # 进入串口直通，此端口变成数据口
  3. 另开一个终端跑 esptool：
         python -m esptool --port COMx --baud 460800 \
             --before no_reset --after no_reset write_flash 0x0 fw.bin
  4. 烧完在直通终端按 Ctrl-C 退出

引脚（同 C 版）
  GP0 -> ESP RX (串 1k)   GP1 <- ESP TX
  GP2 -> Q2 -> ESP GPIO0  GP3 -> Q3 -> ESP EN
  GP4 -> Q1 -> 3.3V 高边开关
"""

import sys
import select

from machine import UART, Pin

# ---------------- 引脚 ----------------
PIN_TX  = 0
PIN_RX  = 1
PIN_IO0 = 2
PIN_EN  = 3
PIN_PWR = 4
PIN_LED = 25

_io0 = Pin(PIN_IO0, Pin.OUT, value=0)   # 1 = 拉低 ESP GPIO0
_en  = Pin(PIN_EN,  Pin.OUT, value=0)   # 1 = 拉低 ESP EN
_pwr = Pin(PIN_PWR, Pin.OUT, value=0)   # 1 = 3.3V 上电
_led = Pin(PIN_LED, Pin.OUT, value=0)

_uart = None
_baud = 115200


def _mk_uart():
    global _uart
    _uart = UART(0, baudrate=_baud, tx=Pin(PIN_TX), rx=Pin(PIN_RX), timeout=0)
    return _uart


_mk_uart()


# ---------------- 控制 ----------------
def pwr(on):
    """3.3V 通断。on=True 上电。"""
    global _uart
    if on:
        # 恢复 TX 输出
        _uart = None
        _mk_uart()
        _pwr.value(1)
        _led.value(1)
    else:
        # 断电前把 TX 拉低并转输入，切断经 ESP RX 的 ESD 倒灌
        try:
            Pin(PIN_TX, Pin.IN)
        except Exception:
            pass
        _uart = None
        _pwr.value(0)
        _led.value(0)
    return _pwr.value()


def io0(low):
    """True = 拉低 ESP GPIO0（下载模式）"""
    _io0.value(1 if low else 0)
    return _io0.value()


def en(low):
    """True = 拉低 ESP EN（保持复位）"""
    _en.value(1 if low else 0)
    return _en.value()


def baud(rate):
    global _baud
    _baud = int(rate)
    _mk_uart()
    return _baud


def boot(delay=120):
    """进串口下载模式"""
    pwr(True)
    io0(True)
    en(True)
    _sleep(delay // 2)
    en(False)
    _sleep(delay)
    return info()


def run(reset=True):
    """退出下载模式并复位运行"""
    pwr(True)
    io0(False)
    if reset:
        en(True)
        _sleep(60)
        en(False)
        _sleep(120)
    return info()


def _sleep(ms):
    import time
    time.sleep_ms(int(ms))


def info():
    d = dict(pwr=_pwr.value(), io0=_io0.value(), en=_en.value(), baud=_baud)
    print("STATUS", d)
    return d


# ---------------- 数据直通 ----------------
def passthru(chunk=256, verbose=True):
    """USB REPL <-> UART0 双向直通。发送单个 0x03（Ctrl-C）退出。

    注意：进入后 REPL 被占用，此时不要用 mpremote 连（会抢端口）。
    退出时直接由上宿主发一个 0x03 字节，本函数拦截该字节、不转发给 ESP。
    """
    global _uart
    if _uart is None:
        _mk_uart()
    poll = select.poll()
    poll.register(sys.stdin, select.POLLIN)
    if verbose:
        print("PASSTHRU baud=%d  (send 0x03 to exit)" % _baud)
    try:
        while True:
            if poll.poll(0):
                data = sys.stdin.buffer.read(chunk)
                if not data:
                    continue
                # 单字节 0x03 = 退出指令，不透传给 ESP
                if data == b"\x03":
                    break
                _uart.write(data)
            if _uart.any():
                d = _uart.read(_uart.any())
                if d:
                    try:
                        sys.stdout.buffer.write(d)
                    except OSError:
                        break
    except KeyboardInterrupt:
        pass
    if verbose:
        print("\nPASSTHRU end")


def boot_and_passthru():
    """一条命令完成：进下载模式 + 直通"""
    boot()
    passthru()


if __name__ == "__main__":
    info()
    print("pwr(1) / boot() / passthru() available")
