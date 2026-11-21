"""USB <-> ESP UART 透传桥（配合 main.py 的 pwr/boot）

用途：MicroPython 版 Pico 固件只有一个 CDC，passthru() 会独占端口导致 esptool
无法同时连接。本脚本先让 ESP 进下载模式，再 os.dupterm(None) 关闭 REPL
独占 USB CDC，把数据双向透传给 ESP —— 此后 esptool 可独占 COM9 正常烧录。

用法（宿主）：
    python -m mpremote connect port:COM9 fs cp bridge.py :bridge.py
    python -m mpremote connect port:COM9 exec "import _thread,bridge; _thread.start_new_thread(bridge.run,(115200,)); print('BRIDGE_STARTED')"
    # 之后 esptool 直接连 COM9（--before no_reset --after no_reset）
    # 烧完：给 Pico 重新上电（或等 secs 超时自动 m.run() 复位 ESP）
"""

import sys
import select
import time
import os
import main as m


def run(rate=115200, secs=300):
    # 1) 设置 UART 波特率并让 ESP 进下载模式（上电 + IO0 低 + EN 脉冲）
    m.baud(rate)
    m.boot()
    u = m._uart

    # 2) 关掉 REPL，确保 esptool 的 SLIP 数据不被 REPL 解析器吃掉
    try:
        os.dupterm(None)
    except Exception:
        pass

    # 3) 双向透传，直到超时
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

    # 4) 超时：退出下载模式，复位运行 ESP
    try:
        m.run()
    except Exception:
        pass
