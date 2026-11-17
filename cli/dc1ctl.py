#!/usr/bin/env python3
"""dc1ctl —— DC1 内网工具链（纯标准库，GUI 也调它，保证 GUI/CLI 同一套逻辑）

子命令：
    discover                         UDP 54321 广播发现（含未配网设备）
    prov --ssid X --pass Y           上电 30 秒内配网（UDP）
    ping / info                      连通性与设备身份
    state                            开关 + 计量 + 电量 + 倒计时
    switch --ch 1 --on 1             开关（ch: total/1/2/3/logo）
    sched list|add|del|clear         定时：--once 单次，--days 1,2,3 / --daily 每周重复
    countdown list|add|del|clear     倒计时：--ch 1 --on 0 --min 30
    meter                            电压/电流/功率/原始计数
    time                             用本机时间校准设备（定时功能需要）
    fw update --dir firmware/micropython    WiFi 覆盖源码（MicroPython 固件更新）
    fw core --serial COM5 --img x.bin       首次烧内核（转调 esptool）

通用参数：--ip / --id 指定设备，--json 输出机器可读结果，--token API token
"""

import argparse
import json
import os
import pathlib
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UDP_PORT = 54321
PROTO = 1
ROOT = pathlib.Path(__file__).resolve().parent.parent
DEF_TOKEN = os.environ.get("DC1_TOKEN", "")


# ------------------------------------------------------------------ 发现 / 配网
def udp_send(payload, targets=("255.255.255.255",), timeout=2.0, want=None):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.settimeout(0.4)
    msg = json.dumps(payload).encode()
    for t in targets:
        try:
            s.sendto(msg, (t, UDP_PORT))
        except OSError:
            pass
    found = []
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            data, addr = s.recvfrom(2048)
        except socket.timeout:
            continue
        except OSError:
            break
        try:
            obj = json.loads(data.decode())
        except Exception:
            continue
        if want and obj.get("t") != want:
            continue
        obj.setdefault("ip", addr[0])
        obj["_from"] = addr[0]
        if not any(f.get("id") == obj.get("id") for f in found):
            found.append(obj)
        if want == "prov":
            break
    s.close()
    return found


def discover(timeout=2.5):
    targets = ["255.255.255.255", "192.168.4.1"]
    devs = udp_send({"t": "discover", "proto": PROTO}, targets, timeout)
    devs.sort(key=lambda d: (d.get("type"), d.get("name") or ""))
    return devs


def resolve(args):
    """按 --ip / --dev / 自动发现 选一台设备，返回 host"""
    if getattr(args, "ip", None):
        return args.ip
    devs = [d for d in discover(2.0) if d.get("ip")]
    if getattr(args, "dev", None):
        for d in devs:
            if d.get("id") == args.dev:
                return d["ip"]
        raise SystemExit("找不到设备 dev=%s（先 discover 看看）" % args.dev)
    if not devs:
        raise SystemExit("没有发现任何 DC1 设备；确认同网段/未被 AP 隔离")
    return devs[0]["ip"]


