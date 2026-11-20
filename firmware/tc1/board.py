"""TC1 换脑板（ESP-12S / ESP-12F）板级抽象

原厂 A1 = MXCHIP EMW3031（MiCO）、A2 = HF-LPT230（汉枫固件），两者都跑不了
MicroPython。本文件对应"拆掉原模组 + 焊 ESP-12S 转接板"的接线定义，
接线表见 docs/08-TC1方案.md 第 3 节。

引脚分配原则：**避开 ESP8266 的三个启动 strap**
  * GPIO0 低 → 进下载模式
  * GPIO2 低 → 启动失败
  * GPIO15 高 → 启动失败
所以 6 路继电器只用 5/4/14/12/13/16；GPIO2 只驱动 LED（低电平点亮，
外部上拉保证启动时为高）；GPIO3(RX) 作 CF 脉冲输入（运行时不需要串口收）。

继电器驱动电平：转接板上一般用 NPN/ULN2003，**高电平吸合**；若你的板子是
光耦低有效驱动，把 RELAY_ACTIVE_LOW 改成 True 即可（极性只在这一处）。
"""

import time
from machine import Pin, ADC

# ------------------------------------------------------------------ 引脚
RELAY_GPIO = (5, 4, 14, 12, 13, 16)   # 插孔 1..6 → D1 D2 D5 D6 D7 D0
RELAY_ACTIVE_LOW = False              # False=高电平吸合；低有效驱动改 True
KEY_ADC_PIN = 0                       # ADC0(TOUT)：按键分压，按下 < 1.0V
KEY_PRESS_MAX = 300                   # ADC 0..1023，低于此值判定按下
LED_GPIO = 2                          # 低电平点亮
CF_GPIO = 3                           # BL0937 CF 功率脉冲（RX，下降沿）

LONG_PRESS_MS = 3000
DEBOUNCE_MS = 60


class Relays:
    """6 路继电器。低电平/高电平极性由 RELAY_ACTIVE_LOW 统一决定。"""

    def __init__(self, gpio=RELAY_GPIO, active_low=RELAY_ACTIVE_LOW):
        self.off = 1 if active_low else 0
        self.on = 0 if active_low else 1
        self.pins = []
        # 先全部写成"断"再配置为输出，避免上电瞬间按默认电平误吸合
        for g in gpio:
            try:
                self.pins.append(Pin(g, Pin.OUT, value=self.off))
            except Exception:
                self.pins.append(None)
        self.state = [False] * len(self.pins)

    def __len__(self):
        return len(self.pins)

    def write(self, idx, on):
        """idx: 0..5；on: bool。返回是否真正写成功。"""
        p = self.pins[idx]
        self.state[idx] = bool(on)
        if p is None:
            return False
        try:
            p.value(self.on if on else self.off)
            return True
        except Exception:
            return False

    def all(self, on):
        for i in range(len(self.pins)):
            self.write(i, on)

    def snapshot(self):
        return list(self.state)

    def count_on(self):
        return sum(1 for s in self.state if s)


class Led:
    """面板 LED，低电平点亮。"""

    def __init__(self, gpio=LED_GPIO):
        try:
            self.pin = Pin(gpio, Pin.OUT, value=1)      # 上电灭
        except Exception:
            self.pin = None
        self.on_state = False
        self._t = 0

    def set(self, on):
        self.on_state = bool(on)
        if self.pin is not None:
            try:
                self.pin.value(0 if on else 1)
            except Exception:
                pass

    def toggle(self):
        self.set(not self.on_state)

    def blink_tick(self, now, period_ms=1000):
        """未联网时慢闪；已联网由调用方保持常亮。"""
        if time.ticks_diff(now, self._t) >= period_ms:
            self._t = now
            self.toggle()


class Key:
    """单按键（ADC 分压读取）。

    返回事件：
      "short" —— 松开且按下时长 < LONG_PRESS_MS
      "long"  —— 按住满 LONG_PRESS_MS（只报一次，松手不重复）
    用 ADC 而不是 GPIO 的原因：ESP8266 上能空出来的脚里没有"既不占 strap
    又能中断"的，ADC0 只做输入，最安全。
    """

    def __init__(self, pin=KEY_ADC_PIN, thresh=KEY_PRESS_MAX):
        try:
            self.adc = ADC(pin)
        except Exception:
            self.adc = None
        self.thresh = thresh
        self.down = False
        self.t0 = 0
        self.long_fired = False
        self._low_n = 0
        self._hi_n = 0

    def raw(self):
        if self.adc is None:
            return 1023
        try:
            return self.adc.read()
        except Exception:
            return 1023

    def poll(self):
        pressed = self.raw() < self.thresh
        now = time.ticks_ms()
        if pressed:
            self._low_n += 1
            self._hi_n = 0
        else:
            self._hi_n += 1
            self._low_n = 0

        # 去抖：连续 3 次同向才认
        if not self.down and self._low_n >= 3:
            self.down = True
            self.t0 = now
            self.long_fired = False
        elif self.down and self._hi_n >= 3:
            self.down = False
            held = time.ticks_diff(now, self.t0)
            if held < LONG_PRESS_MS and not self.long_fired:
                return "short"
            return None

        if self.down and not self.long_fired:
            if time.ticks_diff(now, self.t0) >= LONG_PRESS_MS:
                self.long_fired = True
                return "long"
        return None
