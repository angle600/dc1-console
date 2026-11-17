"""CSE7766 电量计量（DC1 的 U11）—— 24 字节帧，4800 8E1，单向发送

帧结构（社区反推 + datasheet 交叉验证）：
    [0]      0x55 帧头
    [1]      保留
    [2..4]   电压周期计数 (24bit)
    [5..7]   电流周期计数 (24bit)
    [8..10]  功率周期计数 (24bit)
    [11..12] 电量脉冲计数 (16bit)
    [13]     保留
    [14..22] 其他/未用
    [23]     校验 = sum([2..22]) & 0xFF

⚠ 精度说明：CSE7766 输出的是"周期计数"，要换算成 V/A/W 必须除以实测标定系数。
本驱动只做原始值解码 + 线性系数（config.json 里 v_gain/i_gain/p_gain），
默认 1.0 时输出的是原始计数，**必须现场标定**（见 docs/04-MicroPython方案.md）。
"""


class CSE7766:
    def __init__(self, uart, v_gain=1.0, i_gain=1.0, p_gain=1.0):
        self.uart = uart
        self.buf = bytearray()
        self.v_gain = v_gain
        self.i_gain = i_gain
        self.p_gain = p_gain
        self.last = {"v_raw": 0, "i_raw": 0, "p_raw": 0,
                     "voltage": 0.0, "current": 0.0, "power": 0.0,
                     "energy": 0}

    def _decode(self, f):
        v_raw = (f[2] << 16) | (f[3] << 8) | f[4]
        i_raw = (f[5] << 16) | (f[6] << 8) | f[7]
        p_raw = (f[8] << 16) | (f[9] << 8) | f[10]
        energy = (f[11] << 8) | f[12]
        # 周期计数越小 = 量越大，故取倒数；系数由标定决定
        v = (self.v_gain / v_raw * 1e6) if v_raw else 0.0
        i = (self.i_gain / i_raw * 1e6) if i_raw else 0.0
        p = (self.p_gain / p_raw * 1e6) if p_raw else 0.0
        self.last = {"v_raw": v_raw, "i_raw": i_raw, "p_raw": p_raw,
                     "voltage": round(v, 1), "current": round(i, 3),
                     "power": round(p, 1), "energy": energy}

    def poll(self):
        """非阻塞：喂入新字节，凑够一帧就解码。返回 True 表示本帧已更新。"""
        n = self.uart.any()
        if n:
            self.buf.extend(self.uart.read(n))
        if len(self.buf) < 24:
            return False
        updated = False
        while len(self.buf) >= 24:
            idx = self.buf.find(b"\x55")
            if idx < 0:
                self.buf = bytearray()
                break
            if idx:
                self.buf = self.buf[idx:]
                if len(self.buf) < 24:
                    break
            frame = self.buf[:24]
            if (sum(frame[2:23]) & 0xFF) != frame[23]:
                self.buf = self.buf[1:]      # 校验错，滑一位重同步
                continue
            self._decode(frame)
            self.buf = self.buf[24:]
            updated = True
        if len(self.buf) > 96:               # 防止积压吃内存
            self.buf = self.buf[-24:]
        return updated

    def state(self):
        return self.last
