# 扫描 192.168.31.0/24 找 DC1
import concurrent.futures as cf
import json
import urllib.request

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def probe(i):
    ip = "192.168.31.%d" % i
    try:
        with OPENER.open("http://%s/api/ping" % ip, timeout=1.2) as r:
            js = json.loads(r.read().decode())
            if js.get("type") == "DC1":
                return ip, js
    except Exception:
        return None
    return None


found = []
with cf.ThreadPoolExecutor(max_workers=40) as ex:
    for res in ex.map(probe, range(2, 255)):
        if res:
            found.append(res)
            print("FOUND %s -> %s" % res)
if not found:
    print("未找到 DC1（设备可能未联网）")
