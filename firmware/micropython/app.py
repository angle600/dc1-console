"""斐讯 DC1 智能排插 —— MicroPython 固件 v1.1（ESP8266 1MB）

★ 体积红线（2026-11-21 实测，务必先读）
    本文件直接 mpy-cross 出来是 **17,454 B**，1 MB 版 ESP8266 加载不了：
        File "app.py", line 1, in <module>
        MemoryError: memory allocation failed, allocating 1336 bytes
    能跑的产物是 **16,205 B**，即 `firmware/build/mpy/app-1.2.13-16205.mpy`
    （= 设备上实际运行的那份，sha256 4b86e4aa1ce20736…）。那份是从一份精简过的
    源码编出来的，精简源码没有入库，所以**本文件当前不能直接部署**。

    要恢复"本文件可部署"，二选一：
      A) 把本文件精简到 16.2 KB 以内（需要重做当年的瘦身）
      B) 换 4 MB flash 模块，刷 4 MB 版固件（heap 翻倍）
    mpy-cross 的 -O/-Os/-O0/-msmall-int-bits/-march 对体积**完全无影响**，别浪费时间。

    下方 handle() 里的"空包重试"是修好的逻辑（原来 recv 返回 b"" 被当 EOF，
    导致请求体 >600B 必截断、OTA 只能 256B 分块），等有 4 MB 模块就能直接用。

对外能力（内网自持，无云）：
  * UDP 54321 广播发现：{"t":"discover"} → 回设备信息（type="DC1" 可扩展）
  * 上电 **30 秒内**接受 UDP 配网：{"t":"prov","ssid":..,"pass":..}
  * HTTP JSON API：开关 / 定时（单次或周一~周日重复）/ 倒计时 / 电量
  * PUT /api/file：WiFi 覆盖源码（MicroPython 无整片 OTA 的替代）
  * 配网模式：上电按住总键(GPIO16) → 强制 AP（DC1-Setup / 192.168.4.1）配网
"""

import gc
import os
import json
import select
import ubinascii
import uhashlib
import socket
import time
import sys

import machine
import network
from machine import Pin, I2C, UART

from cat9554 import CAT9554, KEY1, KEY2, KEY3, RELAY0, RELAY1, RELAY2, RELAY3
import store
# 注：CSE7766 计量已拆到 meter_ext.py，仅在 meter_enabled=true 时按需 import，
#     避免常驻占用 app.mpy 的堆（ESP8266 堆紧张，>16.6KB 会 OOM）。

FW_VERSION = "1.2.13"
TYPE = "DC1"
UDP_PORT = 54321
PROV_WINDOW_MS = 30000
CFG_FILE = "config.json"
STATE_FILE = "state.json"

PLUGS = ("total", "1", "2", "3", "logo")
RELAY_BIT = {"1": RELAY1, "2": RELAY2, "3": RELAY3, "total": RELAY0}
KEY_BIT = {"1": KEY1, "2": KEY2, "3": KEY3}

DEFAULT_CFG = {
    "ssid": "", "password": "", "ap_ssid": "DC1-Setup", "ap_pass": "",
    "name": "DC1", "hostname": "dc1", "http_port": 80, "api_token": "",
    # 正常运行：UART0 swap 到 GPIO15/13 给 CSE7766，恢复电量计量。
    # 日志改走 UART1(GPIO2, TX-only)；GPIO2 板上未引出时用 HTTP /api/log 读 boot.log。
    "debug_log_tx": False,
    "meter_enabled": True,           # 恢复电量计量
    "meter": {"v_gain": 1.0, "i_gain": 1.0, "p_gain": 1.0,
              "v1r": 1.0, "v2r": 1.0},
}

boot_ms = time.ticks_ms()
state = {"total": True, "1": True, "2": True, "3": True, "logo": True}
shadow = {"1": False, "2": False, "3": False}
w = None
ap_mode = False
prov_open = True
reset_pending = False
wd_ignore_until = 0        # 配网连接期间跳过 8s 看门狗
uid = "dc1-0000"
wifi_led = None           # GPIO0 WiFi 灯：低电平亮；同时是下载脚，须启动后才配置
WIFI_BLINK_MS = 500       # 1 秒周期慢闪 = 500ms 亮 + 500ms 灭


# ---------------------------------------------------------------- 小工具
# UART1 日志口：GPIO2 = LOG_TX，只发不收，115200。
# 与 UART0（计量 GPIO13/15 + 烧录 GPIO1/3）及 I2C(U7) 完全独立，三者无冲突。
# 注意 GPIO2 是启动 strap（外部上拉），作 TX 只输出高/数据，不影响 boot。
uart1 = None


def uart1_write(msg):
    """从 LOG_TX(GPIO2/UART1 TX) 输出一行日志。失败静默，绝不影响主流程。"""
    if uart1 is not None:
        try:
            uart1.write("%s\n" % msg)
        except Exception:
            pass


def log_err(where, e):
    """把运行时异常写进 err.log，同时从 LOG_TX 与 /api/log 输出"""
    txt = "ERR[%s] %r" % (where, e)
    uart1_write(txt)
    try:
        with open("err.log", "a") as f:
            f.write("%s: %r\n" % (where, e))
    except Exception:
        pass
    log(txt)


def read_log(name, n=1500):
    try:
        with open(name) as f:
            return f.read()[-n:]
    except Exception:
        return ""


def log(msg):
    """日志双输出：boot.log（HTTP GET /api/log 读）+ LOG_TX（UART1/GPIO2, 115200 实打）。
    UART0 让给 CSE7766 计量与烧录，UART1 独立，日志与计量/继电器/I2C 三者无冲突。"""
    uart1_write(msg)                       # LOG_TX 实时输出
    try:
        with open("boot.log", "a") as f:
            f.write("%s\n" % msg)
    except Exception:
        pass



def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, obj):
    try:
        with open(path, "w") as f:
            json.dump(obj, f)
        return True
    except Exception:
        return False


def cfg_get(k):
    return cfg.get(k, DEFAULT_CFG.get(k))


