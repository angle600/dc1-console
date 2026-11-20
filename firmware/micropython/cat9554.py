"""CAT9554 IO 扩展（DC1 的 U7，I2C 0x20）

寄存器：0x00 输入口 / 0x01 输出口 / 0x02 极性 / 0x03 方向（1=输入）
注意：U7 是简配片，SCL 实测只能 ~20kHz。

★ v1.2.8 关键修复（2026-11-20）：
  CAT9554/PCF8574 是**准双向 IO**，上电默认 CFG=0xFF（全部输入）。
  一旦 U7 因 5V 跌落复位，方向寄存器回 0xFF，此后 write_pin() 写输出寄存器
  不报错但引脚仍是输入 → 继电器完全不动作；而按键 P0~P2 本来就是输入、
  照常能读 → 表现为「API 全部成功、继电器却没反应」。
  1) `set_input()` 旧实现 `_wr(REG_IN, 0x00)` 会把**所有输出位清零**（误关继电器），
     且输入脚在准双向模式下必须写 1 才有内部上拉。现改为只置输入位、保留输出位。
  2) 新增 `reinit()`：重设方向并恢复输出电平，供 relay_write 失败时自愈。
"""

REG_IN = 0x00
REG_OUT = 0x01
REG_POL = 0x02
REG_CFG = 0x03

# DC1 位映射（与 hardware/README.md 一致）
KEY1, KEY2, KEY3 = 0, 1, 2
RELAY3, RELAY2, RELAY1, RELAY0 = 4, 5, 6, 7

# P0~P2 为按键输入，P3 未用（保留输入），P4~P7 为继电器输出
CFG_MASK = 0x0F          # bit0~3 = 1 → 输入；bit4~7 = 0 → 输出


class CAT9554:
    def __init__(self, i2c, addr=0x20):
        self.i2c = i2c
        self.addr = addr
        # out 保存输出锁存器：P4~P7=继电器(0=断)，P0~P2=1(准双向上拉)
        self.out = CFG_MASK & 0x07
        self.apply()

    def _wr(self, reg, val):
        self.i2c.writeto_mem(self.addr, reg, bytes([val & 0xFF]))

    def _rd(self, reg):
        return self.i2c.readfrom_mem(self.addr, reg, 1)[0]

    def apply(self):
        """按期望值重配方向与输出。U7 复位后调用即可恢复。"""
        self._wr(REG_POL, 0x00)            # 不反相
        self._wr(REG_OUT, self.out)        # 先定电平，避免切换方向时抖动
        self._wr(REG_CFG, CFG_MASK)        # P0~P3 输入，P4~P7 输出

    def reinit(self):
        """U7 疑似复位（ENODEV/继电器不动）时重建配置。"""
        self.apply()

    def is_configured(self):
        """方向寄存器是否仍是期望值 —— 用于检测 U7 掉电复位。"""
        try:
            return self._rd(REG_CFG) == CFG_MASK
        except Exception:
            return False

    # ---- 输入 ----
    def set_input(self, pin):
        cfg = self._rd(REG_CFG)
        self._wr(REG_CFG, cfg | (1 << pin))
        # ★ 只把该输入位置 1（准双向上拉），不得清零其它输出位
        self.out |= (1 << pin)
        self._wr(REG_IN, self.out)

    def set_output(self, pin):
        cfg = self._rd(REG_CFG)
        self._wr(REG_CFG, cfg & ~(1 << pin))
        self.write_pin(pin, 0)

    def read_all(self):
        return self._rd(REG_IN)

    def read_pin(self, pin):
        return (self.read_all() >> pin) & 1

    # ---- 输出 ----
    def write_pin(self, pin, on):
        if on:
            self.out |= (1 << pin)
        else:
            self.out &= ~(1 << pin)
        self._wr(REG_OUT, self.out)

    def read_pin_out(self, pin):
        return (self.out >> pin) & 1
