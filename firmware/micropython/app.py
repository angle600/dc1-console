"""斐讯 DC1 智能排插 —— MicroPython 固件 v1.1（ESP8266 1MB）

对外能力（内网自持，无云）：
  * UDP 54321 广播发现：{"t":"discover"} → 回设备信息（type="DC1" 可扩展）
  * 上电 **30 秒内**接受 UDP 配网：{"t":"prov","ssid":..,"pass":..}
  * HTTP JSON API：开关 / 定时（单次或周一~周日重复）/ 倒计时 / 电量
  * PUT /api/file：WiFi 覆盖源码（MicroPython 无整片 OTA 的替代）
  * 救援模式：上电按住总键(GPIO16) → 跳过 CSE7766 的 UART swap，串口 REPL 保留
"""

import gc
import json
import select
import ubinascii
import socket
import time
import sys

import machine
import network
from machine import Pin, I2C, UART

from cat9554 import CAT9554, KEY1, KEY2, KEY3, RELAY0, RELAY1, RELAY2, RELAY3
from cse7766 import CSE7766
import store

FW_VERSION = "1.1.0"
TYPE = "DC1"
UDP_PORT = 54321
PROV_WINDOW_MS = 30000
CFG_FILE = "config.json"
STATE_FILE = "state.json"

PLUGS = ("total", "1", "2", "3", "logo")
RELAY_BIT = {"1": RELAY1, "2": RELAY2, "3": RELAY3, "total": RELAY0}
KEY_BIT = {"1": KEY1, "2": KEY2, "3": KEY3}

DEFAULT_CFG = {
    "ssid": "", "password": "", "ap_ssid": "DC1-Setup", "ap_pass": "dc1setup",
    "name": "DC1", "hostname": "dc1", "http_port": 80, "api_token": "",
    "meter_enabled": False,          # 首次上电默认关：避免 UART0 swap 抢走 REPL
    "meter": {"v_gain": 1.0, "i_gain": 1.0, "p_gain": 1.0,
              "v1r": 1.0, "v2r": 1.0},
}

boot_ms = time.ticks_ms()
state = {"total": False, "1": False, "2": False, "3": False, "logo": False}
shadow = {"1": False, "2": False, "3": False}
w = None
ap_mode = False
prov_open = True
reset_pending = False
uid = "dc1-0000"


# ---------------------------------------------------------------- 小工具
def log_err(where, e):
    """把运行时异常写进 err.log，同时打到串口"""
    try:
        print("ERR[%s] %r" % (where, e))
    except Exception:
        pass
    try:
        with open("err.log", "a") as f:
            f.write("%s: %r\n" % (where, e))
    except Exception:
        pass


def read_log(name, n=1500):
    try:
        with open(name) as f:
            return f.read()[-n:]
    except Exception:
        return ""


def log(msg):
    """启动日志写文件：UART 被 swap 后串口看不到，只能靠这个排查"""
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
    """WiFi/HTTP 起来之后再初始化硬件：
    UART0 必须先 swap 到 GPIO15/13，GPIO3 才能当 I2C SDA。"""
    global uart0, i2c, u7, logo, pin_total
    if not cfg.get("hw", True):
        print("hw skipped (config: hw=false) -> REPL 保留在 GPIO1/3")
        log("hw skipped")
        return
    # ★ 关键：GPIO3 同时是 UART0-RX（REPL）和 U7 的 I2C-SDA。
    #   必须先把 UART0 swap 到 GPIO15/13，GPIO3 才能当 SDA 用，否则 I2C 收不到 ACK（ENODEV）。
    #   代价：REPL 会随 UART0 搬到 GPIO13/15，USB 口的串口就没有交互了。
    uart0 = None
    try:
        uart0 = UART(0, baudrate=4800, bits=8, parity=0, stop=1, tx=15, rx=13,
                     timeout=200)
        print("uart0 -> GPIO15/13 (CSE7766)")
    except Exception as e:
        print("uart swap fail:", e)

    i2c = None
    u7 = None
    try:
        i2c = I2C(scl=Pin(12), sda=Pin(3), freq=20000)   # U7 简配片，只能 20kHz
        u7 = CAT9554(i2c, 0x20)
        for p in (KEY1, KEY2, KEY3):
            u7.set_input(p)
        print("U7 ok id=0x20")
    except Exception as e:
        print("U7 init fail:", e)                        # 不致命：HTTP 仍要能起来

    logo = Pin(14, Pin.OUT, value=1)                    # 低电平点亮
    try:
        pin_total = Pin(16, Pin.IN, Pin.PULL_DOWN)      # ESP32/RP2 才有 PULL_DOWN
    except (AttributeError, ValueError):
        pin_total = Pin(16, Pin.IN)                     # ESP8266 无内部下拉