def mac_str():
    return ":".join("%02x" % b for b in machine.unique_id())


def sntp_sync(host="ntp.aliyun.com"):
    try:
        ai = socket.getaddrinfo(host, 123)[0][-1]
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(2)
        pkt = bytearray(48)
        pkt[0] = 0x1B
        s.sendto(pkt, ai)
        d, _ = s.recvfrom(48)
        s.close()
        if len(d) < 48:
            return False
        sec = int.from_bytes(d[40:44], "big") - 2208988800 + 8 * 3600
        t = time.gmtime(sec)
        machine.RTC().datetime((t[0], t[1], t[2], t[6], t[3], t[4], t[5], 0))
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- 硬件
def init_hw():
    """WiFi/HTTP 起来之后再初始化硬件。
    debug_log_tx=True：UART0 留在默认 GPIO1(TX)/GPIO3(RX)，LOG_TX 输出 115200 日志；
      代价是 CSE7766 计量暂缓、U7(I2C SDA=GPIO3) 可能 ENODEV 后自动降级。"""
    global i2c, u7, logo, pin_total
    if not cfg.get("hw", True):
        pass
        log("hw skipped")
        return
    # ★ GPIO3 同时是 UART0-RX 与 U7 的 I2C-SDA。
    #   调试模式不 swap：UART0 TX 留在 GPIO1 → LOG_TX 有日志输出；
    #   U7 可能因 GPIO3 被占而 ENODEV，属预期降级，HTTP/OTA/灯不受影响。
    # ★ v1.2.13：UART0/CSE7766 全部搬进 meter_ext.py，按需加载。
    if cfg.get("debug_log_tx"):
        log("UART0 stays GPIO1/3, meter skipped")
    elif not cfg.get("meter_enabled"):
        # 计量禁用：完全不碰 GPIO13(RX1)/GPIO15，避免受外部异常灌流影响。
        log("meter disabled: UART0/GPIO13 not claimed")
    else:
        init_meter()

    i2c = None
    u7 = None
    try:
        i2c = I2C(scl=Pin(12), sda=Pin(3), freq=20000)   # U7 简配片，只能 20kHz
        u7 = CAT9554(i2c, 0x20)
        for p in (KEY1, KEY2, KEY3):
            u7.set_input(p)
        pass
    except Exception as e:
        pass
        u7 = None                                        # 不致命：HTTP 仍要能起来

    logo = Pin(14, Pin.OUT, value=0)                    # 低电平点亮 —— 上电即亮
    try:
        pin_total = Pin(16, Pin.IN, Pin.PULL_DOWN)      # ESP32/RP2 才有 PULL_DOWN
    except (AttributeError, ValueError):
        pin_total = Pin(16, Pin.IN)                     # ESP8266 无内部下拉




relay_state = {k: False for k in RELAY_BIT}
i2c = None
u7 = None
logo = None
pin_total = None

meter = None


def init_meter():
    """按需加载计量模块（meter_ext.py）。
    禁用/调试模式绝不 swap UART0 —— 否则 LOG_TX 立刻没输出。
    模块内有自己的 UART0(tx=15,rx=13,4800 8E1) + CSE7766 + Energy。"""
    global meter
    if cfg.get("debug_log_tx"):
        pass
        return
    if not cfg.get("meter_enabled"):
        pass
        return
    if meter is not None:
        return
    try:
        import meter_ext
        meter = meter_ext.MeterExt(cfg)
        log("meter: loaded (meter_ext)")
    except Exception as e:
        log("meter init fail: %s" % e)


req_n = 0          # 已处理 HTTP 请求数
h_max = 0          # 单次请求最大耗时 ms
h_last_ms = 0      # 最后一次请求完成的 ticks_ms


def free_meter():
    """OTA/读大文件前释放 CSE7766 对象。
    注意：**不要 deinit UART0** —— 那是控制台，实测会让设备失去串口且状态异常。"""
    global meter
    if meter is not None:
        meter = None
        try:
            gc.collect()
        except Exception:
            pass
        log("meter freed (ota)")


def set_gains():
    if meter:
        try:
            meter.gains(cfg.get("meter") or {})
        except Exception:
            pass


RELAY_GAP_MS = 500
_last_rly = 0


def relay_write(ch, on):
    """写一路继电器。v1.2.8 校验 U7 方向寄存器(掉电复位自愈)；v1.2.11 相邻动作间隔 500ms。"""
    global _last_rly
    g = RELAY_GAP_MS - time.ticks_diff(time.ticks_ms(), _last_rly)
    if g > 0 and _last_rly:
        time.sleep_ms(g)
    if u7 is not None:
        try:
            if not u7.is_configured():
                u7.reinit()
                log("u7 reinit: cfg restored")
            u7.write_pin(RELAY_BIT[ch], 1 if on else 0)
        except Exception:
            try:
                u7.reinit()
                u7.write_pin(RELAY_BIT[ch], 1 if on else 0)
            except Exception:
                pass
    _last_rly = time.ticks_ms()
    relay_state[ch] = bool(on)


def logo_write(on):
    if logo is not None:
        logo.value(0 if on else 1)
    state["logo"] = bool(on)


def wifi_led_init():
    """WiFi 灯 GPIO0（低电平亮）。必须在 main.py 的 3s REPL 窗口之后调用，
    否则上电瞬间拉低 IO0 会把芯片带进下载模式。"""
    global wifi_led
    try:
        wifi_led = Pin(0, Pin.OUT, value=1)          # 初始熄灭
        pass
    except Exception as e:
        pass


