"""CSE7766 电量计量（DC1 的 U11）—— 24 字节帧，4800 8E1，单向发送

帧结构（严格按芯片手册 Rev1.2 表 3-1/3-2，0-based 下标）：

    [0]      包头1  0x55=已校准 / 0xAA=未校准 / 0xFx=异常（bit3/2/1 超量程，bit0 系数区异常）
    [1]      包头2  0x5A
    [2..4]   电压系数 Uk (24bit)      [5..7]   电压周期 Ut (24bit, µs)
    [8..10]  电流系数 Ik (24bit)      [11..13] 电流周期 It (24bit, µs)
    [14..16] 功率系数 Pk (24bit)      [17..19] 功率周期 Pt (24bit, µs)
    [20]     Adj
    [21..22] CF 脉冲个数 (16bit)
    [23]     校验 = sum([2..22]) & 0xFF

换算（手册 3.5.2）：

    U = Uk / (Ut * V2R)          I = Ik / (It * V1R)
    P = Pk / (Pt * V1R * V2R)

    V1R：采样锰铜电阻，单位 mΩ（1mΩ→1）
    V2R：电压分压电阻，单位 MΩ（1MΩ→1）

DC1 的 V1R/V2R 未公开，统一按 1.0 处理，再用 v_gain/i_gain/p_gain 做**单点标定**
（gain_new = gain_old * 真实值 / 当前读数）。芯片公式不改，标定只动一个系数。
"""


class CSE7766:
    def __init__(self, uart, v_gain=1.0, i_gain=1.0, p_gain=1.0,
                 v1r=1.0, v2r=1.0):
        self.uart = uart
        self.buf = bytearray()
        self.v_gain = v_gain
        self.i_gain = i_gain
        self.p_gain = p_gain
        self.v1r = v1r
        self.v2r = v2r
        self.last = {
            "voltage": 0.0, "current": 0.0, "power": 0.0,
            "energy_pulses": 0, "calibrated": 0, "flags": 0,
            "uk": 0, "ut": 0, "ik": 0, "it": 0, "pk": 0, "pt": 0,
        }

    # ------------------------------------------------------------------
    def _decode(self, f):
        uk = (f[2] << 16) | (f[3] << 8) | f[4]
        ut = (f[5] << 16) | (f[6] << 8) | f[7]
        ik = (f[8] << 16) | (f[9] << 8) | f[10]
        it = (f[11] << 16) | (f[12] << 8) | f[13]
        pk = (f[14] << 16) | (f[15] << 8) | f[16]
        pt = (f[17] << 16) | (f[18] << 8) | f[19]
        cf = (f[21] << 8) | f[22]

        # 周期为 0 或超量程 → 该通道按 0 处理（手册 3.8：无信号时周期超范围）
        volt = (uk / (ut * self.v2r)) if ut else 0.0
        curr = (ik / (it * self.v1r)) if it else 0.0
        powr = (pk / (pt * self.v1r * self.v2r)) if pt else 0.0

        self.last = {
            "voltage": round(volt * self.v_gain, 1),
            "current": round(curr * self.i_gain, 3),
            "power": round(powr * self.p_gain, 1),
            "energy_pulses": cf,
            "calibrated": 1 if f[0] == 0x55 else 0,
            "flags": f[0] & 0x0F,
            "uk": uk, "ut": ut, "ik": ik, "it": it, "pk": pk, "pt": pt,
        }

    # ------------------------------------------------------------------
    def poll(self):
        """非阻塞：喂字节，凑够一帧就解码。返回 True 表示本帧已更新。"""
        n = self.uart.any()
        if n:
            self.buf.extend(self.uart.read(n))
        if len(self.buf) < 24:
            return False
        updated = False
        while len(self.buf) >= 24:
            idx = self.buf.find(b"\x55")
            if idx < 0:
                idx = self.buf.find(b"\xAA")     # 未校准芯片包头是 0xAA
            if idx < 0:
                self.buf = bytearray()
                break
            if idx:
                self.buf = self.buf[idx:]
                if len(self.buf) < 24:
                    break
            if self.buf[1] != 0x5A:              # 包头2 不对 → 滑一位重同步
                self.buf = self.buf[1:]
                continue
            frame = self.buf[:24]
            if (sum(frame[2:23]) & 0xFF) != frame[23]:
                self.buf = self.buf[1:]
                continue
            self._decode(frame)
            self.buf = self.buf[24:]
            updated = True
        if len(self.buf) > 96:
            self.buf = self.buf[-24:]
        return updated

    def state(self):
        return self.last
