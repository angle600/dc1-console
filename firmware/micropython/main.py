"""斐讯 DC1 智能排插 —— MicroPython 固件（ESP8266 1MB）

功能：
  * 手机 AP 配网（热点 DC1-Setup + DNS 劫持 + 网页配置），无 App
  * HTTP JSON API：/api/state、/api/switch?ch=&on=
  * WiFi 更新代码：PUT /api/file?name=main.py（不需串口/WebREPL）
  * 4 路继电器（U7 P4~P7）+ 3 个分路按键 + 总按键 + LOGO 灯
  * CSE7766 计量（原始计数 + 可标定系数）

⚠ 救援模式：上电时**按住总按键(GPIO16)** 不进计量初始化 —— 因为 CSE7766 需要把 UART0
  swap 到 GPIO15/13，swap 后串口 REPL 会一起搬到那两个脚，USB 口就没 REPL 了。
  按住总键启动 = 保留 GPIO1/3 上的 REPL，方便串口救砖。
⚠ 1MB Flash 无法做整片固件 WiFi 烧写（详见 docs/04-MicroPython方案.md）。
"""

import gc
import json
import socket
import time
import sys

from machine import Pin, I2C, UART
import network

from cat9554 import CAT9554, KEY1, KEY2, KEY3, RELAY0, RELAY1, RELAY2, RELAY3
from cse7766 import CSE7766
import webui

CFG_FILE = "config.json"
STATE_FILE = "state.json"
PLUGS = ("total", "1", "2", "3", "logo")
RELAY_BIT = {"1": RELAY1, "2": RELAY2, "3": RELAY3, "total": RELAY0}
KEY_BIT = {"1": KEY1, "2": KEY2, "3": KEY3}

DEFAULT_CFG = {
    "ssid": "", "password": "", "ap_ssid": "DC1-Setup", "ap_pass": "dc1setup",
    "ap_timeout_s": 120, "hostname": "dc1", "http_port": 80,
    "api_token": "", "webrepl": False,
    "meter": {"v_gain": 1.0, "i_gain": 1.0, "p_gain": 1.0},
}

state = {"total": False, "1": False, "2": False, "3": False, "logo": False}
shadow = {"1": False, "2": False, "3": False}
boot_ms = time.ticks_ms()
ap_mode = False
reset_pending = False


# ---------------------------------------------------------------- 配置 / 状态
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


def apply_cfg():
    m = cfg.get("meter") or {}
    for k in ("v_gain", "i_gain", "p_gain"):
        m.setdefault(k, 1.0)
    cfg["meter"] = m


# ---------------------------------------------------------------- 硬件
i2c = I2C(scl=Pin(12), sda=Pin(3), freq=20000)   # U7 简配片，只能 20kHz
u7 = CAT9554(i2c, 0x20)
for p in (KEY1, KEY2, KEY3):
    u7.set_input(p)
relay_state = {k: False for k in RELAY_BIT}
logo = Pin(14, Pin.OUT, value=1)                  # 低电平点亮
pin_total = Pin(16, Pin.IN, Pin.PULL_DOWN)        # 总按键：按下为高

meter = None
if not pin_total.value():                          # 未按住总键 → 正常启计量
    try:
        # 必须 swap 到 GPIO15/13，注意 REPL 会一起搬走
        uart = UART(0, baudrate=4800, bits=8, parity=0, stop=1, tx=15, rx=13,
                    timeout=200)
        m = cfg.get("meter") or {}
        meter = CSE7766(uart, m.get("v_gain", 1.0), m.get("i_gain", 1.0),
                        m.get("p_gain", 1.0))
    except Exception as e:
        print("CSE7766 init failed:", e)
else:
    print("SAFE MODE: 总键按住，跳过 UART swap，串口 REPL 保留")


def relay_write(ch, on):
    u7.write_pin(RELAY_BIT[ch], 1 if on else 0)
    relay_state[ch] = bool(on)


def logo_write(on):
    logo.value(0 if on else 1)
    state["logo"] = bool(on)


def save_state():
    save_json(STATE_FILE, state)


