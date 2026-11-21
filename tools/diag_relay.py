# 继电器漂移诊断：每步操作后对比 u7_out(软件镜像) / reg_out(U7 实际) / g_state
import json
import sys
import time
import urllib.request

HOST = "http://192.168.31.243"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def get(path, timeout=10):
    with OPENER.open(HOST + path, timeout=timeout) as r:
        return json.loads(r.read().decode())


def show(tag):
    try:
        raw = get("/api/raw")
    except Exception as e:
        print("%-22s RAW ERR %r" % (tag, e))
        return None
    g = raw["g"]
    print("%-22s u7_out=%3d reg_out=%3d cfg=%2d keys=%d g=[%d,%d,%d,%d,%d]%s"
          % (tag, raw["u7_out"], raw["reg_out"], raw["reg_cfg"], raw["keys"],
             g[0], g[1], g[2], g[3], g[4],
             "  <== 软硬件不一致" if raw["u7_out"] != raw["reg_out"] else ""))
    return raw


print("=== 初始 ===")
show("init")

steps = [("1", 0), ("2", 0), ("3", 0), ("1", 1), ("2", 1), ("3", 1)]
for ch, on in steps:
    try:
        s = get("/api/switch?ch=%s&on=%d" % (ch, on))
        api = [s["total"], s["1"], s["2"], s["3"], s["logo"]]
    except Exception as e:
        print("switch ERR %r" % (e,))
        break
    time.sleep(1.0)
    raw = show("ch%s -> %d" % (ch, on))
    if raw:
        g = raw["g"]
        if api != g:
            print("      API 返回 %s 与 /api/raw %s 不一致" % (api, g))
