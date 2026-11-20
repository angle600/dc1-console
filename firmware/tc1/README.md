# TC1 固件（MicroPython / ESP8266 1MB · 换脑版）

> 原厂 TC1（A1=EMW3031 / A2=HF-LPT230）跑不了 MicroPython。
> 这里的代码是"拆掉原模组 + 焊 ESP-12S"之后的固件，接线见 `docs/08-TC1方案.md`。

## 文件

| 文件 | 作用 | 备注 |
|---|---|---|
| `main.py` | 启动器：LED 自检、3 s 打断窗口、崩溃回滚 `app.bak`、safe.flag | 与 DC1 同结构 |
| `app.py` | UDP 54321 发现/配网 + HTTP JSON API + 主循环（按键/LED/计量/定时/倒计时） | 单线程 select，无 asyncio |
| `board.py` | 引脚定义、`Relays`（极性单点可切换）、`Key`（ADC 分压 + 长短按）、`Led` | 换板只改这一个文件 |
| `bl0937.py` | CF 脉冲计数 → 功率 / 标定 K / 脉冲累计 | 无 V/A |
| `store.py` | 定时、倒计时、电量累计 | **与 `firmware/micropython/store.py` 同源**，改一处要同步 |
| `index.html` | 手机网页（6 路开关 / 定时 / 倒计时 / 配网 / 功率） | 无外部 JS/CSS |
| `config.example.json` | 配置模板 | 复制成 `config.json` 再上传 |

## 与 DC1 的协议差异

| 端点 | 差异 |
|---|---|
| `/api/state` | `total` + `1..6`；`meter` 只有 `power/p_raw/k_imp/pulses/calibrated` |
| `/api/cal` | `POST {"power": <功率计读数>}` → 反算 `k_imp`（DC1 是 V/A/W 增益） |
| 其他 | `/api/ping/info/switch/schedule/countdown/energy/time/config/reboot/log/put/file` 与 DC1 一致 |

`type` 固定 `"TC1"`，`features` 为 `switch/schedule/countdown/meter-power/energy/ota-py/prov-udp`。

## 引脚（ESP-12S）

```
D1 GPIO5  → RELAY1     D2 GPIO4  → RELAY2     D5 GPIO14 → RELAY3
D6 GPIO12 → RELAY4     D7 GPIO13 → RELAY5     D0 GPIO16 → RELAY6
RX GPIO3  ← BL0937 CF（光耦输出，上拉+下降沿中断）
ADC0      ← 按键分压（按下 < 1.0 V）
GPIO2     → LED（低电平点亮，外部上拉保证 strap 高）
TX GPIO1  → 烧录/救砖，不接负载
```

继电器极性：`board.RELAY_ACTIVE_LOW`（默认 `False` = 高电平吸合）。

## 部署

```bash
# 首次（串口）
python tools/mp_deploy.py --serial COM5 --dir firmware/tc1
# 之后（WiFi）
python cli/dc1ctl.py --ip 192.168.4.1 fw update --dir firmware/tc1
python cli/dc1ctl.py --ip 192.168.4.1 reboot
```

## 自检

```bash
python -m py_compile firmware/tc1/*.py
python tests/test_tc1_logic.py      # 9 项：继电器/按键/CF 换算/标定/定时/电量

# 交叉编译（mpy-cross 必须与内核同为 1.29 / mpy v6.3）
mpy-cross -o firmware/build/tc1mpy/app.mpy firmware/tc1/app.py   # 其余同理
python cli/dc1ctl.py --ip <ip> fw update --dir firmware/tc1 --mpy \
       --mpy-dir firmware/build/tc1mpy
```

实测待上机项：继电器真实极性、CF 计数在 220 V 下的稳定性、按键 ADC 阈值、长跑。
