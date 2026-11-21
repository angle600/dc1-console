"""CSE7766 计量扩展（按需加载）

设计目的：ESP8266 只有 ~40KB 堆，app.mpy 越大越容易 OOM（实测 >16.6KB 后
连 HTTP POST 都处理不了）。本模块把 UART0 + CSE7766 + Energy 全部搬出 app.py，
app.py 仅在 meter_enabled=true 时 import 它；禁用时完全不加载，
把宝贵的 RAM/体积留给核心功能（开关/定时/网络/OTA）。

对外接口（app.py 只依赖这几个）：
    MeterExt(cfg)      构造：建 UART0(tx=15,rx=13,4800 8E1) + CSE7766 + Energy
    .gains(cfg["meter"])  写入增益/校准系数
    .poll(dt_ms)       读取一帧并累加电量，返回是否有更新
    .state()           最近一帧原始计量字典
    .energy            store.Energy 实例（.state() 读累计 kWh）
"""

from machine import UART
from cse7766 import CSE7766
from store import Energy


class MeterExt:
    def __init__(self, cfg):
        m = cfg.get("meter") or {}
        self.uart = UART(0, baudrate=4800, bits=8, parity=0, stop=1,
                         tx=15, rx=13, timeout=200)
        self.dev = CSE7766(self.uart,
                           v1r=float(m.get("v1r", 1.0)),
                           v2r=float(m.get("v2r", 1.0)))
        self.energy = Energy()
        self.gains(m)

    def gains(self, m):
        d = self.dev
        d.v_gain = float(m.get("v_gain", 1.0))
        d.i_gain = float(m.get("i_gain", 1.0))
        d.p_gain = float(m.get("p_gain", 1.0))
        d.v1r = float(m.get("v1r", 1.0))
        d.v2r = float(m.get("v2r", 1.0))

    def poll(self, dt):
        if self.dev.poll():
            self.energy.add(self.dev.last.get("power", 0), dt)
            return True
        return False

    def state(self):
        return self.dev.state()