relay_state = {k: False for k in RELAY_BIT}
uart0 = None
i2c = None
u7 = None
logo = None
pin_total = None


def _safe_mode():
    """上电按住总键 → 不读计量（仅当按键确实为高有效时生效）"""
    if pin_total is None:
        return False
    for _ in range(20):
        if pin_total.value() != 1:
            return False
        time.sleep_ms(5)
    return True


meter = None


def init_meter():
    """meter_enabled=True 才解析 CSE7766 帧；UART 已在上电时 swap 好。"""
    global meter
    if not cfg.get("meter_enabled"):
        print("meter disabled (config.json: meter_enabled=true 开启)")
        return
    if uart0 is None:
        print("meter: uart not available")
        return
    # 注：GPIO16 悬空时会误判为"按住"，故不再用它决定是否跳过计量
    try:
        meter = CSE7766(uart0, v1r=float((cfg.get("meter") or {}).get("v1r", 1.0)),
                        v2r=float((cfg.get("meter") or {}).get("v2r", 1.0)))
        print("meter ready")
    except Exception as e:
        print("cse7766 init fail:", e)


def free_meter():
    """OTA/读大文件前释放计量对象：1MB 版堆只有 ~5KB，不释放会 MemoryError。
    重启后 init_meter() 会重建，不影响使用。"""
    global meter
    if meter is not None:
        meter = None
        try:
            gc.collect()
        except Exception:
            pass
        log("meter freed (ota/read)")


def set_gains():
    m = cfg.get("meter") or {}
    if meter:
        meter.v_gain = float(m.get("v_gain", 1.0))
        meter.i_gain = float(m.get("i_gain", 1.0))
        meter.p_gain = float(m.get("p_gain", 1.0))
        meter.v1r = float(m.get("v1r", 1.0))
        meter.v2r = float(m.get("v2r", 1.0))


def relay_write(ch, on):
    if u7 is not None:
        u7.write_pin(RELAY_BIT[ch], 1 if on else 0)
    relay_state[ch] = bool(on)


def logo_write(on):
    if logo is not None:
        logo.value(0 if on else 1)
    state["logo"] = bool(on)


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
    s = load_json(STATE_FILE, None) or {}
    if s.get("total"):
        relay_write("total", True)
        state["total"] = True
    for k in ("1", "2", "3"):
        if s.get(k):
            relay_write(k, True)
            state[k] = True
    if s.get("logo"):
        logo_write(True)


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
    print("keys idle:", key_idle, "total:", tot_idle)
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
                        print("KEY", ch)
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
                print("KEY total")
                set_plug("total", not state["total"])
        elif not pressed:
            tot_down = False
            tot_t = 0


