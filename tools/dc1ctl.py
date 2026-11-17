#!/usr/bin/env python3
"""dc1ctl —— 局域网控制斐讯 DC1 排插（ESPHome Native API 通道）

依赖：pip install aioesphomeapi
用法：
    python dc1ctl.py list                 # 列出实体
    python dc1ctl.py state                # 打印全部开关/计量状态
    python dc1ctl.py on 1                 # 开插孔1（total/1/2/3/logo）
    python dc1ctl.py off total            # 关总开关
    python dc1ctl.py toggle 2
    python dc1ctl.py meter --watch        # 实时功率（Ctrl-C 退出）
环境变量：
    DC1_HOST  默认 dc1.local（也可写 IP）
    DC1_KEY   API encryption key（firmware/secrets.yaml 里的 api_encryption_key）
"""

import argparse
import asyncio
import os
import sys
import time

try:
    from aioesphomeapi import APIClient
except ImportError:
    sys.exit("缺少依赖：pip install aioesphomeapi")

HOST = os.environ.get("DC1_HOST", "dc1.local")
PORT = int(os.environ.get("DC1_PORT", "6053"))
KEY = os.environ.get("DC1_KEY", "")

# 逻辑名 -> ESPHome 实体 object_id 关键字（按 friendly name 前缀匹配）
PLUGS = {
    "total": "总开关",
    "1": "插孔1",
    "2": "插孔2",
    "3": "插孔3",
    "logo": "logo",
}


def match(name: str, token: str) -> bool:
    return token.lower() in (name or "").lower()


async def connect():
    cli = APIClient(HOST, PORT, None, noise_psk=KEY or None)
    await cli.connect(login=True)
    try:
        ents, _ = await cli.list_entities_services()
    except Exception:
        ents = await cli.list_entities()
    return cli, ents


def pick_switch(ents, token):
    for e in ents:
        if hasattr(e, "key") and hasattr(e, "name") and match(e.name, token):
            # 只认开关类实体：名称含关键字的 switch
            if type(e).__name__.lower().startswith("switch"):
                return e
    return None


async def cmd_list(args):
    cli, ents = await connect()
    for e in ents:
        kind = type(e).__name__
        if kind.startswith("Switch") or kind.startswith("Sensor"):
            unit = getattr(e, "unit_of_measurement", "") or ""
            print(f"{e.key:>6}  {kind:<16} {e.name} {unit}")
    await cli.disconnect()


async def read_states(cli, ents, timeout=3.0):
    states = {}
    done = asyncio.Event()

    def on_state(st):
        states[st.key] = st
        if len(states) >= 1 and done.is_set():
            pass

    cli.subscribe_states(on_state)
    await asyncio.sleep(0.6)  # 等首轮推送
    return states


async def cmd_state(args):
    cli, ents = await connect()
    states = await read_states(cli, ents)
    rows = []
    for e in ents:
        st = states.get(e.key)
        if st is None:
            continue
        val = getattr(st, "state", getattr(st, "value", None))
        if isinstance(val, bool):
            val = "ON" if val else "OFF"
        elif isinstance(val, float):
            val = f"{val:.3f}".rstrip("0").rstrip(".")
        unit = getattr(e, "unit_of_measurement", "") or ""
        rows.append((type(e).__name__, e.name, f"{val}{(' ' + unit) if unit else ''}"))
    w = max([len(r[1]) for r in rows] or [10])
    for kind, name, val in rows:
        print(f"{name:<{w}}  {val:<14} ({kind})")
    await cli.disconnect()


async def set_plug(token, on):
    cli, ents = await connect()
    ent = pick_switch(ents, token)
    if ent is None:
        await cli.disconnect()
        sys.exit(f"未找到开关实体：{token}")
    await cli.switch_command(ent.key, on)
    await asyncio.sleep(0.8)
    states = await read_states(cli, ents)
    st = states.get(ent.key)
    print(f"{ent.name} -> {'ON' if getattr(st, 'state', None) else 'OFF'}")
    await cli.disconnect()


async def cmd_toggle(token):
    cli, ents = await connect()
    ent = pick_switch(ents, token)
    states = await read_states(cli, ents)
    cur = getattr(states.get(ent.key), "state", False)
    await cli.switch_command(ent.key, not cur)
    print(f"{ent.name} -> {'ON' if not cur else 'OFF'}")
    await cli.disconnect()


async def cmd_meter(args):
    cli, ents = await connect()
    keep = [e for e in ents if getattr(e, "unit_of_measurement", "") in ("V", "A", "W", "kWh")]
    keys = {e.key: e for e in keep}
    latest = {}

    def on_state(st):
        if st.key in keys:
            latest[st.key] = getattr(st, "state", getattr(st, "value", None))

    cli.subscribe_states(on_state)
    if not args.watch:
        await asyncio.sleep(1.5)
        for k, e in keys.items():
            print(f"{e.name}: {latest.get(k)} {e.unit_of_measurement}")
        await cli.disconnect()
        return
    try:
        while True:
            await asyncio.sleep(1.0)
            parts = [f"{keys[k].unit_of_measurement}={latest[k]}" for k in keys if k in latest]
            print("  ".join(parts), flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        await cli.disconnect()


def main():
    p = argparse.ArgumentParser(description="DC1 局域网控制（ESPHome API）")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("state").set_defaults(func=cmd_state)
    for name in ("on", "off"):
        sp = sub.add_parser(name)
        sp.add_argument("plug", choices=list(PLUGS))
        sp.set_defaults(func=lambda a, n=name: set_plug(PLUGS[a.plug], n == "on"))
    sp = sub.add_parser("toggle")
    sp.add_argument("plug", choices=list(PLUGS))
    sp.set_defaults(func=lambda a: cmd_toggle(PLUGS[a.plug]))
    sp = sub.add_parser("meter")
    sp.add_argument("--watch", action="store_true")
    sp.set_defaults(func=cmd_meter)
    sub.choices["list"].set_defaults(func=cmd_list)
    sub.choices["state"].set_defaults(func=cmd_state)

    args = p.parse_args()
    t0 = time.time()
    try:
        asyncio.run(args.func(args))
    except Exception as e:
        sys.exit(f"连接 {HOST}:{PORT} 失败：{e!r}")
    if "-v" in sys.argv:
        print(f"({time.time() - t0:.2f}s)")


if __name__ == "__main__":
    main()
