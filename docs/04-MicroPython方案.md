# 04 · MicroPython 方案（含"WiFi 烧写"能做到哪一层）

## 0. 结论先行

| 问题 | 答案 |
|---|---|
| DC1 能跑 MicroPython 吗？ | **能**。ESP8266EX + **1MB** Flash，官方有专门的 `FLASH_1M` 变体，实测镜像 **607,116 B**（≈593KB），剩余约 380KB 给 littlefs，放代码绰绰有余 |
| 能"WiFi 烧固件"吗？ | **分两层**：<br>① **应用代码（.py）：能，而且比 ESPHome 更快** —— 本仓库固件内置 `PUT /api/file`，`mp_deploy.py` 一条命令秒级推送；<br>② **整片 MicroPython 内核（.bin）：1MB stock 版本不能** —— 没有双分区/OTA stub，只能串口，或用第三方 DFU 分支/换 2~4MB 模组 |
| 值得换掉 ESPHome 吗？ | 见第 7 节判断表。**要稳定长期跑 → ESPHome**；**要"纯 Python 可改、能自己加逻辑、不想装编译链" → MicroPython** |

## 1. 已落地的文件

```
firmware/micropython/
├── main.py            # 配网 + HTTP API + 继电器逻辑 + 计量轮询（单线程 select 模型）
├── cat9554.py         # U7 IO 扩展驱动（P0~P7）
├── cse7766.py         # U11 计量帧解析（原始计数 + 可标定系数）
├── webui.py           # 手机控制页（~1.5KB）
└── config.example.json
tools/mp_deploy.py     # WiFi 推送代码 / 串口烧内核 二合一
```

> ⚠ 这套代码是**按硬件资料写的、未上机验证**（无实物），第一次烧写请按第 9 节清单逐项确认。

## 2. Flash / RAM 预算（这是方案成立与否的关键）

| 项 | 占用 | 说明 |
|---|---|---|
| MicroPython 1M 镜像 | 607,116 B | 官方 `ESP8266_GENERIC-FLASH_1M-20260824-v1.29.0.bin` |
| 剩余（littlefs） | ≈ 400 KB | 放 5 个源文件（合计 <25KB）+ state.json |
| RAM 可用堆 | ≈ 30~36 KB | 1M 变体偏紧，**所以本仓库不用 asyncio、不开 WebREPL 常驻** |
| 模块裁剪 | 1M 变体移除了 asyncio、FAT/FATFS、部分 micropython-lib | 用 littlefs + select + socket 手写事件循环 |

## 3. 三层"烧写"能力矩阵

| 层级 | 内容 | MicroPython 方案 | 通道 | 耗时 |
|---|---|---|---|---|
| L1 应用代码 | `main.py` 等 .py | ✅ **本仓库自实现** | `PUT /api/file`（HTTP，WiFi） | <1s |
| L2 预编译 .mpy / 冻结模块 | `esp.add_frozen()` | ⚠ 需要带 DFM 的定制固件 | 串口 | — |
| L3 整片内核 .bin | MicroPython 本体 | ❌ 1MB stock 不行（见第 6 节） | 串口 esptool | ~40s |

**L1 才是日常 99% 的"更新固件"**——改完 Python 代码推上去重启即可，不用拆机、不用焊线、不用装 ESPHome/pio。

## 4. 首次烧写（唯一需要串口的一次）

```bash
# 1) 进下载模式：IO0 短接 GND，再上电
# 2) 擦 + 烧（1M 变体，务必带 --flash_size）
esptool.py --port COM5 erase_flash
esptool.py --port COM5 --baud 460800 write_flash --flash_size=1MB 0x0 \
    ESP8266_GENERIC-FLASH_1M-20260824-v1.29.0.bin

# 3) 断电重启 → 串口 115200 出现 >>> 即成功
```

固件下载页：<https://micropython.org/download/ESP8266_GENERIC/>（选 **FLASH_1M** 变体；
稳定老版本 `esp8266-1m-*` 命名已 404，别再用旧链接）。

## 5. 配网（本仓库自己实现，不依赖 ESPHome）