# ---------------------------------------------------------------- 网络
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
        print("ap essid err", e)
    try:
        a.config(password=cfg_get("ap_pass"))
    except Exception as e:
        print("ap pass err", e)
    try:
        a.config(authmode=3)
    except Exception:
        pass
    try:
        a.ifconfig(("192.168.4.1", "255.255.255.0", "192.168.4.1", "192.168.4.1"))
    except Exception as e:
        print("ap ip err", e)
    print("AP:", a.config("essid"), a.ifconfig()[0])
    return a


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
    print("HANDLE len=%d %r" % (len(data), data[:40]))
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
        print("SENT", n)
    except Exception as e:
        print("SEND ERR", e)


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
    d = {k: bool(v) for k, v in state.items()}
    d["meter"] = meter.state() if meter else {"voltage": 0, "current": 0,
                                              "power": 0}
    d["energy"] = energy.state()
    d["cd"] = cd.all()
    d["sched"] = len(sched.all())
    d["type"] = TYPE
    d["fw"] = FW_VERSION
    d["ip"] = ip_now()
    d["ap"] = 1 if ap_mode else 0
    d["prov"] = 1 if prov_open else 0
    d["time"] = int(time.time())
    try:
        d["rssi"] = w.status("rssi") if w else 0
    except Exception:
        d["rssi"] = 0
    return json.dumps(d)


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
    # ESP8266 上不要给 accept 出来的 socket 设 timeout（recv 会立刻超时），
    # 用 poll 判断可读后再做阻塞 recv。
    data = b""
    p = select.poll()
    p.register(conn, select.POLLIN)
    deadline = time.ticks_add(time.ticks_ms(), 3000)
    while b"\r\n\r\n" not in data and len(data) < 4096:
        left = time.ticks_diff(deadline, time.ticks_ms())
        if left <= 0:
            break
        try:
            ev = p.poll(left)
        except Exception as e:
            log_err("poll", e)
            break
        if not ev:
            break
        try:
            chunk = conn.recv(512)
        except Exception as e:
            log_err("recv", e)
            break
        if not chunk:
            break
        data += chunk
    print("HANDLE len=%d %r" % (len(data), data[:40]))
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
        need = clen - len(body)
        while need > 0:
            try:
                chunk = conn.recv(min(512, need))
            except Exception:
                break
            if not chunk:
                break
            body += chunk
            need -= len(chunk)

    path, q = parse_qs(target)
    js = {}
    if body:
        try:
            js = json.loads(body.decode())
        except Exception:
            js = {}

    if path in ("/", "/index.html"):
        return serve_file(conn, "index.html", "text/html; charset=utf-8")
    if path == "/api/ping":
        return reply(conn, "200 OK", '{"ok":1,"type":"%s","fw":"%s"}'
                     % (TYPE, FW_VERSION))
    if path == "/api/info":
        return reply(conn, "200 OK", json.dumps(info_obj()))
    if path == "/api/state":
        return reply(conn, "200 OK", state_json())
    if path == "/api/cal" and method == "GET":
        last = meter.state() if meter else {}
        return reply(conn, "200 OK", json.dumps({
            "meter": cfg.get("meter") or {},
            "measured": {"voltage": last.get("voltage", 0),
                         "current": last.get("current", 0),
                         "power": last.get("power", 0)},
            "raw": {k: last.get(k, 0) for k in ("uk", "ut", "ik", "it", "pk", "pt")},
            "calibrated": last.get("calibrated", 0),
            "flags": last.get("flags", 0),
        }))

    # ---- TCP 无线迭代：分片写文件 / 读回校验 ----
    if path == "/api/put" and method == "POST":
        if not need_token(hdrs, q):
            return reply(conn, "401 Unauthorized", '{"err":"token"}')
        try:
            name = js.get("name", "")
            off = int(js.get("off", 0))
            if off == 0:
                free_meter()
            raw = ubinascii.a2b_base64(js.get("b64", ""))
            if not name or "/" in name or name.startswith("."):
                raise ValueError("bad name")
            mode = "r+b" if off else "wb"
            with open(name, mode) as f:
                f.seek(off)
                f.write(raw)
            return reply(conn, "200 OK", json.dumps(
                {"ok": 1, "name": name, "off": off + len(raw)}))
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
        return reply(conn, "200 OK", json.dumps(energy.state()))
    if path == "/api/meter":
        return reply(conn, "200 OK", json.dumps(meter.state() if meter else {}))

    if path == "/api/sys":
        try:
            up = time.ticks_diff(time.ticks_ms(), boot_ms) / 1000.0
        except Exception:
            up = 0
        try:
            rc = machine.reset_cause()
        except Exception:
            rc = -1
        return reply(conn, "200 OK", json.dumps({
            "up": round(up, 1), "free": gc.mem_free(),
            "min_free": min_free, "loop": loop_n, "reset": rc,
            "rssi": (w.status("rssi") if w else 0),
        }))

    if path == "/api/keys":
        raw = -1
        if u7 is not None:
            try:
                raw = u7.read_all()
            except Exception as e:
                raw = -2
        return reply(conn, "200 OK", json.dumps({
            "raw": raw,
            "total_pin": pin_total.value() if pin_total is not None else -1,
            "u7": u7 is not None,
        }))

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
    if path == "/api/cal":
        if not need_token(hdrs, q):
            return reply(conn, "401 Unauthorized", '{"err":"token"}')
        m = cfg.get("meter") or {}
        if js.get("reset") or q.get("reset"):
            m.update({"v_gain": 1.0, "i_gain": 1.0, "p_gain": 1.0,
                      "v1r": 1.0, "v2r": 1.0})
            cfg["meter"] = m
            save_json(CFG_FILE, cfg)
            set_gains()
            return reply(conn, "200 OK", json.dumps({"ok": 1, "meter": m}))
        if meter is None:
            return reply(conn, "503 Unavailable",
                         '{"err":"meter_off","hint":"安全模式下未初始化 CSE7766"}')
        last = meter.state()
        changed = {}
        # 硬件常量：V1R=采样锰铜电阻(mΩ)，V2R=分压电阻(MΩ)。
        # 求出它们等价于"用回原厂标定"（芯片内的 Uk/Ik/Pk 已随帧下发）
        if js.get("v1r") or q.get("v1r"):
            v1r = float(js.get("v1r") or q.get("v1r"))
            if v1r <= 0:
                return reply(conn, "400 Bad Request", '{"err":"bad v1r"}')
            m["v1r"] = round(v1r, 6)
            changed["v1r"] = m["v1r"]
        if js.get("v2r") or q.get("v2r"):
            v2r = float(js.get("v2r") or q.get("v2r"))
            if v2r <= 0:
                return reply(conn, "400 Bad Request", '{"err":"bad v2r"}')
            m["v2r"] = round(v2r, 6)
            changed["v2r"] = m["v2r"]
        try:
            if js.get("voltage") or q.get("voltage"):
                true_v = float(js.get("voltage") or q.get("voltage"))
                ut, uk = last.get("ut", 0), last.get("uk", 0)
                if not (ut and uk):
                    raise ValueError("no_frame")
                m["v_gain"] = round(true_v * ut / uk, 6)
                changed["v_gain"] = m["v_gain"]
            if js.get("current") or q.get("current"):
                true_i = float(js.get("current") or q.get("current"))
                it, ik = last.get("it", 0), last.get("ik", 0)
                if not (it and ik):
                    raise ValueError("no_frame")
                m["i_gain"] = round(true_i * it / ik, 6)
                changed["i_gain"] = m["i_gain"]
            if js.get("power") or q.get("power"):
                true_p = float(js.get("power") or q.get("power"))
                pt, pk = last.get("pt", 0), last.get("pk", 0)
                if not (pt and pk):
                    raise ValueError("no_frame")
                m["p_gain"] = round(true_p * pt / pk, 6)
                changed["p_gain"] = m["p_gain"]
        except Exception as e:
            return reply(conn, "400 Bad Request", '{"err":"%s"}' % e)
        if not changed:
            return reply(conn, "400 Bad Request",
                         '{"err":"need voltage/current/power/v1r/v2r"}')
        cfg["meter"] = m
        save_json(CFG_FILE, cfg)
        set_gains()
        return reply(conn, "200 OK", json.dumps(
            {"ok": 1, "changed": changed, "gains": m,
             "measured": {"voltage": last.get("voltage"),
                          "current": last.get("current"),
                          "power": last.get("power")}}))

    if path == "/api/config":
        if not need_token(hdrs, q):
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
        for k, v in js.items():
            if k in ("ssid", "password", "name", "ap_ssid", "ap_pass",
                     "api_token", "http_port", "hostname", "meter_enabled",
                     "hw"):
                cfg[k] = v
            elif k == "meter":
                cfg["meter"] = v
        save_json(CFG_FILE, cfg)
        set_gains()
        if js.get("reboot"):
            reset_pending = True
        return reply(conn, "200 OK", '{"ok":1}')

    if path == "/api/file":
        if not need_token(hdrs, q):
            return reply(conn, "401 Unauthorized", '{"err":"token"}')
        if not need_token(hdrs, q):
            return reply(conn, "401 Unauthorized", '{"err":"token"}')
        name = q.get("name", "")
        if not (name.endswith(".py") or name.endswith(".json")) or "/" in name:
            return reply(conn, "400 Bad Request", '{"err":"name"}')
        try:
            with open(name, "wb") as f:
                f.write(body)
            return reply(conn, "200 OK", '{"ok":1,"bytes":%d}' % len(body))
        except Exception as e:
            return reply(conn, "500 Internal", '{"err":"%s"}' % e)

    if path == "/api/reboot":
        if not need_token(hdrs, q):
            return reply(conn, "401 Unauthorized", '{"err":"token"}')
        reset_pending = True
        return reply(conn, "200 OK", '{"ok":1}')

    return reply(conn, "404 Not Found", '{"err":"404"}')


