"""WiFi/HTTP 最小诊断 —— 上传为设备 main.py 运行

不 swap UART、不初始化 I2C，所以 REPL 始终在 GPIO1/3，日志直接可见：
  mpremote connect COM8  / 或串口助手 115200

判定点：
  1. config.json 是否读到 ssid
  2. STA 连接状态（status 码：1=连接中 2=已连接 3=获取IP中 …）
  3. ifconfig 是否拿到 192.168.31.x
  4. bind 80 是否成功
"""

import json
import network
import socket
import time


def log(msg):
    print(msg)


cfg = {}
try:
    cfg = json.load(open("config.json"))
except Exception as e:
    log("config ERR: %s" % e)

ssid = cfg.get("ssid", "")
pw = cfg.get("password", "")
log("ssid=%r pwlen=%d name=%r" % (ssid, len(pw), cfg.get("name")))

a = network.WLAN(network.AP_IF)
a.active(True)
try:
    a.config(essid="DC1-DIAG")
    log("ap essid -> %r" % a.config("essid"))
except Exception as e:
    log("ap cfg ERR: %s" % e)

s = network.WLAN(network.STA_IF)
s.active(True)
try:
    s.connect(ssid, pw)
    log("connect() issued")
except Exception as e:
    log("connect ERR: %s" % e)

for i in range(30):
    time.sleep(1)
    try:
        st, conn = s.status(), s.isconnected()
    except Exception as e:
        st, conn = -1, False
    log("t%02d status=%s conn=%s %s" % (i, st, conn, s.ifconfig()[0]))
    if conn:
        break

ip = s.ifconfig()[0]
log("STA ip=%s" % ip)

srv = socket.socket()
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    srv.bind(("0.0.0.0", 80))
    srv.listen(2)
    srv.settimeout(1.0)
    log("HTTP bind OK on 0.0.0.0:80")
except Exception as e:
    log("HTTP bind FAIL: %s" % e)
    srv = None

boot_ms = time.ticks_ms()
while True:
    if srv:
        try:
            c, addr = srv.accept()
            req = c.recv(256)
            log("REQ from %s: %r" % (addr[0], req.split(b"\r\n")[0]))
            body = json.dumps({"t": "dev", "type": "DC1", "id": "diag",
                               "ip": ip, "up": time.ticks_diff(time.ticks_ms(), boot_ms)}).encode()
            c.send(b"HTTP/1.0 200 OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n" % len(body) + body)
            c.close()
        except OSError:
            pass
        except Exception as e:
            log("serve ERR: %s" % e)
    else:
        time.sleep_ms(500)
