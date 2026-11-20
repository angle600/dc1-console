"""斐讯 TC1 智能插排 —— MicroPython 固件 v1.0（ESP8266 换脑方案）

背景：TC1 原厂主控是 A1=MXCHIP EMW3031（MiCO）、A2=HF-LPT230（汉枫固件），
两者都跑不了 MicroPython。本固件对应"拆模组 + 焊 ESP-12S 转接板"的换脑方案，
对外协议与 DC1 完全一致，所以 `cli/dc1ctl.py` 与 Qt 上位机可以直接复用。

对外能力（内网自持，无云）：
  * UDP 54321 广播发现：{"t":"discover"} → 回设备信息（type="TC1"）
  * 上电 **30 秒内**接受 UDP 配网：{"t":"prov","ssid":..,"pass":..}
  * HTTP JSON API：6 路开关（total 为虚拟总控）/ 定时 / 倒计时 / 功率 / 电量
  * PUT /api/put：WiFi 覆盖源码（app.mpy 热更新，带 sha256 校验与 .bak 回滚）
  * 按键：短按 = 6 路总开关；长按 3 s = 强制进 AP 配网并重启
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

import board
import bl0937
import store

FW_VERSION = "1.0.0"
TYPE = "TC1"
UDP_PORT = 54321
PROV_WINDOW_MS = 30000
CFG_FILE = "config.json"
STATE_FILE = "state.json"
N_RELAY = 6

PLUGS = ("total", "1", "2", "3", "4", "5", "6")

DEFAULT_CFG = {
    "ssid": "", "password": "", "ap_ssid": "TC1-Setup", "ap_pass": "",
    "name": "TC1", "hostname": "tc1", "http_port": 80, "api_token": "",
    # 首次上电（无 state.json）时是否全开。默认关：换脑后的设备第一次通电
    # 就吸合 6 路继电器，对桌上那堆负载不够友好；要"上电全开"改成 True。
    "boot_default_on": False,
    "k_imp": bl0937.DEFAULT_K,      # 脉冲常数，用 /api/cal 标定后覆盖
    "hw": True,
}

boot_ms = time.ticks_ms()
state = {str(i): False for i in range(1, N_RELAY + 1)}
w = None
ap_mode = False
prov_open = True
reset_pending = False
force_ap_next = False
wd_ignore_until = 0
uid = "tc1-0000"

relays = None
led = None
key = None
meter = None
sched = None
cd = None
energy = None


# ---------------------------------------------------------------- 小工具
def log(msg):
    """日志只落 boot.log（HTTP GET /api/log 读）。

    不占用 UART0：GPIO3(RX) 被 CF 脉冲中断占用，GPIO1(TX) 留给烧录/救砖。
    """
    try:
        with open("boot.log", "a") as f:
            f.write("%s\n" % msg)
    except Exception:
        pass


def log_err(where, e):
    txt = "ERR[%s] %r" % (where, e)
    log(txt)
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


# ---------------------------------------------------------------- 开关
def save_state():
    save_json(STATE_FILE, state)


def restore_state(default_on=False):
    """state.json 缺失时按 default_on 全部置位（默认关）。"""
    global state
    d = load_json(STATE_FILE, None)
    for i in range(1, N_RELAY + 1):
        k = str(i)
        state[k] = bool(d.get(k, default_on)) if isinstance(d, dict) else default_on
    if relays is not None:
        for i in range(1, N_RELAY + 1):
            relays.write(i - 1, state[str(i)])


def total_on():
    for k in state:
        if not state[k]:
            return False
    return True


def set_plug(ch, on):
    """ch: "total" 或 "1".."6"。total 是虚拟总控（6 路一起写）。"""
    ch = str(ch)
    if ch == "total":
        targets = [str(i) for i in range(1, N_RELAY + 1)]
    elif ch in PLUGS:
        targets = [ch]
    else:
        raise ValueError("bad ch")
    for k in targets:
        state[k] = bool(on)
        if relays is not None:
            relays.write(int(k) - 1, on)
    save_state()
    log("relay: %s=%d total=%d" % (ch, 1 if on else 0, 1 if total_on() else 0))
    return targets


# ---------------------------------------------------------------- 硬件
def init_hw():
    global relays, led, key, meter
    if not cfg.get("hw", True):
        log("hw skipped")
        return
    relays = board.Relays(active_low=board.RELAY_ACTIVE_LOW)
    led = board.Led()
    key = board.Key()
    try:
        meter = bl0937.BL0937(k_imp=float(cfg_get("k_imp")))
    except Exception as e:
        log("bl0937 fail: %s" % e)
        meter = None
    log("hw: relays=%d cf=%s led=%s adc_key=%d" % (
        len(relays), meter is not None, led.pin is not None, key.raw()))


def state_json():
    """直接拼字符串，避免 dict + json.dumps 的两轮分配（1MB 版堆很紧）。"""
    m = meter.state() if meter else {"voltage": 0, "current": 0, "power": 0,
                                     "p_raw": 0, "k_imp": 0, "pulses": 0,
                                     "calibrated": 0}
    e = energy.state() if energy else {"total_kwh": 0, "today_kwh": 0}
    sw = ",".join('"%d":%d' % (i, 1 if state[str(i)] else 0)
                  for i in range(1, N_RELAY + 1))
    try:
        rssi = w.status("rssi") if w else 0
    except Exception:
        rssi = 0
    return ('{"total":%d,%s,"meter":{"voltage":0,"current":0,"power":%s,'
            '"p_raw":%s,"k_imp":%s,"pulses":%d,"calibrated":%d},'
            '"energy":{"total_kwh":%s,"today_kwh":%s},"cd":%s,"sched":%d,'
            '"type":"%s","fw":"%s","ip":"%s","ap":%d,"prov":%d,"time":%d,'
            '"rssi":%d}'
            % (1 if total_on() else 0, sw, m.get("power", 0),
               m.get("p_raw", 0), m.get("k_imp", 0), m.get("pulses", 0),
               m.get("calibrated", 0),
               e.get("total_kwh", 0), e.get("today_kwh", 0),
               json.dumps(cd.all() if cd else []), len(sched.all()) if sched else 0,
               TYPE, FW_VERSION, ip_now(), 1 if ap_mode else 0,
               1 if prov_open else 0, int(time.time()), rssi))


def info_obj():
    return {"t": "dev", "proto": 1, "type": TYPE, "id": uid,
            "name": cfg_get("name"), "fw": FW_VERSION, "mac": mac_str(),
            "ip": ip_now(), "port": int(cfg_get("http_port")),
            "prov": 1 if prov_open else 0,
            "plugs": list(PLUGS),
            "features": ["switch", "schedule", "countdown", "meter-power",
                         "energy", "ota-py", "prov-udp"]}


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


# ---------------------------------------------------------------- HTTP 工具
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
        conn.send(head.encode() + body)
    except Exception as e:
        log_err("send", e)


def serve_file(conn, name, ctype):
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


def need_token(hdrs, q):
    want = cfg.get("api_token") or ""
    if not want:
        return True
    return (q.get("t") or hdrs.get("x-token", "")) == want


# ---------------------------------------------------------------- 路由
def handle(conn):
    global reset_pending
    try:
        gc.collect()
    except Exception:
        pass
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
    if not data:
        return

    sep = data.find(b"\r\n\r\n")
    if sep < 0:
        head, body = data, b""
    else:
        head, body = data[:sep], data[sep + 4:]
    lines = head.split(b"\r\n")
    try:
        p0 = lines[0].decode().split(" ")
        method, target = p0[0], p0[1]
    except Exception:
        return reply(conn, "400 Bad Request", '{"err":"bad"}')

    hdrs = {}
    clen = 0
    for ln in lines[1:]:
        try:
            s = ln.decode()
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
    try:
        gc.collect()
    except Exception:
        pass
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
    if path == "/api/hw":
        d = {"relays": len(relays) if relays else 0,
             "relay_state": relays.snapshot() if relays else [],
             "cf": meter is not None,
             "key_adc": key.raw() if key else -1,
             "active_low": board.RELAY_ACTIVE_LOW}
        return reply(conn, "200 OK", json.dumps(d))
    if path == "/api/state":
        return reply(conn, "200 OK", state_json())

    if path == "/api/switch":
        ch = q.get("ch", "total")
        if ch not in PLUGS:
            return reply(conn, "400 Bad Request", '{"err":"bad ch"}')
        on = q.get("on", "1") in ("1", "true", "on", "ON")
        set_plug(ch, on)
        return reply(conn, "200 OK", state_json())

    if path == "/api/schedule":
        if method == "GET":
            return reply(conn, "200 OK", json.dumps(sched.all()))
        op = js.get("op")
        try:
            if op == "add":
                if str(js.get("ch", "1")) not in PLUGS:
                    raise ValueError("bad ch")
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
                if str(js.get("ch", "1")) not in PLUGS:
                    raise ValueError("bad ch")
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

    if path == "/api/meter":
        return reply(conn, "200 OK", json.dumps(
            meter.state() if meter else {"voltage": 0, "current": 0,
                                         "power": 0}))
    if path == "/api/energy":
        return reply(conn, "200 OK", json.dumps(energy.state()))

    # 单点标定：POST {"power": 100} 用外部功率计读数反算脉冲常数 K
    if path == "/api/cal":
        if method == "GET":
            return reply(conn, "200 OK", json.dumps(
                meter.state() if meter else {}))
        if not need_token(hdrs, q):
            return reply(conn, "401 Unauthorized", '{"err":"token"}')
        try:
            wref = float(js.get("power") or q.get("power") or 0)
            if meter is None:
                raise ValueError("meter off")
            if wref <= 0:
                return reply(conn, "400 Bad Request", '{"err":"need power"}')
            k = meter.cal_by_ref(wref)
            cfg["k_imp"] = round(k, 2)
            save_json(CFG_FILE, cfg)
            return reply(conn, "200 OK", json.dumps(
                {"ok": 1, "k_imp": round(k, 2)}))
        except Exception as e:
            return reply(conn, "400 Bad Request", '{"err":"%s"}' % e)

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

    if path == "/api/config":
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
        if js.get("ssid") and js.get("try_connect", ap_mode):
            ssid = js.get("ssid")
            password = js.get("password") or ""
            ok, ip = sta_try(ssid, password)
            if not ok:
                return reply(conn, "200 OK", '{"ok":0,"err":"connect_fail"}')
            cfg["ssid"] = ssid
            cfg["password"] = password
            save_json(CFG_FILE, cfg)
            log("prov saved ssid=%s ip=%s" % (ssid, ip))
            reset_pending = True
            return reply(conn, "200 OK", '{"ok":1,"ip":"%s","saved":1}' % ip)
        for k, v in js.items():
            if k in ("ssid", "password", "name", "ap_ssid", "ap_pass",
                     "api_token", "http_port", "hostname", "hw",
                     "boot_default_on", "k_imp"):
                cfg[k] = v
        save_json(CFG_FILE, cfg)
        if js.get("k_imp") and meter is not None:
            meter.k = float(js["k_imp"])
        if js.get("reboot"):
            reset_pending = True
        return reply(conn, "200 OK", '{"ok":1}')

    if path == "/api/reboot":
        if not need_token(hdrs, q):
            return reply(conn, "401 Unauthorized", '{"err":"token"}')
        reset_pending = True
        return reply(conn, "200 OK", '{"ok":1}')

    if path == "/api/log":
        return reply(conn, "200 OK", json.dumps(
            {"log": read_log("boot.log"), "err": read_log("err.log")}))

    # 源码热更新（`cli/dc1ctl.py fw update` 用的就是这个口）：
    # PUT /api/file?name=app.py + 原始 body，先写 .new 再改名，失败不留半截文件
    if path == "/api/file" and method == "PUT":
        if not need_token(hdrs, q):
            return reply(conn, "401 Unauthorized", '{"err":"token"}')
        try:
            name = q.get("name", "")
            if not name or "/" in name or name.startswith("."):
                raise ValueError("bad name")
            if not body:
                raise ValueError("empty body")
            tmp = name + ".new"
            with open(tmp, "wb") as f:
                f.write(body)
            try:
                os.remove(name + ".bak")
            except Exception:
                pass
            try:
                os.rename(name, name + ".bak")
            except Exception:
                pass
            os.rename(tmp, name)
            return reply(conn, "200 OK", json.dumps(
                {"ok": 1, "name": name, "committed": len(body)}))
        except Exception as e:
            return reply(conn, "400 Bad Request",
                         json.dumps({"ok": 0, "err": str(e)}))

    # 源码热更新（UDP 版）：先写 .new，全部发完再校验 sha256 → 改名提交
    if path == "/api/put" and method == "POST":
        if not need_token(hdrs, q):
            return reply(conn, "401 Unauthorized", '{"err":"token"}')
        try:
            name = js.get("name", "")
            off = int(js.get("off", 0))
            if not name or "/" in name or name.startswith("."):
                raise ValueError("bad name")
            tmp = name + ".new"
            if js.get("done"):
                pass
            else:
                raw = ubinascii.a2b_base64(js.get("b64", ""))
                mode = "r+b" if off else "wb"
                with open(tmp, mode) as f:
                    f.seek(off)
                    f.write(raw)
            nxt = off + (0 if js.get("done") else len(raw))
            if js.get("done"):
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
                try:
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
            return reply(conn, "400 Bad Request",
                         json.dumps({"ok": 0, "err": str(e)}))

    return reply(conn, "404 Not Found", '{"err":"404"}')


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
    try:
        a.config(essid=cfg_get("ap_ssid"))
    except Exception:
        pass
    # 配网 AP 开放（免密码），与 DC1 一致：手机连上即用
    try:
        a.config(password="")
    except Exception:
        pass
    try:
        a.config(authmode=0)
    except Exception:
        pass
    try:
        a.ifconfig(("192.168.4.1", "255.255.255.0", "192.168.4.1", "192.168.4.1"))
    except Exception:
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
    except Exception:
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


# ---------------------------------------------------------------- 启动
cfg = load_json(CFG_FILE, {})
for k, v in DEFAULT_CFG.items():
    cfg.setdefault(k, v)

_id = machine.unique_id()
uid = "tc1-%02x%02x" % (_id[-2], _id[-1])

try:
    open("boot.log", "w").close()
except Exception:
    pass

sched = store.Sched()
cd = store.Countdown()
energy = store.Energy()

# 长按进 AP 的标记：上电先读，读到就强制 AP 并删标记
try:
    if "ap.flag" in os.listdir():
        os.remove("ap.flag")
        force_ap_next = True
except Exception:
    pass

log("boot start FW=%s id=%s free=%d" % (FW_VERSION, uid, gc.mem_free()))

w = None if force_ap_next else wifi_sta()
if w is None:
    ap_mode = True
    wifi_ap()
else:
    try:
        network.WLAN(network.AP_IF).active(False)
    except Exception:
        pass

us = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
us.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
us.setblocking(False)
try:
    us.bind(("0.0.0.0", UDP_PORT))
except Exception as e:
    log("udp bind fail: %s" % e)
    us = None

srv = socket.socket()
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv_poll = None
try:
    srv.bind(("0.0.0.0", int(cfg_get("http_port"))))
    srv.listen(4)
    srv_poll = select.poll()
    srv_poll.register(srv, select.POLLIN)
except Exception as e:
    log("http bind fail: %s" % e)
    srv = None

try:
    if not ap_mode:
        log("sntp: %s" % ("ok" if sntp_sync() else "fail"))
except Exception as e:
    log("sntp err: %s" % e)

# 硬件必须在 WiFi/HTTP 起来之后初始化：继电器吸合会拉电流，先保网络
try:
    init_hw()
    restore_state(bool(cfg_get("boot_default_on")))
    log("relay init: %s" % relays.snapshot())
except Exception as e:
    log_err("init_hw", e)

log("TC1 %s id=%s ip=%s ap=%d" % (FW_VERSION, uid, ip_now(), 1 if ap_mode else 0))
try:
    gc.collect()
    log("free: %d" % gc.mem_free())
except Exception:
    pass

last = time.ticks_ms()
last_iter = last
last_gc = last
last_led = last
loop_n = 0
led_solid = False


def on_key(ev):
    """短按 = 总开关；长按 = 写标记后重启进 AP。"""
    global reset_pending
    if ev == "long":
        log("key long: force AP")
        try:
            with open("ap.flag", "w") as f:
                f.write("1")
        except Exception:
            pass
        reset_pending = True
        return
    if ev == "short":
        target = not total_on()
        set_plug("total", target)
        log("key short: total=%d" % (1 if target else 0))


while True:
    loop_n += 1
    now = time.ticks_ms()
    stall = time.ticks_diff(now, last_iter)
    if stall > 8000 and time.ticks_diff(wd_ignore_until, now) <= 0:
        log_err("watchdog", "stall %d ms, rebooting" % stall)
        time.sleep_ms(150)
        machine.reset()
    last_iter = now

    if reset_pending:
        time.sleep_ms(300)
        machine.reset()

    # ---------------- 按键 / LED
    try:
        if key is not None:
            ev = key.poll()
            if ev:
                on_key(ev)
    except Exception as e:
        log_err("key", e)

    try:
        if led is not None:
            online = (w is not None and not ap_mode and w.isconnected())
            if online and not led_solid:
                led.set(True)
                led_solid = True
            elif not online:
                led_solid = False
                led.blink_tick(now, 1000)
    except Exception as e:
        log_err("led", e)

    # ---------------- 计量
    try:
        if meter is not None and meter.poll():
            pass
    except Exception as e:
        log_err("meter", e)

    dt = time.ticks_diff(now, last)
    last = now
    if dt > 0 and dt < 5000 and meter is not None:
        try:
            energy.add(meter.power, dt)
        except Exception as e:
            log_err("energy", e)

    if prov_open and time.ticks_diff(now, boot_ms) > PROV_WINDOW_MS:
        prov_open = False

    # ---------------- HTTP
    if srv is not None and srv_poll is not None:
        conn = None
        try:
            if srv_poll.poll(0):
                conn, _ = srv.accept()
        except Exception:
            conn = None
        if conn is not None:
            try:
                handle(conn)
            except Exception as e:
                log_err("handle", e)
            finally:
                try:
                    time.sleep_ms(40)
                    conn.close()
                except Exception:
                    pass

    # ---------------- UDP
    if us is not None:
        try:
            udp_serve(us)
        except Exception as e:
            log_err("udp", e)

    # ---------------- 定时 / 倒计时
    try:
        for ch, on, rid in sched.due():
            set_plug(ch, on)
        for ch, on, cid in cd.tick(dt):
            set_plug(ch, on)
    except Exception as e:
        log_err("sched", e)

    if time.ticks_diff(now, last_gc) > 60000:
        last_gc = now
        try:
            gc.collect()
        except Exception:
            pass