# ---------------------------------------------------------------- 启动
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

sched = store.Sched()
cd = store.Countdown()
energy = store.Energy()

try:
    restore_state()
except Exception as e:
    print("restore_state fail:", e)

w = wifi_sta()
if w is None:
    ap_mode = True
    wifi_ap()
    print("AP mode: %s / %s" % (cfg_get("ap_ssid"), cfg_get("ap_pass")))
else:
    print("STA:", w.ifconfig())
    # STA 已连上，关掉 ESP8266 默认开启的开放热点（否则旁边谁都能连）
    try:
        network.WLAN(network.AP_IF).active(False)
        print("AP disabled (STA connected)")
    except Exception as e:
        print("ap disable fail", e)

us = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
us.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
us.setblocking(False)
try:
    us.bind(("0.0.0.0", UDP_PORT))
except Exception as e:
    print("udp bind fail", e)
    us = None

srv_poll = None
srv = socket.socket()
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    srv.bind(("0.0.0.0", int(cfg_get("http_port"))))
    srv.listen(4)
    srv_poll = select.poll()
    srv_poll.register(srv, select.POLLIN)
except Exception as e:
    print("http bind fail", e)
    srv = None

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
        print("keys_init fail", e)
    log("hw: uart=%s i2c=%s" % (uart0 is not None, u7 is not None))