def wifi_led_tick(now):
    """AP 或未连上已记录 SSID → 1s 周期慢闪；STA 已连上 → 长亮。"""
    if wifi_led is None:
        return
    linked = (not ap_mode) and (w is not None)
    if linked:
        try:
            linked = bool(w.isconnected())
        except Exception:
            linked = False
    if linked:
        wifi_led.value(0)                            # 长亮（低电平点亮）
    else:
        wifi_led.value(0 if ((now // WIFI_BLINK_MS) & 1) == 0 else 1)


def save_state():
    save_json(STATE_FILE, state)


def set_plug(ch, on):
    """总闸/分路互锁 + 断电记忆（语义同官方 App）"""
    if ch == "logo":
        logo_write(on)
        save_state()
        return True
    if ch == "total":
        if on:
            relay_write("total", True)
            for p in ("1", "2", "3"):
                if shadow[p]:
                    relay_write(p, True)
                    shadow[p] = False
                state[p] = relay_state[p]
            logo_write(True)
        else:
            for p in ("1", "2", "3"):
                shadow[p] = relay_state[p]
                relay_write(p, False)
                state[p] = False
            relay_write("total", False)
            logo_write(False)
        state["total"] = relay_state["total"]
        save_state()
        return True
    if ch not in ("1", "2", "3"):
        return False
    if on and not relay_state["total"]:
        relay_write("total", True)
        state["total"] = True
        logo_write(True)
    relay_write(ch, on)
    state[ch] = relay_state[ch]
    save_state()
    return True


def restore_state():
    """恢复继电器状态。**必须在 init_hw() 之后调用**——那时 U7/logo 才初始化好，
    relay_write 才会真正写继电器（早期版本在 init_hw 前调用，只改了内存字典，插孔实际没通电）。
    state.json 缺失或字段缺失时按“开”处理（上电默认全开）。"""
    s = load_json(STATE_FILE, None) or {}
    if s.get("total", True):
        relay_write("total", True)
        state["total"] = True
    else:
        relay_write("total", False)
        state["total"] = False
    for k in ("1", "2", "3"):
        if s.get(k, True):
            relay_write(k, True)
            state[k] = True
        else:
            relay_write(k, False)
            state[k] = False
    logo_write(bool(s.get("logo", True)))
    # 回读实际寄存器，确认真的写进去了（不再只信内存字典）
    real = -1
    try:
        real = u7._rd(0x01) if u7 is not None else -1
    except Exception:
        pass
    log("relay: total=%s 1=%s 2=%s 3=%s out=0x%02X" % (
        state["total"], state["1"], state["2"], state["3"], real & 0xFF))


# ---------------------------------------------------------------- 按键
# 按键：不假设极性。上电采基线（空闲电平），之后"与基线不同"即视为按下。
key_idle = {"1": 1, "2": 1, "3": 1}
key_down = {"1": False, "2": False, "3": False}
key_t = {"1": 0, "2": 0, "3": 0}
tot_idle = 1
tot_down = False
tot_t = 0
DEBOUNCE = 60


def keys_init():
    """采按键空闲基线：连续取 6 次，取最后一次（避开上电抖动）"""
    global tot_idle
    if u7 is not None:
        for ch, bit in KEY_BIT.items():
            for _ in range(6):
                try:
                    raw = u7.read_all()
                except Exception:
                    break
                key_idle[ch] = (raw >> bit) & 1
                time.sleep_ms(20)
    if pin_total is not None:
        for _ in range(6):
            tot_idle = pin_total.value()
            time.sleep_ms(20)
    pass
    log("keys idle: %s total=%d" % (key_idle, tot_idle))


def poll_keys():
    """按下沿触发（按住不连发）；极性由基线决定，不写死高/低有效"""
    global tot_down, tot_t
    now = time.ticks_ms()
    if u7 is not None:
        try:
            raw = u7.read_all()
        except Exception:
            raw = None
        if raw is not None:
            for ch, bit in KEY_BIT.items():
                v = (raw >> bit) & 1
                pressed = (v != key_idle[ch])
                if pressed and not key_down[ch]:
                    if not key_t[ch]:
                        key_t[ch] = now
                    elif time.ticks_diff(now, key_t[ch]) > DEBOUNCE:
                        key_down[ch] = True
                        key_t[ch] = 0
                        pass
                        set_plug(ch, not state[ch])
                elif not pressed:
                    key_down[ch] = False
                    key_t[ch] = 0

    if pin_total is not None:
        v = pin_total.value()
        pressed = (v != tot_idle)
        if pressed and not tot_down:
            if not tot_t:
                tot_t = now
            elif time.ticks_diff(now, tot_t) > DEBOUNCE:
                tot_down = True
                tot_t = 0
                pass
                set_plug("total", not state["total"])
        elif not pressed:
            tot_down = False
            tot_t = 0


# ---------------------------------------------------------------- 网络
def key1_held():
    """开机检测 KEY1 是否按住。
    板上两个 KEY1：丝印 KEY1 = 总键 GPIO16（实测空闲为高，按下拉低）；
    U7 P0/P1/P2 = 插孔键（上拉，按下为低）。两路都是低有效，任一成立即强制 AP。
    采样原值写 boot.log，现场可直接判极性。"""
    global i2c, u7, pin_total
    try:
        Pin(14, Pin.OUT, value=0)          # 确保 LOGO 已亮再采样
    except Exception:
        pass
    if u7 is None:
        try:
            i2c = I2C(scl=Pin(12), sda=Pin(3), freq=20000)
            u7 = CAT9554(i2c, 0x20)
            for p in (KEY1, KEY2, KEY3):
                u7.set_input(p)
        except Exception as e:
            log("key1 probe fail: %s" % e)
            u7 = None
    if pin_total is None:
        try:
            pin_total = Pin(16, Pin.IN)    # 开机早于 init_hw，这里自建总键脚
        except Exception as e:
            log("gpio16 init fail: %s" % e)
    lo16 = 0
    low7 = 0
    for _ in range(8):
        if pin_total is not None:
            try:
                if pin_total.value() == 0:
                    lo16 += 1
            except Exception:
                pass
        if u7 is not None:
            try:
                raw = u7.read_all()
                if ((raw >> KEY1) & 1) == 0 or ((raw >> KEY2) & 1) == 0 \
                        or ((raw >> KEY3) & 1) == 0:
                    low7 += 1
            except Exception:
                pass
        time.sleep_ms(30)
    log("KEY16 low %d/8 U7 low %d/8" % (lo16, low7))
    return lo16 >= 6 or low7 >= 6


def wifi_sta():
    s = network.WLAN(network.STA_IF)
    s.active(True)
    try:
        s.config(dhcp_hostname=cfg_get("hostname"))
    except Exception:
        pass
    if not cfg.get("ssid"):
        return None
    try:
        s.connect(cfg["ssid"], cfg["password"])
    except Exception:
        return None
    t0 = time.ticks_ms()
    while not s.isconnected():
        if time.ticks_diff(time.ticks_ms(), t0) > 15000:
            return None
        time.sleep_ms(200)
    return s


def wifi_ap():
    a = network.WLAN(network.AP_IF)
    a.active(True)
    # 逐项设置：某项不支持不要拖垮其它项（ESP8266 不支持 authmode 关键字）
    try:
        a.config(essid=cfg_get("ap_ssid"))
    except Exception as e:
        pass
    # 配网 AP 固定开放：主人要求免密码，手机/电脑一连即用。
    # 先清密码再设 OPEN，避免残留 WPA 配置导致仍要输入密码。
    try:
        a.config(password="")
    except Exception:
        pass
    try:
        a.config(authmode=0)               # 0 = OPEN（1=WEP、3=WPA2，勿写错）
    except Exception:
        pass
    try:
        a.ifconfig(("192.168.4.1", "255.255.255.0", "192.168.4.1", "192.168.4.1"))
    except Exception as e:
        pass
    pass
    return a


def _sta_wait(s, timeout_ms):
    t0 = time.ticks_ms()
    while not s.isconnected():
        if time.ticks_diff(time.ticks_ms(), t0) > timeout_ms:
            return False
        time.sleep_ms(200)
    return True


def sta_try(ssid, password, timeout_ms=12000):
    """切 STA 连接。成功返回 (True, ip)；失败恢复旧 SSID，返回 (False, "")。
    不关 AP，失败时网页仍可从 192.168.4.1 重试。"""
    global w, wd_ignore_until
    wd_ignore_until = time.ticks_add(time.ticks_ms(), timeout_ms * 2 + 8000)
    old_ssid = cfg.get("ssid") or ""
    old_pass = cfg.get("password") or ""
    s = network.WLAN(network.STA_IF)
    s.active(True)
    try:
        s.disconnect()
    except Exception:
        pass
    time.sleep_ms(200)
    try:
        s.connect(ssid, password or "")
    except Exception as e:
        pass
        return False, ""
    if not _sta_wait(s, timeout_ms):
        try:
            s.disconnect()
        except Exception:
            pass
        if old_ssid:
            try:
                s.connect(old_ssid, old_pass)
                _sta_wait(s, timeout_ms)
            except Exception:
                pass
        return False, ""
    w = s
    try:
        ip = s.ifconfig()[0]
    except Exception:
        ip = ""
    return True, ip


def ip_now():
    if ap_mode:
        return "192.168.4.1"
    try:
        return w.ifconfig()[0]
    except Exception:
        return "0.0.0.0"


def info_obj():
    return {"t": "dev", "proto": 1, "type": TYPE, "id": uid,
            "name": cfg_get("name"), "fw": FW_VERSION, "mac": mac_str(),
            "ip": ip_now(), "port": int(cfg_get("http_port")),
            "prov": 1 if prov_open else 0,
            "features": ["switch", "schedule", "countdown", "meter", "energy",
                         "ota-py", "prov-udp"]}


# ---------------------------------------------------------------- UDP：发现+配网
def udp_serve(sock):
    global reset_pending
    try:
        data, addr = sock.recvfrom(2048)
    except Exception:
        return
    if not data:
        return
    try:
        req = json.loads(data.decode())
    except Exception:
        return
    t = req.get("t")
    if t == "put":
        # 无线上传：{"t":"put","name":"app.mpy","off":0,"b64":"..."}
        try:
            name = req.get("name", "")
            off = int(req.get("off", 0))
            raw = ubinascii.a2b_base64(req.get("b64", ""))
            if not name or "/" in name:
                raise ValueError("bad name")
            mode = "r+b" if off else "wb"
            with open(name, mode) as f:
                f.seek(off)
                f.write(raw)
            sock.sendto(json.dumps({"ok": 1, "off": off + len(raw)}).encode(), addr)
        except Exception as e:
            try:
                sock.sendto(json.dumps({"ok": 0, "err": str(e)}).encode(), addr)
            except Exception:
                pass
        return
    if t == "log":
        try:
            payload = json.dumps({"boot": read_log("boot.log"),
                                  "err": read_log("err.log")})
            sock.sendto(payload.encode(), addr)
        except Exception:
            pass
        return
    if t in ("discover", "who"):
        try:
            sock.sendto(json.dumps(info_obj()).encode(), addr)
        except Exception:
            pass
    elif t == "prov":
        if not prov_open:
            msg = '{"t":"prov","ok":0,"err":"window_closed"}'
        elif not req.get("ssid"):
            msg = '{"t":"prov","ok":0,"err":"no_ssid"}'
        else:
            cfg["ssid"] = req["ssid"]
            cfg["password"] = req.get("pass") or ""
            if req.get("name"):
                cfg["name"] = req["name"]
            save_json(CFG_FILE, cfg)
            msg = '{"t":"prov","ok":1}'
            reset_pending = True
        try:
            sock.sendto(msg.encode(), addr)
        except Exception:
            pass


# ---------------------------------------------------------------- HTTP
def unquote(s):
    out = []
    i = 0
    while i < len(s):
        c = s[i]
        if c == "%" and i + 2 < len(s):
            try:
                out.append(chr(int(s[i + 1:i + 3], 16)))
                i += 3
                continue
            except Exception:
                pass
        out.append(" " if c == "+" else c)
        i += 1
    return "".join(out)


def parse_qs(target):
    # str 也没有 partition()（MicroPython）
    qi = target.find("?")
    if qi < 0:
        path, q = target, ""
    else:
        path, q = target[:qi], target[qi + 1:]
    d = {}
    for kv in q.split("&"):
        if "=" in kv:
            k, v = kv.split("=", 1)
            d[unquote(k)] = unquote(v)
    return path, d


def reply(conn, code, body, ctype="application/json; charset=utf-8"):
    if isinstance(body, str):
        body = body.encode()
    head = ("HTTP/1.0 %s\r\nContent-Type: %s\r\nContent-Length: %d\r\n"
            "Access-Control-Allow-Origin: *\r\nConnection: close\r\n"
            "Cache-Control: no-store\r\n\r\n" % (code, ctype, len(body)))
    try:
        n = conn.send(head.encode() + body)
    except Exception as e:
        log_err("send", e)


def serve_file(conn, name, ctype):
    """流式发送：不把整个文件读进内存（1MB 版堆只有 ~36KB）"""
    try:
        f = open(name, "rb")
    except Exception:
        return reply(conn, "404 Not Found", '{"err":"no file"}')
    try:
        conn.send(("HTTP/1.0 200 OK\r\nContent-Type: %s\r\n"
                   "Connection: close\r\nCache-Control: no-store\r\n\r\n"
                   % ctype).encode())
        while True:
            chunk = f.read(256)
            if not chunk:
                break
            mv = 0
            while mv < len(chunk):
                mv += conn.send(chunk[mv:])
    except Exception as e:
        log_err("serve_file", e)
    finally:
        try:
            f.close()
        except Exception:
            pass


def state_json():
    """直接拼字符串，避免 "建 dict + json.dumps" 的两轮大分配。
    1MB 版堆只有 2-3 KB，这两步经常直接 MemoryError（表现为连接被静默关闭）。"""
    m = meter.state() if meter else None
    if m:
        ms = ('{"voltage":%s,"current":%s,"power":%s,"calibrated":%d,'
              '"flags":%d,"uk":%d,"ut":%d,"ik":%d,"it":%d,"pk":%d,"pt":%d,'
              '"energy_pulses":%d}'
              % (m.get("voltage", 0), m.get("current", 0), m.get("power", 0),
                 m.get("calibrated", 0), m.get("flags", 0), m.get("uk", 0),
                 m.get("ut", 0), m.get("ik", 0), m.get("it", 0),
                 m.get("pk", 0), m.get("pt", 0), m.get("energy_pulses", 0)))
    else:
        ms = '{"voltage":0,"current":0,"power":0}'
    e = meter.energy.state() if meter else {"total_kwh": 0.0, "today_kwh": 0.0}
    try:
        rssi = w.status("rssi") if w else 0
    except Exception:
        rssi = 0
    return ('{"total":%d,"1":%d,"2":%d,"3":%d,"logo":%d,"meter":%s,'
            '"energy":{"total_kwh":%s,"today_kwh":%s},"cd":%s,"sched":%d,'
            '"type":"%s","fw":"%s","ip":"%s","ap":%d,"prov":%d,"time":%d,'
            '"rssi":%d}'
            % (1 if state["total"] else 0, 1 if state["1"] else 0,
               1 if state["2"] else 0, 1 if state["3"] else 0,
               1 if state["logo"] else 0, ms,
               e.get("total_kwh", 0), e.get("today_kwh", 0),
               json.dumps(cd.all()), len(sched.all()), TYPE, FW_VERSION,
               ip_now(), 1 if ap_mode else 0, 1 if prov_open else 0,
               int(time.time()), rssi))


def need_token(hdrs, q):
    want = cfg.get("api_token") or ""
    if not want:
        return True
    return (q.get("t") or hdrs.get("x-token", "")) == want


def handle(conn):
    global reset_pending
    try:
        gc.collect()
    except Exception:
        pass
    # ESP8266 上不要给 accept 出来的 socket 设 timeout（recv 会立刻超时）。
    # ★ 2026-11-21 修复：原来整个接收循环都靠 poll 判断，而 ESP8266 的 poll 会
    #   误报"无数据"，导致大请求体被截断（实测 JSON body >597B 就坏，OTA 只能 256B 分块）。
    #   现在 poll 只用来等首字节；首字节到了就说明客户端在发请求，后续用阻塞 recv
    #   把 header 和 body 一次收满。
    data = b""
    p = select.poll()
    p.register(conn, select.POLLIN)
    try:
        ev = p.poll(3000)
    except Exception as e:
        log_err("poll", e)
        return
    if not ev:
        return
    # ESP8266 的 recv 在数据还没到时会返回 b""（不是 EOF），当成 EOF 直接 break
    # 就会把请求体截断 —— 这正是 OTA 大分块失败的根因。空包重试，连续 30 次
    # （≈300ms）仍无数据才放弃。
    miss = 0
    while b"\r\n\r\n" not in data and len(data) < 6144:
        try:
            chunk = conn.recv(512)
        except Exception as e:
            log_err("recv", e)
            break
        if not chunk:
            miss += 1
            if miss > 30:
                break
            time.sleep_ms(10)
            continue
        miss = 0
        data += chunk
    if not data:
        return
    # MicroPython 的 bytes 没有 partition()，用 find 切片
    sep = data.find(b"\r\n\r\n")
    if sep < 0:
        head, body = data, b""
    else:
        head, body = data[:sep], data[sep + 4:]
    lines = head.split(b"\r\n")
    try:
        p = lines[0].decode().split(" ")
        method, target = p[0], p[1]
    except Exception:
        return reply(conn, "400 Bad Request", '{"err":"bad"}')

    hdrs = {}
    clen = 0
    for ln in lines[1:]:
        try:
            s = ln.decode()          # MicroPython 不支持 decode(errors=...)
        except Exception:
            continue
        if ":" in s:
            k, v = s.split(":", 1)
            k = k.strip().lower()
            hdrs[k] = v.strip()
            if k == "content-length":
                try:
                    clen = int(v.strip())
                except Exception:
                    clen = 0
    if clen and len(body) < clen:
        # 请求体上限：堆只有 ~6KB，超过就没法解码了，直接拒绝比 OOM 更可控
        if clen > 6000:
            return reply(conn, "413 Payload Too Large", '{"err":"too large"}')
        need = clen - len(body)
        miss = 0
        while need > 0:
            try:
                chunk = conn.recv(min(512, need))
            except Exception as e:
                log_err("recv-body", e)
                break
            if not chunk:
                miss += 1
                if miss > 30:
                    break
                time.sleep_ms(10)
                continue
            miss = 0
            body += chunk
            need -= len(chunk)
        if len(body) < clen:
            log_err("body-short", "%d/%d" % (len(body), clen))

    path, q = parse_qs(target)
    try:
        gc.collect()          # 路由前回收：碎片的堆最容易在构造大响应时失败
    except Exception:
        pass
    js = {}
    if body:
        try:
            js = json.loads(body.decode())
        except Exception as e:
            log_err("json", "%s len=%d/%d" % (e, len(body), clen))

    if path in ("/", "/index.html"):
        return serve_file(conn, "index.html", "text/html; charset=utf-8")
    if path == "/api/ping":
        return reply(conn, "200 OK", '{"ok":1,"type":"%s","fw":"%s"}'
                     % (TYPE, FW_VERSION))
    if path == "/api/info":
        return reply(conn, "200 OK", json.dumps(info_obj()))
    if path == "/api/state":
        return reply(conn, "200 OK", state_json())
    if path == "/api/put" and method == "POST":
        if not need_token(hdrs, q):
            return reply(conn, "401 Unauthorized", '{"err":"token"}')
        try:
            name = js.get("name", "")
            off = int(js.get("off", 0))
            if not name or "/" in name or name.startswith("."):
                raise ValueError("bad name")
            # 原子写入：先写 <name>.new，客户端确认全部发完后才改名替换。
            # 这样中途断电/失败不会把旧固件截断（今天两次变砖都是这个原因）。
            tmp = name + ".new"
            if js.get("done"):
                # 提交请求只做改名，绝不再碰内容（off=0 会截断文件 —— 曾把 app.mpy 截成 0 B）
                pass
            else:
                if off == 0:
                    free_meter()
                raw = ubinascii.a2b_base64(js.get("b64", ""))
                mode = "r+b" if off else "wb"
                with open(tmp, mode) as f:
                    f.seek(off)
                    f.write(raw)
            nxt = off + (0 if js.get("done") else len(raw))
            if js.get("done"):
                # 设备端 sha256 校验后再提交：不依赖客户端回读（低内存下回读必失败）
                try:
                    sz = os.stat(tmp)[6]
                except Exception as e:
                    raise ValueError("tmp missing: %s" % e)
                if sz == 0:
                    raise ValueError("empty tmp")
                want = js.get("sha") or ""
                if want:
                    h = uhashlib.sha256()
                    with open(tmp, "rb") as f:
                        while True:
                            blk = f.read(256)
                            if not blk:
                                break
                            h.update(blk)
                    got = ubinascii.hexlify(h.digest()).decode()
                    if got != want:
                        raise ValueError("sha mismatch")
                try:                      # 保留一份旧固件用于回滚
                    os.remove(name + ".bak")
                except Exception:
                    pass
                try:
                    os.rename(name, name + ".bak")
                except Exception:
                    pass
                os.rename(tmp, name)
                return reply(conn, "200 OK", json.dumps(
                    {"ok": 1, "name": name, "committed": sz}))
            return reply(conn, "200 OK", json.dumps(
                {"ok": 1, "name": name, "off": nxt}))
        except Exception as e:
            return reply(conn, "400 Bad Request", json.dumps({"ok": 0, "err": str(e)}))

    if path == "/api/get":
        if not need_token(hdrs, q):
            return reply(conn, "401 Unauthorized", '{"err":"token"}')
        try:
            gc.collect()
        except Exception:
            pass
        name = q.get("name", "")
        free_meter()
        try:
            off = int(q.get("off", 0))
            lim = int(q.get("len", 512))
        except Exception:
            off, lim = 0, 512
        try:
            with open(name, "rb") as f:
                f.seek(off)
                blob = f.read(lim)          # 分片读，避免整文件进堆（1MB 版会 OOM）
            return reply(conn, "200 OK", ubinascii.b2a_base64(blob).decode(),
                         "text/plain")
        except Exception as e:
            return reply(conn, "404 Not Found", json.dumps({"err": str(e)}))

    if path == "/api/log":
        try:
            with open("boot.log") as f:
                txt = f.read()[-2000:]
        except Exception:
            txt = ""
        return reply(conn, "200 OK", json.dumps({"log": txt}))

    if path == "/api/energy":
        return reply(conn, "200 OK", json.dumps(meter.energy.state() if meter else {}))
    if path == "/api/meter":
        want = q.get("on", "")
        if want in ("0", "1"):
            if not need_token(hdrs, q):
                return reply(conn, "401 Unauthorized", '{"err":"token"}')
            if want == "1":
                cfg["meter_enabled"] = True     # 先置位，否则 init_meter 直接返回
                if meter is None:
                    init_meter()
                cfg["meter_enabled"] = (meter is not None)
            else:
                free_meter()
                cfg["meter_enabled"] = False
            save_json(CFG_FILE, cfg)
            return reply(conn, "200 OK", json.dumps({
                "ok": 1, "meter": meter is not None,
                "free": gc.mem_free()}))
        return reply(conn, "200 OK", json.dumps(meter.state() if meter else {}))

    if path == "/api/switch":
        ch = q.get("ch", "total")
        if ch not in PLUGS:
            return reply(conn, "400 Bad Request", '{"err":"bad ch"}')
        on = q.get("on", "1") in ("1", "true", "on", "ON")
        set_plug(ch, on)
        return reply(conn, "200 OK", state_json())

    # 定时：days=0 单次（触发后自动删除）；days 位掩码 bit0=周一...
    if path == "/api/schedule":
        if method == "GET":
            return reply(conn, "200 OK", json.dumps(sched.all()))
        op = js.get("op")
        try:
            if op == "add":
                return reply(conn, "200 OK",
                             json.dumps({"ok": 1, "rule": sched.add(js)}))
            if op == "del":
                return reply(conn, "200 OK", json.dumps(
                    {"ok": 1, "removed": sched.rm(int(js.get("id", 0)))}))
            if op == "en":
                return reply(conn, "200 OK", json.dumps(
                    {"ok": 1, "changed": sched.enable(int(js.get("id", 0)),
                                                      js.get("en", 1))}))
            if op == "clear":
                for r in list(sched.all()):
                    sched.rm(r["id"])
                return reply(conn, "200 OK", '{"ok":1}')
        except Exception as e:
            return reply(conn, "400 Bad Request", '{"err":"%s"}' % e)
        return reply(conn, "400 Bad Request", '{"err":"bad op"}')

    if path == "/api/countdown":
        if method == "GET":
            return reply(conn, "200 OK", json.dumps(cd.all()))
        op = js.get("op")
        try:
            if op == "add":
                it = cd.add(js.get("ch", "1"), js.get("on", 0),
                            int(js.get("sec", 60)))
                return reply(conn, "200 OK", json.dumps({"ok": 1, "item": it}))
            if op == "del":
                return reply(conn, "200 OK", json.dumps(
                    {"ok": 1, "removed": cd.rm(int(js.get("id", 0)))}))
            if op == "clear":
                cd.clear()
                return reply(conn, "200 OK", '{"ok":1}')
        except Exception as e:
            return reply(conn, "400 Bad Request", '{"err":"%s"}' % e)
        return reply(conn, "400 Bad Request", '{"err":"bad op"}')

    if path == "/api/time":
        try:
            ep = int(js.get("epoch") or q.get("epoch") or 0)
            if ep > 1600000000:
                t = time.gmtime(ep + 8 * 3600)
                machine.RTC().datetime((t[0], t[1], t[2], t[6], t[3], t[4],
                                        t[5], 0))
                return reply(conn, "200 OK", '{"ok":1}')
        except Exception as e:
            return reply(conn, "400 Bad Request", '{"err":"%s"}' % e)
        return reply(conn, "400 Bad Request", '{"err":"bad epoch"}')

    # 单点标定：把"电表读数"换算成系数（gain_new = 真实值 * 周期 / 系数）
    if path == "/api/config":
        # AP 配网页不带 token（热点密码已是门槛）；STA 仍要 token
        if (not ap_mode) and (not need_token(hdrs, q)):
            return reply(conn, "401 Unauthorized", '{"err":"token"}')
        if method == "GET":
            return reply(conn, "200 OK", json.dumps(
                {k: cfg.get(k) for k in cfg if k != "password"}))
        if js.get("wifi_reset"):
            cfg["ssid"] = ""
            cfg["password"] = ""
            save_json(CFG_FILE, cfg)
            reset_pending = True
            return reply(conn, "200 OK", '{"ok":1,"reboot":1}')
        # 配网：先连上再保存。失败不改 config.json，网页可重试。
        if js.get("ssid") and js.get("try_connect", ap_mode):
            ssid = js.get("ssid")
            password = js.get("password") or ""
            ok, ip = sta_try(ssid, password)
            if not ok:
                return reply(conn, "200 OK",
                             '{"ok":0,"err":"connect_fail"}')
            cfg["ssid"] = ssid
            cfg["password"] = password
            save_json(CFG_FILE, cfg)
            log("prov saved ssid=%s ip=%s" % (ssid, ip))
            pass
            reset_pending = True
            return reply(conn, "200 OK",
                         '{"ok":1,"ip":"%s","saved":1}' % ip)
        for k, v in js.items():
            if k in ("ssid", "password", "name", "ap_ssid", "ap_pass",
                     "api_token", "http_port", "hostname", "meter_enabled",
                     "debug_log_tx", "hw"):
                cfg[k] = v
            elif k == "meter":
                cfg["meter"] = v
        save_json(CFG_FILE, cfg)
        set_gains()
        if js.get("reboot"):
            reset_pending = True
        return reply(conn, "200 OK", '{"ok":1}')

    if path == "/api/reboot":
        if not need_token(hdrs, q):
            return reply(conn, "401 Unauthorized", '{"err":"token"}')
        reset_pending = True
        return reply(conn, "200 OK", '{"ok":1}')

    return reply(conn, "404 Not Found", '{"err":"404"}')


# ---------------------------------------------------------------- 启动
pass

cfg = load_json(CFG_FILE, {})
for k, v in DEFAULT_CFG.items():
    cfg.setdefault(k, v)
set_gains()

_id = machine.unique_id()
uid = "dc1-%02x%02x" % (_id[-2], _id[-1])

try:
    open("boot.log", "w").close()
except Exception:
    pass

# WiFi 灯 GPIO0：main.py 的 3s 下载窗口已过，配置为输出不影响烧录
wifi_led_init()

# LOG_TX 日志口：UART1(GPIO2, TX-only, 115200)，与计量 UART0/I2C 独立。
# 放在第一条 log 之前，保证上电启动日志也能从 LOG_TX 实时抓到。
try:
    try:
        uart1 = UART(1, baudrate=115200, tx=2)   # 明确指定 GPIO2
    except Exception:
        uart1 = UART(1, baudrate=115200)         # UART1 TX 默认就在 GPIO2
    uart1.write("LOG_TX ready\n")
except Exception as e:
    uart1 = None

log("boot start FW=%s id=%s free=%d debug=%s" % (
    FW_VERSION, uid, gc.mem_free(), cfg.get("debug_log_tx")))

sched = store.Sched()
cd = store.Countdown()
# energy 已随计量搬入 meter_ext（meter.energy），禁用计量时不实例化，省一点堆。

# 注意：restore_state 已移到 init_hw() 之后调用——U7 未初始化时 relay_write 只改内存字典，
# 继电器不会真正吸合。见下方硬件初始化之后。

# LOGO 已亮后再采 KEY1。
#   按住总键开机 → ① 照旧强制进 AP 配网（原功能不变）
#                   ② v1.2.12 新增：复位默认状态（所有开关置开 + 清空定时器/倒计时）
force_ap = False
reset_all = False
try:
    reset_all = key1_held()
    force_ap = reset_all
except Exception as e:
    pass
    log("key1_held fail: %s" % e)
log("force_ap: %s reset_all: %s" % (force_ap, reset_all))
if reset_all:
    try:
        save_json(STATE_FILE, {"total": True, "1": True, "2": True, "3": True, "logo": True})
        sched.rules = []
        sched.flush()
        cd.clear()
        log("RESET: all switches ON, sched+cd cleared")
    except Exception as e:
        log_err("reset", e)

w = None if force_ap else wifi_sta()
if w is None:
    ap_mode = True
    wifi_ap()
    time.sleep_ms(600)      # ★ 等 AP 接口/IP 就绪再监听 80，否则 bind 可能失败
else:
    pass
    # STA 已连上，关掉 ESP8266 默认开启的开放热点（否则旁边谁都能连）
    try:
        network.WLAN(network.AP_IF).active(False)
        pass
    except Exception as e:
        pass

us = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
us.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
us.setblocking(False)
try:
    us.bind(("0.0.0.0", UDP_PORT))
except Exception as e:
    pass
    us = None

srv_poll = None
srv = None
# ★ v1.2.13：AP 模式下接口/IP 可能尚未就绪，bind 会失败且旧代码把原因吞掉了。
#   这里重试 3 次、每次间隔 300ms，并把真实异常写进 boot.log。
_port = int(cfg_get("http_port"))
for _try in range(3):
    try:
        srv = socket.socket()
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", _port))
        srv.listen(4)
        srv_poll = select.poll()
        srv_poll.register(srv, select.POLLIN)
        break
    except Exception as e:
        log("http bind[%d] fail: %s" % (_try, e))
        try:
            srv.close()
        except Exception:
            pass
        srv = None
        srv_poll = None
        time.sleep_ms(300)

try:
    gc.collect()
except Exception:
    pass
try:
    if not ap_mode:
        t_ok = sntp_sync()
        log("sntp: %s" % ("ok" if t_ok else "fail"))
except Exception as e:
    log("sntp err: %s" % e)

log("wifi: %s" % ("AP" if ap_mode else "STA"))
log("http bind: %s" % ("ok" if srv else "FAIL"))
try:
    init_hw()
    try:
        keys_init()
    except Exception as e:
        pass
    # ★ U7 就绪后才恢复继电器：默认全开、且真正写入 CAT9554 输出寄存器。
    try:
        restore_state()
    except Exception as e:
        log_err("restore_state", str(e))
    log("hw: meter=%s i2c=%s" % (meter is not None, u7 is not None))
except Exception as e:
    pass
    log("init_hw fail: %s" % e)
try:
    init_meter()
    log("meter: %s" % (meter is not None))
except Exception as e:
    log("init_meter fail: %s" % e)
log("DC1 %s id=%s ip=%s prov=%d" % (FW_VERSION, uid, ip_now(),
                                   1 if prov_open else 0))
gc.collect()

last = time.ticks_ms()
last_gc = last
try:
    import gc
    gc.collect()
    pass
    log("free: %d" % gc.mem_free())
except Exception:
    gc = None

loop_n = 0
min_free = gc.mem_free()
last_iter = time.ticks_ms()
while True:
    loop_n += 1
    # 软件看门狗：单次迭代超过 8 秒 = 卡死（I2C/网络异常），重启自愈
    _now = time.ticks_ms()
    _stall = time.ticks_diff(_now, last_iter)
    if _stall > 8000 and time.ticks_diff(wd_ignore_until, _now) <= 0:
        log_err("watchdog", "stall %d ms, rebooting" % _stall)
        time.sleep_ms(150)
        machine.reset()
    last_iter = _now
    # ★ v1.2.8 自愈：U7 因 5V 跌落复位后 CFG 会回 0xFF（全输入），继电器从此不动。
    #   每 100 轮（≈5s）校验一次方向寄存器，异常立即重配。
    if u7 is not None and loop_n % 100 == 0:
        try:
            if not u7.is_configured():
                u7.reinit()
                log("u7 reinit: cfg restored (watch)")
        except Exception:
            pass
    if gc and loop_n % 50 == 0:
        gc.collect()
        _f = gc.mem_free()
        if _f < min_free:
            min_free = _f
    if reset_pending:
        time.sleep_ms(300)
        machine.reset()

    now = time.ticks_ms()
    wifi_led_tick(now)                 # WiFi 灯：未连慢闪 1s / 已连长亮
    dt = time.ticks_diff(now, last)
    last = now
    if prov_open and time.ticks_diff(now, boot_ms) > PROV_WINDOW_MS:
        prov_open = False
        pass

    if srv is not None and srv_poll is not None:
        conn = None
        try:
            if srv_poll.poll(0):
                conn, _ = srv.accept()
        except OSError:
            conn = None
        except Exception as e:
            pass
            conn = None
        if conn is not None:
            try:
                _t0 = time.ticks_ms()
                handle(conn)
                _dt = time.ticks_diff(time.ticks_ms(), _t0)
                req_n += 1
                h_last_ms = time.ticks_ms()
                if _dt > h_max:
                    h_max = _dt
            except Exception as e:
                log_err("handle", e)
            finally:
                try:
                    time.sleep_ms(60)
                    conn.close()
                except Exception:
                    pass

    if us is not None:
        udp_serve(us)

    try:
        poll_keys()
    except Exception as e:
        pass
    if meter:
        meter.poll(dt)
    else:
        time.sleep_ms(10)

    try:
        for ch, on, rid in sched.due():
            set_plug(ch, on)
        for ch, on, cid in cd.tick(dt):
            set_plug(ch, on)
    except Exception as e:
        pass

    if time.ticks_diff(now, last_gc) > 60000:
        last_gc = now
        gc.collect()
