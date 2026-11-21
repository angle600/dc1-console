"""USB <-> ESP UART 透传桥 v3（不 dupterm，靠主线程独占）

原理：MicroPython 的 REPL 只在解释器"空闲等待输入"时才读 stdin。
主线程跑本函数的死循环期间，REPL 无法介入，stdin 数据全部由本循环读取，
因此无需 os.dupterm(None)（那个会连 USB 输出一起禁掉，导致 esptool 收不到响应）。

GP4 --NMOS--> 3V3_EN 反相：GP4=0 上电，GP4=1 断电。
"""

import sys
import select
import time
from machine import UART, Pin

PIN_TX, PIN_RX, PIN_IO0, PIN_PWR = 0, 1, 2, 4


def power_cycle_and_boot():
    pwr = Pin(PIN_PWR, Pin.OUT, value=0)
    io0 = Pin(PIN_IO0, Pin.OUT, value=0)
    pwr.value(1)          # 断电（反相）
    time.sleep_ms(1000)
    io0.value(1)          # IO0 拉低 -> 下载模式
    time.sleep_ms(50)
    pwr.value(0)          # 上电
    time.sleep_ms(400)
    return pwr, io0


def run(rate=115200, secs=900):
    pwr, io0 = power_cycle_and_boot()
    u = UART(0, baudrate=rate, tx=Pin(PIN_TX), rx=Pin(PIN_RX), timeout=0)
    p = select.poll()
    p.register(sys.stdin, select.POLLIN)
    t0 = time.ticks_ms()
    n_in = n_out = 0
    while time.ticks_diff(time.ticks_ms(), t0) < secs * 1000:
        if p.poll(0):
            d = sys.stdin.buffer.read(512)
            if d:
                u.write(d)
                n_in += len(d)
        k = u.any()
        if k:
            d = u.read(k)
            if d:
                sys.stdout.buffer.write(d)
                n_out += len(d)
    # 结束时复位 ESP 运行
    try:
        io0.value(0)
        pwr.value(1)
        time.sleep_ms(100)
        pwr.value(0)
    except Exception:
        pass
    return n_in, n_out