except Exception as e:
    print("init_hw fail", e)
    log("init_hw fail: %s" % e)
try:
    init_meter()
    log("meter: %s" % (meter is not None))
except Exception as e:
    log("init_meter fail: %s" % e)
print("DC1 %s id=%s ip=%s prov=%d" % (FW_VERSION, uid, ip_now(),
                                      1 if prov_open else 0))
gc.collect()

last = time.ticks_ms()
last_gc = last
try:
    import gc
    gc.collect()
    print("free after init:", gc.mem_free())
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
    if _stall > 8000:
        log_err("watchdog", "stall %d ms, rebooting" % _stall)
        time.sleep_ms(150)
        machine.reset()
    last_iter = _now
    if gc and loop_n % 50 == 0:
        gc.collect()
        _f = gc.mem_free()
        if _f < min_free:
            min_free = _f
    if reset_pending:
        time.sleep_ms(300)
        machine.reset()

    now = time.ticks_ms()
    dt = time.ticks_diff(now, last)
    last = now
    if prov_open and time.ticks_diff(now, boot_ms) > PROV_WINDOW_MS:
        prov_open = False
        print("prov window closed")

    if srv is not None and srv_poll is not None:
        conn = None
        try:
            if srv_poll.poll(0):
                conn, _ = srv.accept()
        except OSError:
            conn = None
        except Exception as e:
            print("accept err", e)
            conn = None
        if conn is not None:
            print("ACCEPT")
            try:
                handle(conn)
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
        print("keys err", e)
    if meter:
        if meter.poll():
            energy.add(meter.last.get("power", 0), dt)
    else:
        time.sleep_ms(10)

    try:
        for ch, on, rid in sched.due():
            set_plug(ch, on)
        for ch, on, cid in cd.tick(dt):
            set_plug(ch, on)
    except Exception as e:
        print("timer err", e)

    if time.ticks_diff(now, last_gc) > 60000:
        last_gc = now
        gc.collect()