def set_plug(ch, on, _internal=False):
    """总闸/分路互锁 + 断电记忆，语义与官方 App 一致。"""
    if ch == "logo":
        logo_write(on)
        save_state()
        return

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
        return

    if ch not in ("1", "2", "3"):
        raise ValueError("bad ch")
    if on and not relay_state["total"]:
        relay_write("total", True)
        state["total"] = True
        logo_write(True)
    relay_write(ch, on)
    state[ch] = relay_state[ch]
    save_state()


def restore_state():
    s = load_json(STATE_FILE, None)
    if not s:
        return
    for k in PLUGS:
        if s.get(k):
            if k == "total":
                relay_write("total", True)
                state["total"] = True
            elif k in ("1", "2", "3"):
                relay_write(k, True)
                state[k] = True
            elif k == "logo":
                logo_write(True)


restore_state()


# ---------------------------------------------------------------- 按键
key_prev = {"1": 1, "2": 1, "3": 1}
key_t = {"1": 0, "2": 0, "3": 0}
tot_prev = 0
tot_t = 0
DEBOUNCE = 50


def poll_keys():
    global tot_prev, tot_t
    raw = u7.read_all()
    now = time.ticks_ms()
    for ch, bit in KEY_BIT.items():
        v = (raw >> bit) & 1                 # 低有效
        if v != key_prev[ch]:
            key_prev[ch] = v
            key_t[ch] = now
        elif v == 0 and time.ticks_diff(now, key_t[ch]) > DEBOUNCE:
            if key_t[ch] and time.ticks_diff(now, key_t[ch]) < 1200:
                key_t[ch] = 0                # 一次按下只触发一次
                set_plug(ch, not state[ch])
    v = pin_total.value()
    if v != tot_prev:
        tot_prev = v
        tot_t = now
    elif v == 1 and time.ticks_diff(now, tot_t) > DEBOUNCE:
        if tot_t and time.ticks_diff(now, tot_t) < 1200:
            tot_t = 0
            set_plug("total", not state["total"])


# ---------------------------------------------------------------- 网络
def wifi_sta():
    w = network.WLAN(network.STA_IF)
    w.active(True)
    if cfg.get("hostname"):
        try:
            w.config(dhcp_hostname=cfg["hostname"])
        except Exception:
            pass
    if not cfg.get("ssid"):
        return None
    try:
        w.connect(cfg["ssid"], cfg["password"])
    except Exception:
        return None
    t0 = time.ticks_ms()
    while not w.isconnected():
        if time.ticks_diff(time.ticks_ms(), t0) > 12000:
            return None
        time.sleep_ms(200)
    return w


def wifi_ap():
    a = network.WLAN(network.AP_IF)
    a.active(True)
    try:
        a.config(essid=cfg_get("ap_ssid"), password=cfg_get("ap_pass"),
                 authmode=3)
    except Exception:
        a.config(essid=cfg_get("ap_ssid"), password=cfg_get("ap_pass"))
    a.ifconfig(("192.168.4.1", "255.255.255.0", "192.168.4.1", "192.168.4.1"))
    return a


def dns_socket():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setblocking(False)
    try:
        s.bind(("0.0.0.0", 53))
    except Exception:
        return None
    return s


def dns_answer(s):
    """把所有域名都解析到 192.168.4.1，触发手机强制门户。"""
    try:
        data, addr = s.recvfrom(256)
    except Exception:
        return
    if len(data) < 13:
        return
    i = 12
    while i < len(data) and data[i]:
        i += data[i] + 1
    qend = i + 5
    if qend > len(data):
        return
    rsp = (data[:2] + b"\x81\x80" + b"\x00\x01\x00\x01\x00\x00\x00\x00" +
           data[12:qend] + b"\xc0\x0c" + b"\x00\x01\x00\x01" +
           b"\x00\x00\x00\x3c" + b"\x00\x04" + bytes([192, 168, 4, 1]))
    try:
        s.sendto(rsp, addr)
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
    path, _, q = target.partition("?")
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
            "Connection: close\r\nCache-Control: no-store\r\n\r\n" %
            (code, ctype, len(body)))
    try:
        conn.send(head.encode() + body)
    except Exception:
        pass