```
上电 → 读 config.json → 12s 连不上 → 开 AP: DC1-Setup / dc1setup
     → UDP 53 全量劫持到 192.168.4.1（手机自动弹门户）
     → 浏览器填 SSID/密码 → POST /api/config → 保存并重启 → 入网
```

- 手机端操作与 ESPHome 版完全一样；区别是页面由 `webui.py` 生成（自带控制按钮）。
- 配网后可用 `curl http://<ip>/api/ping` 验证（返回 `{"ok":1}`）。

## 6. 整片固件 OTA：为什么 1MB 做不了，以及三条替代路

ESP8266 没有分区表，代码**就地 XIP 执行**，擦掉正在跑的扇区 = 当场变砖。要做整片 OTA 必须有"第二份镜像 + 引导搬运"：

| 方案 | 原理 | 1MB 可行性 |
|---|---|---|
| stock MicroPython | 无 OTA 机制 | ❌ |
| **yaota8266**（老 OTA build） | 预留两份固件区，按键触发 | ❌ 两份 600KB 装不下；且需按键 |
| **第三方 DFU 分支**（`inisetup.fwupdate`） | 先把 .bin 存到文件系统，再用 IRAM 中的 `esp.DFU()` 逐扇区刷 | ⚠ 可行但**要自己编译该分支**；1MB 下 FS 只有 400KB，放不进 600KB 镜像 → 实际需 ≥2MB |
| 换模组 | ESP-12F/ESP-07S（4MB）飞线或换板 | ✅ 但破坏性改造 |
| 保留串口 | 拆一次焊 4 根线，之后只走 L1 代码更新 | ✅ 推荐 |

**结论**：这套方案里"WiFi 烧写"= **WiFi 更新 Python 代码**；内核升级老老实实留串口。
如果主人的硬需求是"连内核都能 WiFi 刷"，那 ESPHome（自带 OTA stub）才是对的底座 —— 这也是 `docs/01` 仍推荐 ESPHome 的原因。

## 7. 选型判断表

| 维度 | MicroPython（本方案） | ESPHome（01 方案） |
|---|---|---|
| 改逻辑 | 直接改 .py，WiFi 秒推 | 改 YAML → 编译（~1min）→ OTA |
| 内核 WiFi 升级 | ❌ | ✅ |
| 内存余量 | 紧（30KB 堆，注意 GC） | 宽松（C++ 静态分配） |
| 稳定性/长跑 | 中（单线程、异常需自己兜） | 高（成熟组件、自动重连） |
| 计量精度 | 自己标定，可控 | 官方 cse7766 组件，含滤波 |
| 与 HA / MQTT | 自己写 | 原生 |
| 学习/改造价值 | 高（全是 Python，逻辑透明） | 中（框架黑盒） |
| 适合 | 想自己加逻辑、做实验、不想装编译链 | 装好就不想再动、要长期稳定 |

## 8. 计量标定（CSE7766 输出的是周期计数，不是伏特）

`cse7766.py` 按手册 §3.5.2 解码：`U = Uk/(Ut*V2R)`、`I = Ik/(It*V1R)`、`P = Pk/(Pt*V1R*V2R)`，
再乘 `v/i/p_gain`（单点标定系数，默认 1.0）。标定用 `/api/cal`（GUI 的「标定…」或
`dc1ctl.py cal --power 40`），固件直接用 raw 周期反算，不需要重启。

基准负载怎么选（白炽灯 ✅ / 电热器 ✅ / 电烙铁 ⚠ / LED ❌）见 README「电量计量与标定」。

## 9. 上机验收清单（首次必做）

| # | 检查 | 方法 | 通过标准 |
|---|---|---|---|
| 1 | 启动不崩 | 串口 115200 | 打印 `STA ok` 或 `AP mode`，无 traceback |
| 2 | 继电器可控 | `curl "…/api/switch?ch=1&on=1"` | 对应插孔"咔"一声 |
| 3 | 总闸互锁 | 关总闸后开插孔2 | 总闸自动先合 |
| 4 | 断电记忆 | 关总闸再开 | 原分路恢复 |
| 5 | 按键 | 手按 KEY1~3 / 总键 | 状态同步，无抖动 |
| 6 | 计量 | 万用表 + 60W 灯 | 电压 ±5V 内，功率可标定到 ±10% |
| 7 | WiFi 更新 | `python tools/mp_deploy.py --only main.py` | 返回 `{"ok":1}`，重启后生效 |
| 8 | 救援模式 | 按住总键上电 | 串口 REPL 可用（不被 UART swap 抢走） |

