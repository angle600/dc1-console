"""USB <-> ESP UART 透传桥（反相 3V3_EN 版）

GP4 --NMOS--> 3V3_EN 为反相：GP4=0 上电，GP4=1 断电。
断电 1s -> IO0 拉低 -> 上电 -> 关闭 REPL 独占 USB CDC -> 双向透传。
"""

import sys
import select
import time
import os
from machine import UART, Pin

PIN_TX, PIN_RX, PIN_IO0, PIN_PWR = 0, 1, 2, 4


def run(rate=115200, secs=900):
    pwr = Pin(PIN_PWR, Pin.OUT, value=0)    # 0=上电（反相）
    io0 = Pin(PIN_IO0, Pin.OUT, value=0)

    # 断电 1s -> IO0 拉低 -> 上电（此刻 IO0 已低，冷启动进下载模式）
    pwr.value(1)
    time.sleep_ms(1000)
    io0.value(1)
    time.sleep_ms(50)
    pwr.value(0)
    time.sleep_ms(400)

    u = UART(0, baudrate=rate, tx=Pin(PIN_TX), rx=Pin(PIN_RX), timeout=0)

    try:
        os.dupterm(None)
    except Exception:
        pass

    p = select.poll()
    p.register(sys.stdin, select.POLLIN)
    t0 = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), t0) < secs * 1000:
        if p.poll(0):
            d = sys.stdin.buffer.read(512)
            if d:
                u.write(d)
        n = u.any()
        if n:
            d = u.read(n)
            if d:
                try:
                    sys.stdout.buffer.write(d)
                except Exception:
                    pass

    # 超时：恢复正常运行（GP4 低=上电，IO0 释放）
    try:
        io0.value(0)
    except Exception:
        pass