def state_json():
    d = {k: bool(v) for k, v in state.items()}
    d["meter"] = meter.state() if meter else {"voltage": 0, "current": 0,
                                              "power": 0}
    d["ip"] = w.ifconfig()[0] if (w is not None) else "192.168.4.1"
    try:
        d["rssi"] = w.status("rssi") if w is not None else 0
    except Exception:
        d["rssi"] = 0
    d["uptime"] = time.ticks_diff(time.ticks_ms(), boot_ms) // 1000
    d["ap"] = ap_mode
    return json.dumps(d)


def check_token(headers, q):
    want = cfg.get("api_token") or ""
    if not want:
        return True
    got = q.get("t") or headers.get("x-token", "")
    return got == want


def handle(conn):
    global reset_pending
    conn.settimeout(3)
    data = b""
    while b"\r\n\r\n" not in data and len(data) < 4096:
        try:
            chunk = conn.recv(512)
        except Exception:
            break
        if not chunk:
            break
        data += chunk
    if not data:
        return
    head, _, body = data.partition(b"\r\n\r\n")
    lines = head.split(b"\r\n")
    try:
        parts = lines[0].decode().split(" ")
        method, target = parts[0], parts[1]
    except Exception:
        return reply(conn, "400 Bad Request", '{"err":"bad request"}')

    hdrs = {}
    clen = 0
    for ln in lines[1:]:
        s = ln.decode(errors="ignore")
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

    if path in ("/", "/index.html"):
        return reply(conn, "200 OK", webui.PAGE, "text/html; charset=utf-8")

    if path == "/api/ping":
        return reply(conn, "200 OK", '{"ok":1,"name":"dc1-mpy"}')

    if path == "/api/state":
        return reply(conn, "200 OK", state_json())

    if path == "/api/switch":
        ch = q.get("ch", "total")
        if ch not in PLUGS:
            return reply(conn, "400 Bad Request", '{"err":"bad ch"}')
        on = q.get("on", "1") in ("1", "true", "on", "ON")
        try:
            set_plug(ch, on)
        except Exception as e:
            return reply(conn, "500 Internal", '{"err":"%s"}' % e)
        return reply(conn, "200 OK", state_json())

    if path == "/api/meter":
        return reply(conn, "200 OK", json.dumps(meter.state() if meter else {}))

    if path == "/api/config":
        if not check_token(hdrs, q):
            return reply(conn, "401 Unauthorized", '{"err":"token"}')
        if method == "GET":
            safe = {k: cfg.get(k) for k in cfg if k not in ("password",)}
            return reply(conn, "200 OK", json.dumps(safe))
        try:
            new = json.loads(body.decode())
        except Exception:
            return reply(conn, "400 Bad Request", '{"err":"json"}')
        for k, v in new.items():
            cfg[k] = v
        apply_cfg()
        save_json(CFG_FILE, cfg)
        reset_pending = True
        return reply(conn, "200 OK", '{"ok":1,"reboot":1}')

    if path == "/api/file":
        if not check_token(hdrs, q):
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

    return reply(conn, "404 Not Found", '{"err":"404"}')


def http_server():
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("0.0.0.0", int(cfg_get("http_port"))))
    except Exception as e:
        print("bind failed", e)
        return None
    s.listen(4)
    s.settimeout(0.25)
    return s


# ---------------------------------------------------------------- 主流程
cfg = load_json(CFG_FILE, {})
for k, v in DEFAULT_CFG.items():
    cfg.setdefault(k, v)
apply_cfg()

w = wifi_sta()
if w is None:
    ap_mode = True
    wifi_ap()
    dns = dns_socket()
    print("AP mode: %s / %s -> 192.168.4.1" % (cfg_get("ap_ssid"),
                                               cfg_get("ap_pass")))
else:
    dns = None
    print("STA ok:", w.ifconfig())

srv = http_server()
print("http on", cfg_get("http_port"))
gc.collect()

last_ui = 0
while True:
    if reset_pending:
        time.sleep_ms(300)
        import machine
        machine.reset()

    if srv is not None:
        try:
            conn, _ = srv.accept()
            try:
                handle(conn)
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
        except OSError:
            pass
        except Exception as e:
            print("http err", e)

    if dns is not None:
        dns_answer(dns)

    poll_keys()
    if meter:
        meter.poll()
    else:
        time.sleep_ms(20)

    if time.ticks_diff(time.ticks_ms(), last_ui) > 60000:
        last_ui = time.ticks_ms()
        gc.collect()