## 10. 已知坑（都是这套硬件特有的）

| 坑 | 说明 | 对策 |
|---|---|---|
| **UART swap 抢走 REPL** | CSE7766 必须用 `UART(0, tx=15, rx=13)`，swap 后 REPL 也搬到 GPIO15/13，USB 口没输出 | 上电**按住总键**进安全模式（本固件已实现）；或 `machine.UART(0, tx=1, rx=3)` 换回 |
| I2C 频率 | U7 简配，50k 以上随机失败 | `I2C(freq=20000)`，别改 |
| GPIO0 | 板载 WiFi 灯 + 下载脚；`main.py` 里别驱动它，否则下次可能进不了正常启动 | 不碰 |
| RAM 只有 ~30KB | 大 JSON / 长字符串拼接会 MemoryError | 本固件用 `%` 拼接、单连接处理、60s 一次 `gc.collect()` |
| 1M 变体无 asyncio | `uasyncio` 不可用 | 用 `socket` + 阻塞 accept（timeout 0.25s）轮询 |
| AP 门户不弹 | 部分安卓不认 302 | 直接访问 `http://192.168.4.1`；DNS 劫持已内置 |
| 掉电丢文件 | littlefs 写一半断电可能损坏 | 只在状态变化时写 `state.json`；关键配置改完等 1s 再断电 |

## 11. 后续可做（TODO）

- [x] UDP 广播发现 + 30s 配网窗口（v1.1 已实现）
- [ ] 上机实测 + 按第 8 节标定，把默认 gain 写回 `config.example.json`
- [ ] 把 `dc1ctl.py` 增加 `--http` 后端，与 ESPHome 固件共用一套 CLI
- [ ] 若要整片 OTA：评估换 4MB 模组 + yaota8266/DFU 分支的可行性（需重新设计 `docs/01` 的烧写流程）

## 9. 上板实测踩坑记录（2026-11-18，DC1 实机）

1 MB Flash / ESP8266 的 MicroPython 堆只有 **~36 KB**，实测空闲峰值 ~7.3 KB。
以下问题在 CPython 上测不出来，只能上板暴露：

| 现象 | 原因 | 处理 |
|---|---|---|
| `MemoryError` | 24 KB 源码 `main.py` 无法现场编译 | 逻辑编译成 `app.mpy`，`main.py` 只留启动器 |
| `MemoryError: allocating 1016` | 5.7 KB 的 HTML 常驻堆 | HTML 落到 `index.html`，按 256 B 流式发送 |
| `AttributeError: 'bytes' object has no attribute 'partition'` | MicroPython 无 `partition` | 用 `find` + 切片 |
| `TypeError: function doesn't take keyword arguments` | `ln.decode(errors="ignore")` | 改 `try: ln.decode()` |
| `Pin.PULL_DOWN` 不存在 | ESP8266 无内部下拉 | `try/except` 降级为 `Pin.IN` |
| HTTP 连接立刻 0 字节关闭 | 对 accept 出来的 socket 调 `settimeout()` 会让 `recv` 立即超时 | 用 `select.poll()` 判可读 + 阻塞 `recv` |
| `OSError: [Errno 19] ENODEV`（I2C） | GPIO3 同时是 UART0-RX 与 U7 的 SDA | **必须先 `UART(0, tx=15, rx=13)` 再初始化 I2C** |
| 请求处理 `MemoryError: allocating 1634` | 堆只剩 1.6 KB | `handle()` 开头 `gc.collect()`；计量与网页不要同时常驻 |

### 无线迭代（不用拆机）

```bash
# TCP 推送（推荐）：POST /api/put 分片写文件，512 B/片
python tools/tcp_push.py --ip 192.168.31.243 --file firmware/build/mpy/app.mpy --name app.mpy
python tools/tcp_push.py --ip 192.168.31.243 --reset
```

