"""BL0937 单相有功功率计量（只用 CF 脉冲，TC1 出厂就只贴了这一路）

硬件链路（A2 手绘原理图 09-*.png）：
    火线串 1mΩ 采样电阻 → BL0937(U4) → CF 脉冲 → EL1019 光耦(U3) → 主控 GPIO
    CF1/SEL（电压/电流）支路出厂未贴装 → 拿不到 V/A，只有功率

换算关系：
    CF 频率 f(Hz) 正比于有功功率 P(W)：  P = 1000 × f / K
    K = 脉冲常数（imp/kWh），出厂典型 1600，**必须单点标定**。

为什么用"数窗口脉冲"而不是 machine.time_pulse_us()：
    CF 在轻载时可能几十毫秒才一个脉冲，测单周期抖动大；数 1 秒窗口的
    脉冲数再折算频率，低压小功率下线性更好，也不需要独占定时器。
"""

import time
from machine import Pin

from board import CF_GPIO

DEFAULT_K = 1600.0      # imp/kWh，出厂估计值，需标定
WINDOW_MS = 1000


class BL0937:
    def __init__(self, gpio=CF_GPIO, k_imp=DEFAULT_K, window_ms=WINDOW_MS,
                 alpha=0.5):
        self.k = float(k_imp)
        self.window_ms = window_ms
        self.alpha = alpha
        self.total = 0          # 累计脉冲（不掉电清零，仅内存）
        self.win = 0            # 当前窗口脉冲
        self.power = 0.0        # 平滑后的功率 W
        self.freq = 0.0         # 最近窗口频率 Hz
        self.updated = False
        self._t = time.ticks_ms()
        self.pin = None
        try:
            self.pin = Pin(gpio, Pin.IN, Pin.PULL_UP)
            self.pin.irq(trigger=Pin.IRQ_FALLING, handler=self._isr)
        except Exception:
            self.pin = None

    def _isr(self, _p):
        self.win += 1
        self.total += 1

    # -------------------------------------------------- 采样
    def poll(self):
        """每 WINDOW_MS 结算一次。返回 True 表示本次有新功率值。"""
        now = time.ticks_ms()
        dt = time.ticks_diff(now, self._t)
        if dt < self.window_ms:
            return False
        n = self.win
        self.win = 0
        self._t = now
        f = n * 1000.0 / dt
        self.freq = f
        w = 1000.0 * f / self.k
        if self.power <= 0.0 and w < 5.0:
            self.power = w                 # 待机：直接跟，不拖尾巴
        else:
            self.power = self.power * (1.0 - self.alpha) + w * self.alpha
        self.updated = True
        return True

    # -------------------------------------------------- 标定
    def cal_by_ref(self, w_ref):
        """用外部功率计读数标定脉冲常数 K = 1000·f / P_ref。

        注意：必须在**稳定负载**下调用，且窗口已结算过至少一次；
        待机（f≈0）或功率 < 5W 时拒绝，否则 K 会被算成天文数字。
        """
        w_ref = float(w_ref)
        if w_ref < 5.0:
            raise ValueError("power too low")
        if self.freq <= 1.0:
            raise ValueError("no pulse")
        self.k = 1000.0 * self.freq / w_ref
        self.power = w_ref
        return self.k

    # -------------------------------------------------- 输出
    def state(self):
        return {"voltage": 0, "current": 0,
                "power": round(self.power, 1),
                "p_raw": round(self.freq, 2),     # CF 频率 Hz
                "k_imp": round(self.k, 2),
                "pulses": self.total,
                "calibrated": 1 if self.k != DEFAULT_K else 0}