# ------------------------------------------------------------------ HTTP
def http(host, path, method="GET", body=None, token="", timeout=8):
    url = "http://%s%s" % (host, path)
    data = None
    if body is not None:
        data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method)
    if token:
        req.add_header("X-Token", token)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return json.loads(raw.decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:
            return {"err": "HTTP %s" % e.code}
    except Exception as e:
        return {"err": "%s" % e}


def get_json(host, path, token="", timeout=8):
    return http(host, path, token=token, timeout=timeout)


# ------------------------------------------------------------------ 输出
def emit(obj, as_json, human=None):
    if as_json:
        print(json.dumps(obj, ensure_ascii=False, indent=2))
    elif human is not None:
        print(human)
    else:
        print(json.dumps(obj, ensure_ascii=False, indent=2))


def fmt_state(s):
    sw = "  ".join("%s=%s" % (k, "ON " if s.get(k) else "OFF")
                   for k in ("total", "1", "2", "3", "logo"))
    m = s.get("meter") or {}
    e = s.get("energy") or {}
    lines = [sw,
             "V=%.1f  A=%.3f  W=%.1f" % (m.get("voltage", 0),
                                          m.get("current", 0),
                                          m.get("power", 0)),
             "今日 %.3f kWh / 累计 %.3f kWh" % (e.get("today_kwh", 0),
                                                e.get("total_kwh", 0)),
             "定时 %s 条，倒计时 %s 条" % (s.get("sched", 0), len(s.get("cd") or []))]
    return "\n".join(lines)


DOW = "一二三四五六日"


def fmt_rules(rules):
    if not rules:
        return "（无）"
    out = []
    for r in rules:
        when = "单次" if not r.get("days") else (
            "每天" if r["days"] == 127 else
            "周" + "".join(DOW[i] for i in range(7) if (r["days"] >> i) & 1))
        out.append("  #%d  %s  %02d:%02d %s %s" %
                   (r["id"], r.get("ch"), r.get("hh", 0), r.get("mm", 0),
                    "开" if r.get("on") else "关", when))
    return "\n".join(out)


# ------------------------------------------------------------------ 命令
def cmd_discover(a):
    devs = discover(a.timeout)
    if a.json:
        return emit(devs, True)
    if not devs:
        print("没有发现设备")
        return
    print("%-10s %-6s %-14s %-6s %-5s %s" %
          ("ID", "TYPE", "IP", "FW", "PROV", "NAME"))
    for d in devs:
        print("%-10s %-6s %-14s %-6s %-5s %s" %
              (d.get("id"), d.get("type"), d.get("ip"), d.get("fw"),
               d.get("prov"), d.get("name")))


def cmd_prov(a):
    payload = {"t": "prov", "proto": PROTO, "ssid": a.ssid, "pass": a.password}
    if a.name:
        payload["name"] = a.name
    one = a.target or getattr(a, "ip", None)
    targets = [one] if one else ["255.255.255.255", "192.168.4.1"]
    res = udp_send(payload, targets, 3.0, want="prov")
    if not res:
        emit({"ok": 0, "err": "no_reply",
              "hint": "设备需在上电 30 秒内；确认同一网段"}, a.json,
             "配网失败：无响应（30 秒窗口已过？同网段？）")
        sys.exit(1)
    r = res[0]
    if r.get("ok"):
        emit(r, a.json, "配网成功，设备正在重启并连接 %s" % a.ssid)
    else:
        emit(r, a.json, "配网失败：%s" % r.get("err"))
        sys.exit(1)


def cmd_ping(a):
    host = resolve(a)
    r = get_json(host, "/api/ping", a.token, timeout=5)
    ok = bool(r.get("ok"))
    emit(r, a.json, "OK %s %s" % (r.get("type"), r.get("fw")) if ok
         else "失败：%s" % r.get("err"))


def cmd_state(a):
    host = resolve(a)
    s = get_json(host, "/api/state", a.token)
    if "err" in s:
        sys.exit("读取失败：%s" % s["err"])
    emit(s, a.json, fmt_state(s))


def cmd_switch(a):
    host = resolve(a)
    s = get_json(host, "/api/switch?ch=%s&on=%d" % (a.ch, 1 if a.on else 0),
                 a.token)
    if "err" in s:
        sys.exit("操作失败：%s" % s["err"])
    emit(s, a.json, fmt_state(s))


def cmd_sched(a):
    host = resolve(a)
    if a.op == "list":
        rules = get_json(host, "/api/schedule", a.token)
        emit(rules, a.json, fmt_rules(rules if isinstance(rules, list) else []))
        return
    if a.op == "clear":
        body = {"op": "clear"}
    elif a.op == "del":
        body = {"op": "del", "id": a.id_num}
    else:
        hh, mm = 0, 0
        if a.at:
            hh, mm = [int(x) for x in a.at.split(":")[:2]]
        if a.once:
            days = 0
        elif a.mask is not None:
            days = int(a.mask) & 0x7F
        elif a.days:
            days = 0
            for d in a.days.split(","):
                days |= 1 << ((int(d) - 1) % 7)
        else:
            days = 127
        body = {"op": "add", "ch": a.ch, "on": 1 if a.on else 0,
                "hh": hh, "mm": mm, "days": days}
    r = http(host, "/api/schedule", "POST", body, a.token)
    emit(r, a.json, "OK" if r.get("ok") else "失败：%s" % r.get("err"))


def cmd_countdown(a):
    host = resolve(a)
    if a.op == "list":
        items = get_json(host, "/api/countdown", a.token)
        if a.json:
            return emit(items, True)
        if not items:
            return print("（无）")
        for i in items:
            print("  #%d  %s  %s  %.0fs 后" %
                  (i["id"], i.get("ch"), "开" if i.get("on") else "关",
                   i.get("left", 0)))
        return
    if a.op == "clear":
        body = {"op": "clear"}
    elif a.op == "del":
        body = {"op": "del", "id": a.id_num}
    else:
        body = {"op": "add", "ch": a.ch, "on": 1 if a.on else 0,
                "sec": int(a.min * 60) if a.min else 60}
    r = http(host, "/api/countdown", "POST", body, a.token)
    emit(r, a.json, "OK" if r.get("ok") else "失败：%s" % r.get("err"))


def cmd_meter(a):
    host = resolve(a)
    m = get_json(host, "/api/meter", a.token)
    emit(m, a.json, "V=%.1f A=%.3f W=%.1f  (raw v=%s i=%s p=%s)" %
         (m.get("voltage", 0), m.get("current", 0), m.get("power", 0),
          m.get("v_raw"), m.get("i_raw"), m.get("p_raw")))


def cmd_time(a):
    host = resolve(a)
    r = http(host, "/api/time", "POST", {"epoch": int(time.time())}, a.token)
    emit(r, a.json, "设备时间已校准" if r.get("ok") else "失败：%s" % r.get("err"))


def cmd_cal(a):
    """单点标定：用外部电表读数校正 V/A/W。"""
    host = resolve(a)
    if not (a.power or a.voltage or a.current or a.reset):
        r = get_json(host, "/api/cal", a.token)
        m = r.get("meter") or {}
        me = r.get("measured") or {}
        emit(r, a.json,
             "当前读数  V=%.1f A=%.3f W=%.1f\n系数  v=%.6f i=%.6f p=%.6f\n"
             "芯片%s，异常标志 0x%x" %
             (me.get("voltage", 0), me.get("current", 0), me.get("power", 0),
              m.get("v_gain", 1), m.get("i_gain", 1), m.get("p_gain", 1),
              "已校准" if r.get("calibrated") else "未校准",
              r.get("flags", 0)))
        return
    body = {}
    if a.reset:
        body["reset"] = 1
    if a.voltage:
        body["voltage"] = a.voltage
    if a.current:
        body["current"] = a.current
    if a.power:
        body["power"] = a.power
    r = http(host, "/api/cal", "POST", body, a.token)
    if r.get("ok"):
        g = r.get("gains") or {}
        emit(r, a.json, "标定完成：v=%.6f i=%.6f p=%.6f" %
             (g.get("v_gain", 1), g.get("i_gain", 1), g.get("p_gain", 1)))
    else:
        emit(r, a.json, "失败：%s %s" % (r.get("err"), r.get("hint", "")))
        sys.exit(1)


def cmd_reboot(a):
    host = resolve(a)
    r = http(host, "/api/reboot", "POST", {}, a.token)
    emit(r, a.json, "设备重启中…" if r.get("ok") else "失败：%s" % r.get("err"))


def cmd_fw(a):
    if a.action == "core":
        if not (a.serial and a.img):
            sys.exit("fw core 需要 --serial 与 --img")
        cmds = [["esptool.py", "--port", a.serial, "erase_flash"],
                ["esptool.py", "--port", a.serial, "--baud", "460800",
                 "write_flash", "--flash_size=1MB", "0x0", a.img]]
        for c in cmds:
            print("+", " ".join(c))
            subprocess.run(c, check=True)
        return
    host = resolve(a)
    d = pathlib.Path(a.dir or (ROOT / "firmware" / "micropython"))
    if not d.exists():
        sys.exit("目录不存在：%s" % d)
    names = a.only or ["main.py", "store.py", "cat9554.py", "cse7766.py",
                       "webui.py", "config.json"]
    mpy_dir = pathlib.Path(a.mpy_dir or (ROOT / "firmware" / "build" / "mpy"))
    ok, fail = [], []
    for n in names:
        p = d / n
        if not p.exists():
            if n == "config.json":
                continue
            fail.append((n, "missing"))
            continue
        up_name, payload = n, p.read_bytes()
        if a.mpy and n.endswith(".py") and n != "main.py":
            m = mpy_dir / (n[:-3] + ".mpy")
            if m.exists():
                up_name, payload = m.name, m.read_bytes()
        url = "http://%s/api/file?name=%s" % (host, urllib.parse.quote(up_name))
        req = urllib.request.Request(url, data=payload, method="PUT")
        if a.token:
            req.add_header("X-Token", a.token)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                json.loads(r.read().decode())
            ok.append(up_name)
            if not a.json:
                print("OK   %-14s %6d B" % (up_name, len(payload)))
        except Exception as e:
            fail.append((n, str(e)))
            if not a.json:
                print("FAIL %-14s %s" % (n, e))
    if a.json:
        emit({"ok": len(fail) == 0, "uploaded": ok,
              "failed": [f[0] for f in fail]}, True)
    elif fail:
        sys.exit(1)


def main():
    p = argparse.ArgumentParser(description="DC1 内网控制/配网/升级 CLI")
    p.add_argument("--json", action="store_true", help="机器可读输出")
    p.add_argument("--ip", help="直接指定设备 IP")
    p.add_argument("--dev", help="按设备 id 选择")
    p.add_argument("--token", default=DEF_TOKEN, help="API token")

    # 让 --json/--ip/--dev/--token 写在子命令后面也能用
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    common.add_argument("--ip", default=argparse.SUPPRESS)
    common.add_argument("--dev", default=argparse.SUPPRESS)
    common.add_argument("--token", default=argparse.SUPPRESS)

    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("discover", parents=[common])
    d.add_argument("--timeout", type=float, default=2.5)
    d.set_defaults(func=cmd_discover)

    pr = sub.add_parser("prov", parents=[common])
    pr.add_argument("--ssid", required=True)
    pr.add_argument("--pass", dest="password", default="")
    pr.add_argument("--name")
    pr.add_argument("--target", help="单播目标 IP（默认广播）")
    pr.set_defaults(func=cmd_prov)

    sub.add_parser("ping", parents=[common]).set_defaults(func=cmd_ping)
    sub.add_parser("state", parents=[common]).set_defaults(func=cmd_state)

    sw = sub.add_parser("switch", parents=[common])
    sw.add_argument("--ch", default="total",
                    choices=["total", "1", "2", "3", "logo"])
    sw.add_argument("--on", type=int, default=1)
    sw.set_defaults(func=cmd_switch)

    sc = sub.add_parser("sched", parents=[common])
    sc.add_argument("op", choices=["list", "add", "del", "clear"])
    sc.add_argument("--ch", default="1")
    sc.add_argument("--on", type=int, default=1)
    sc.add_argument("--at", help="HH:MM")
    sc.add_argument("--once", action="store_true", help="单次（触发后自动删除）")
    sc.add_argument("--days", help="每周重复，如 1,3,5（1=周一）")
    sc.add_argument("--mask", help="每周重复（位掩码 0~127，GUI 用）")
    sc.add_argument("--id", dest="id_num", type=int, help="删除/启用时的规则 id")
    sc.set_defaults(func=cmd_sched)

    cd = sub.add_parser("countdown", parents=[common])
    cd.add_argument("op", choices=["list", "add", "del", "clear"])
    cd.add_argument("--ch", default="1")
    cd.add_argument("--on", type=int, default=0)
    cd.add_argument("--min", type=float, default=30)
    cd.add_argument("--id", dest="id_num", type=int)
    cd.set_defaults(func=cmd_countdown)

    sub.add_parser("meter", parents=[common]).set_defaults(func=cmd_meter)

    cal = sub.add_parser("cal", parents=[common])
    cal.add_argument("--voltage", type=float, help="电表测得的电压 V")
    cal.add_argument("--current", type=float, help="电表测得的电流 A")
    cal.add_argument("--power", type=float, help="电表测得的功率 W")
    cal.add_argument("--reset", action="store_true", help="系数复位为 1.0")
    cal.set_defaults(func=cmd_cal)
    sub.add_parser("time", parents=[common]).set_defaults(func=cmd_time)
    sub.add_parser("reboot", parents=[common]).set_defaults(func=cmd_reboot)

    fw = sub.add_parser("fw", parents=[common])
    fw.add_argument("action", choices=["update", "core"])
    fw.add_argument("--dir", help="源码目录（默认 firmware/micropython）")
    fw.add_argument("--only", nargs="*")
    fw.add_argument("--serial")
    fw.add_argument("--img")
    fw.add_argument("--mpy", action="store_true",
                    help="上传 mpy-cross 产物（main.py 仍传源码）")
    fw.add_argument("--mpy-dir", help="mpy 目录，默认 firmware/build/mpy")
    fw.set_defaults(func=cmd_fw)

    a = p.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