> 1024 B/片会撞内存上限返回 400；`/api/get` 读 >5 KB 文件会 OOM（仅适合小文件校验）。

### 运行时接口（实测）

| 接口 | 说明 |
|---|---|
| `GET /api/state` | 开关/计量/能量/信号 |
| `POST /api/switch?ch=1|2|3|total&on=0|1&t=<token>` | 总闸 OFF 会级联关分路 |
| `GET/POST /api/config?t=<token>` | 注意参数名是 `t`，键白名单含 `hw`/`meter_enabled` |
| `GET /api/log` | boot.log |
| `POST /api/reboot?t=<token>` | 软重启（加载新 app.mpy） |
| `GET /api/get?name=<file>` | 读小文件（base64） |

## 10. 配网机制（实测）

三条路径，按场景区分：

| 场景 | 路径 | 端口/地址 |
|---|---|---|
| 首次 / 无凭据 | 设备开热点 `DC1-Setup` / `dc1setup`，手机打开 `http://192.168.4.1` 用网页表单填 WiFi | AP 192.168.4.1 |
| 上电 30 秒内 | UDP `{"t":"prov","ssid","pass"}` → 写盘并重启 | UDP 54321 |
| 已联网 | `POST /api/config?t=<token>` `{"ssid","password","reboot":1}`；`{"wifi_reset":true}` 清凭据回 AP | HTTP 80 |

要点：

- **首次 `api_token` 为空 → 配网免鉴权**；连上路由器后建议在网页「存令牌」里填上 token
- **STA 连上后必须 `AP_IF.active(False)`**：ESP8266 的 AP 默认开启且无密码，否则设备旁边永远有个开放入口
- 有线→无线的**广播会被路由器丢弃**（实测），`cli prov` 必须让电脑先连设备热点再发（走 192.168.4.1 单播）
- 页面 `index.html` 的 JS 是从 `webui.py` 抽取的，抽取时 `\\'` 会多留一个反斜杠导致整段 JS 语法错误，抽取后必须 `node --check` 验证

## 11. 装配前加固（2026-11-18）

| 改动 | 说明 |
|---|---|
| 按键极性自适应 | `keys_init()` 采空闲基线，之后"与基线不同"= 按下；不再假设高/低有效（U7 三键 + GPIO16 总键都适用） |
| 按键按下沿触发 | 按住不连发；`DEBOUNCE=60ms` |
| 软件看门狗 | 主循环单次迭代 > 8 s 判定卡死 → 写 err.log → `machine.reset()` |
| `/api/get` 分片 | `?off=&len=`，避免整文件进堆；`b2a_base64` 每行带 `\n`，校验前需 `split()` 再解码 |
| OTA 前释放计量 | `free_meter()`：写/读文件前释放 CSE7766 对象，否则 `MemoryError`（堆仅 ~5 KB） |
| 危险接口鉴权 | `/api/put`、`/api/file` 需 `?t=<token>`；`/api/switch` 仍开放（局域网内可用，网页免配 token） |
| 令牌化推送 | `tools/tcp_push.py --token <api_token>`；256 B/片在计量常开时也能过 |

实测：倒计时 8 s 准点执行；单次定时到点触发并自动删除；OTA 15,911 B 回读 sha256 一致。

### 10.1 AP 配网闭环实测（2026-11-18，通过）

```
POST /api/config {"wifi_reset":1}      → {"ok":1,"reboot":1}
   ↓ 设备重启，config.ssid 为空
热点 DC1-Setup / dc1setup（WPA2-PSK）   → PC 连上后拿到 192.168.4.x
GET  http://192.168.4.1/               → 7709 B，含「WiFi 设置」表单
POST http://192.168.4.1/api/config     → {"ok":1}（带 reboot:1）
   ↓ 设备重启
重新连上 zheng31                        → 192.168.31.243，rssi -56
AP 自动关闭                             → 扫描不到 DC1-Setup / MicroPython-xxxx
```

注意：`prov`（UDP 30 秒窗口）此时已为 0，但 HTTP `/api/config` 不受窗口限制，仍可配网。
