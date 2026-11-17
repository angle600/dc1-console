"""DC1 的持久化与业务状态：定时 / 倒计时 / 电量累计

设计原则（1MB Flash + ~30KB 堆）：
  * 只在"变更"时写盘，电量 60s 落一次，倒计时 30s 落一次
  * 规则数量硬上限（定时 16 条、倒计时 8 条），避免堆被吃光
"""

import json
import time

SCHED_MAX = 16
CD_MAX = 8

DOW_MASK_ALL = 127           # bit0=周一 ... bit6=周日


def load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def save(path, obj):
    try:
        with open(path, "w") as f:
            json.dump(obj, f)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- 定时
class Sched:
    """规则：{"id":1,"ch":"1","on":1,"hh":7,"mm":30,"days":127,"en":1}

    days 位掩码：bit0=周一 … bit6=周日；**days == 0 表示"单次"**
    —— 下次到点触发一次后自动删除（一次性定时）。
    """

    def __init__(self, path="sched.json"):
        self.path = path
        d = load(path, {"rules": [], "next": 1})
        self.rules = d.get("rules", [])[:SCHED_MAX]
        self.next = d.get("next", 1)
        self._last_key = ""

    def flush(self):
        save(self.path, {"rules": self.rules, "next": self.next})

    def add(self, rule):
        if len(self.rules) >= SCHED_MAX:
            raise ValueError("schedule full")
        r = {
            "id": self.next,
            "ch": str(rule.get("ch", "1")),
            "on": 1 if rule.get("on") else 0,
            "hh": int(rule.get("hh", 0)) % 24,
            "mm": int(rule.get("mm", 0)) % 60,
            "days": int(rule.get("days", DOW_MASK_ALL)) & 0x7F,
            "en": 1 if rule.get("en", 1) else 0,
        }
        self.next += 1
        self.rules.append(r)
        self.flush()
        return r

    def rm(self, rid):
        n = len(self.rules)
        self.rules = [r for r in self.rules if r["id"] != rid]
        self.flush()
        return len(self.rules) != n

    def enable(self, rid, en):
        for r in self.rules:
            if r["id"] == rid:
                r["en"] = 1 if en else 0
                self.flush()
                return True
        return False

    def all(self):
        return self.rules

    def due(self):
        """每分钟最多判定一次；返回 [(ch, on, id), ...]

        days != 0：按星期重复，命中即触发，规则保留。
        days == 0：单次，命中触发后**自动删除**该规则。
        """
        lt = time.localtime()
        key = "%04d%02d%02d%02d%02d" % (lt[0], lt[1], lt[2], lt[3], lt[4])
        if key == self._last_key:
            return []
        dow = lt[6] % 7                                  # localtime()[6]: 0=周一
        out = []
        once_ids = []
        for r in self.rules:
            if not r["en"]:
                continue
            if r["hh"] != lt[3] or r["mm"] != lt[4]:
                continue
            if r["days"] == 0:                          # 单次
                out.append((r["ch"], bool(r["on"]), r["id"]))
                once_ids.append(r["id"])
            elif (r["days"] >> dow) & 1:                 # 每周重复
                out.append((r["ch"], bool(r["on"]), r["id"]))
        if out:
            self._last_key = key
        if once_ids:
            self.rules = [r for r in self.rules if r["id"] not in once_ids]
            self.flush()
        return out


# ---------------------------------------------------------------- 倒计时
class Countdown:
    """条目：{"id":1,"ch":"1","on":0,"left":600}  left 单位秒"""

    def __init__(self, path="count.json"):
        self.path = path
        self.items = load(path, [])[:CD_MAX]
        self.next = max([i.get("id", 0) for i in self.items] or [0]) + 1
        self._last_save = time.ticks_ms()

    def flush(self):
        save(self.path, self.items)

    def add(self, ch, on, sec):
        if len(self.items) >= CD_MAX:
            raise ValueError("countdown full")
        it = {"id": self.next, "ch": str(ch), "on": 1 if on else 0,
              "left": int(sec)}
        self.next += 1
        self.items.append(it)
        self.flush()
        return it

    def rm(self, cid):
        n = len(self.items)
        self.items = [i for i in self.items if i["id"] != cid]
        self.flush()
        return len(self.items) != n

    def clear(self):
        self.items = []
        self.flush()

    def all(self):
        return self.items

    def tick(self, dt_ms):
        """返回到期动作 [(ch,on,id), ...]"""
        if not self.items:
            return []
        sec = dt_ms / 1000.0
        out = []
        keep = []
        for i in self.items:
            i["left"] = i["left"] - sec
            if i["left"] <= 0:
                out.append((i["ch"], bool(i["on"]), i["id"]))
            else:
                keep.append(i)
        if out:
            self.items = keep
            self.flush()
        else:
            self.items = keep
            now = time.ticks_ms()
            if time.ticks_diff(now, self._last_save) > 30000:
                self._last_save = now
                self.flush()
        return out


# ---------------------------------------------------------------- 电量
class Energy:
    """累计 kWh；today 按"日"滚动（用 localtime 的 DOY 判断）"""

    def __init__(self, path="energy.json"):
        self.path = path
        d = load(path, {})
        self.total = float(d.get("total", 0.0))
        self.today = float(d.get("today", 0.0))
        self.doy = int(d.get("doy", -1))
        self._last_save = time.ticks_ms()

    def add(self, power_w, dt_ms):
        if power_w <= 0:
            return
        lt = time.localtime()
        doy = lt[7]
        if doy != self.doy:
            self.doy = doy
            self.today = 0.0
        kwh = power_w * (dt_ms / 1000.0) / 3600.0 / 1000.0
        self.total += kwh
        self.today += kwh
        now = time.ticks_ms()
        if time.ticks_diff(now, self._last_save) > 60000:
            self._last_save = now
            self.flush()

    def flush(self):
        save(self.path, {"total": round(self.total, 4),
                         "today": round(self.today, 4), "doy": self.doy})

    def state(self):
        return {"total_kwh": round(self.total, 4),
                "today_kwh": round(self.today, 4)}

    def reset(self):
        self.total = 0.0
        self.today = 0.0
        self.flush()
