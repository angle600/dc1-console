"""CAT9554 IO 扩展（DC1 的 U7，I2C 0x20）

寄存器：0x00 输入口 / 0x01 输出口 / 0x02 极性 / 0x03 方向（1=输入）
注意：U7 是简配片，SCL 实测只能 ~20kHz。
"""

REG_IN = 0x00
REG_OUT = 0x01
REG_POL = 0x02
REG_CFG = 0x03

# DC1 位映射（与 hardware/README.md 一致）
KEY1, KEY2, KEY3 = 0, 1, 2
RELAY3, RELAY2, RELAY1, RELAY0 = 4, 5, 6, 7


class CAT9554:
    def __init__(self, i2c, addr=0x20):
        self.i2c = i2c
        self.addr = addr
        self.out = 0x00
        # 全部先做输出、写 0（继电器全断），再把 P0~P2 设输入
        self._wr(REG_CFG, 0x00)
        self._wr(REG_OUT, 0x00)
        self.out = 0x00

    def _wr(self, reg, val):
        self.i2c.writeto_mem(self.addr, reg, bytes([val & 0xFF]))

    def _rd(self, reg):
        return self.i2c.readfrom_mem(self.addr, reg, 1)[0]

    # ---- 输入 ----
    def set_input(self, pin):
        cfg = self._rd(REG_CFG)
        self._wr(REG_CFG, cfg | (1 << pin))
        self._wr(REG_POL, 0x00)          # 不反相，按键低有效由上层判断
        self._wr(REG_IN, 0x00)           # 准双向：写 1 才有上拉

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
