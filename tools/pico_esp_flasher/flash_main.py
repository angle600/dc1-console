# 烧录桥。参数不要改：
# 波特率 115200
# 烧录: GP4 高(断电) 0.5s -> IO0 拉低 -> GP4 低(上电) 0.5s
# 运行: GP4 高(断电) 0.5s -> IO0 拉高 -> GP4 低(上电) 0.5s
# GP2 直连 IO0。GP4 经 NMOS 反相接 3V3_EN：GP4=1 断电，GP4=0 上电。
# 板上没有 CH_PD/EN，不要去控那根线。
import _thread
import micropython
import sys
import time
from machine import UART, Pin

micropython.kbd_intr(-1)

MAGIC = b"\x00\xffDC1RUN\xff\x00"
pwr = None
io0 = None
u = None
stop = False


def enter_download():
    global pwr, io0, u
    u = None
    Pin(0, Pin.IN)                       # 释放 TX，避免经 ESP RX 倒灌
    pwr = Pin(4, Pin.OUT, value=1)       # 断电
    time.sleep_ms(500)
    io0 = Pin(2, Pin.OUT, value=0)       # IO0 低
    pwr.value(0)                         # 上电 -> 下载模式
    time.sleep_ms(80)
    u = UART(0, baudrate=115200, tx=Pin(0), rx=Pin(1), timeout=0, rxbuf=1024)
    time.sleep_ms(420)


def enter_run():
    global u
    u = None
    time.sleep_ms(20)
    try:
        Pin(0, Pin.IN)
    except Exception:
        pass
    pwr.value(1)                         # 断电
    time.sleep_ms(500)
    io0.value(1)                         # IO0 拉高
    pwr.value(0)                         # 上电 -> 运行
    time.sleep_ms(80)
    u = UART(0, baudrate=115200, tx=Pin(0), rx=Pin(1), timeout=0, rxbuf=1024)
    time.sleep_ms(420)


def uart_thread():
    while not stop:
        uu = u
        if uu is None:
            time.sleep_ms(5)
            continue
        try:
            n = uu.any()
            if n:
                d = uu.read(n)
                if d:
                    sys.stdout.buffer.write(d)
            else:
                time.sleep_ms(1)
        except Exception:
            time.sleep_ms(5)


enter_download()
_thread.start_new_thread(uart_thread, ())
win = bytearray()
while True:
    b = sys.stdin.buffer.read(1)
    if not b:
        continue
    if u is not None:
        u.write(b)
    win += b
    if len(win) > 24:
        del win[:-24]
    if MAGIC in win:
        enter_run()
        micropython.kbd_intr(3)
        while True:
            time.sleep_ms(200)
