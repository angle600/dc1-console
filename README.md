# DC1 Console · 斐讯 DC1 排插的内网自持方案

[![license](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![device](https://img.shields.io/badge/device-ESP8266%20%2F%20DC1-lightgrey)]()
[![firmware](https://img.shields.io/badge/firmware-MicroPython%201.29-2b2728)]()
[![gui](https://img.shields.io/badge/GUI-Qt%206.8.5%20QML-41cd52)]()
[![ci](https://github.com/angle600/dc1-console/actions/workflows/check.yml/badge.svg)](https://github.com/angle600/dc1-console/actions/workflows/check.yml)

> 仓库：<https://github.com/angle600/dc1-console>

把**斐讯 DC1 智能排插**改成一台完全本地运行的设备：MicroPython 固件跑在排插上，
手机/PC 在局域网内发现它、给它配网、控制它；上位机是 Qt 6.8（QML）桌面程序，
业务逻辑全部在 Python CLI 里，GUI 只负责展示。

> 官方云已停运，本方案不依赖任何服务器、不注册账号、不出公网。

```
┌────────────────────┐   QProcess(stdout)   ┌────────────────────┐
│  dc1console (Qt6)  │ ───────────────────► │  cli/dc1ctl.py     │
│  QML: 列表/面板     │ ◄─────────────────── │  Python 3, 标准库   │
└────────────────────┘        JSON          └─────────┬──────────┘
                                                     │ UDP 54321（发现/配网）
                                                     │ HTTP 80（控制/OTA）
                                           ┌─────────▼──────────┐
                                           │  DC1 (ESP8266 1MB)  │
                                           │  MicroPython 1.29   │
                                           │  CAT9554 + CSE7766  │
                                           └────────────────────┘
```

## 功能

| 模块 | 能力 |
|---|---|
| 固件 | UDP 54321 广播发现；**上电 30 秒内**接受 UDP 配网；开关 4 路 + LOGO 灯 |
| 定时 | **单次**（触发一次后自动删除）/ **每周重复**（周一~周日任意勾选） |
| 倒计时 | 指定插孔在 N 分钟后开或关，支持多条并发 |
| 电量 | 实时 V/A/W + 今日/累计 kWh（CSE7766，系数可标定） |
| 上位机 | 发现设备、按 `type` 选界面、配网、开关、定时、倒计时、电量、**WiFi 更新固件** |
| 扩展 | 设备类型由 `type` + `features` 驱动，新增类型只需加一个 Panel |

## 目录

```
.
├── README.md
├── cli/dc1ctl.py                 # 唯一业务入口（发现/配网/控制/定时/OTA）
├── firmware/micropython/         # 设备端固件
│   ├── main.py                   # 配网 + HTTP API + 主循环
│   ├── store.py                  # 定时/倒计时/电量 持久化
│   ├── cat9554.py  cse7766.py    # U7 IO 扩展 / 电量计
│   └── webui.py                  # 手机网页（备用控制面）
├── gui/                          # Qt 6.8.5 MinGW64 上位机
│   ├── CMakeLists.txt
│   ├── main.cpp  clirunner.{h,cpp}
│   └── qml/{Main,Dc1Panel,GenericPanel,ProvisionDialog,FirmwareDialog}.qml
├── hardware/                     # 电路图资料 + 自绘原理框图 + datasheet
├── docs/                         # 刷机 / 配网 / AI 控制 / MicroPython 方案
└── tools/                        # mp_deploy.py（串口烧内核）等辅助脚本
```

## 快速开始

### 1. 刷固件（仅首次需要串口）

```bash
# 1) 备份原厂固件（拆机接 TTL，IO0 接 GND 进下载模式）
esptool.py --port COM5 read_flash 0 0x100000 backup_dc1.bin

# 2) 烧 MicroPython 1M 内核（1MB Flash 专用变体，607KB）
python tools/mp_deploy.py --serial COM5 --erase \
    --flash-img ESP8266_GENERIC-FLASH_1M-20260824-v1.29.0.bin

# 3) 烧业务代码（串口或 WiFi 均可）
python cli/dc1ctl.py --ip 192.168.4.1 fw update --dir firmware/micropython
```

### 2. 配网（两种场景）

```bash
# 设备已入网：直接配（会保存并重启）
python cli/dc1ctl.py prov --ssid MyHome2.4G --pass 12345678 --name 客厅排插

# 设备全新：会开热点 DC1-Setup/dc1setup，PC 连上热点后
python cli/dc1ctl.py --ip 192.168.4.1 prov --ssid MyHome2.4G --pass 12345678
```

> 配网窗口只有**上电后 30 秒**（`PROV_WINDOW_MS`）。超时后回 `window_closed`，断电重来即可。

### 3. CLI 日常操作

```bash
python cli/dc1ctl.py discover                        # 广播发现
python cli/dc1ctl.py state                           # 开关 + 计量 + 电量
python cli/dc1ctl.py switch --ch 1 --on 1            # 开插孔1
python cli/dc1ctl.py sched add --ch 1 --on 0 --at 23:00 --once          # 单次
python cli/dc1ctl.py sched add --ch total --on 1 --at 07:30 --days 1,2,3,4,5  # 每周一~五
python cli/dc1ctl.py countdown add --ch 1 --on 0 --min 30               # 30 分钟后关
python cli/dc1ctl.py time                            # 校时（定时功能依赖）
python cli/dc1ctl.py fw update --ip 192.168.1.9      # WiFi 覆盖源码
```

所有命令加 `--json` 输出机器可读结果——GUI 就是靠这个契约工作的。

### 4. 构建上位机（Qt 6.8.5 MinGW64）

```bat
:: Qt: D:\Qt\Qt6.8.5MinGW64   CMake 3.21+   Generator: MinGW Makefiles
cd gui
cmake -S . -B build -G "MinGW Makefiles" ^
      -DCMAKE_PREFIX_PATH=D:/Qt/Qt6.8.5MinGW64 ^
      -DCMAKE_C_COMPILER=D:/Qt/Tools/mingw1310_64/bin/gcc.exe ^
      -DCMAKE_CXX_COMPILER=D:/Qt/Tools/mingw1310_64/bin/g++.exe
cmake --build build -j
windeployqt --qmldir qml build/dc1console.exe
```

运行前确保 `python` 在 PATH（或设置 `DC1_PYTHON`）；CLI 路径优先取 `DC1_CLI`，
否则依次找 `./cli/dc1ctl.py`、`../cli/dc1ctl.py` 与源码树。

## 协议（内网，明文；读状态无需鉴权）

### UDP 54321

| 方向 | 报文 | 说明 |
|---|---|---|
| 上位机 → 广播 | `{"t":"discover","proto":1}` | 发现 |
| 设备 → 单播 | `{"t":"dev","type":"DC1","id":"dc1-a1b2","name":"客厅排插","ip":"192.168.1.9","fw":"1.1.0","prov":1,"features":[...]}` | 设备信息 |
| 上位机 → 广播 | `{"t":"prov","ssid":"...","pass":"...","name":"可选"}` | 配网（仅 30s 窗口） |
| 设备 → 单播 | `{"t":"prov","ok":1}` / `{"t":"prov","ok":0,"err":"window_closed"}` | 结果 |

### HTTP（端口 80）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/info` | 身份（type/id/fw/mac/features） |
| GET | `/api/state` | 开关 + 计量 + 电量 + 倒计时 |
| GET | `/api/switch?ch=1&on=1` | 控制（`total/1/2/3/logo`） |
| GET/POST | `/api/schedule` | 定时列表 / `{"op":"add\|del\|en\|clear"}` |
| GET/POST | `/api/countdown` | 倒计时 / `{"op":"add\|del\|clear"}` |
| GET | `/api/energy` | `{"total_kwh":..,"today_kwh":..}` |
| POST | `/api/time` | 校时 `{"epoch":...}` |
| POST | `/api/config` | 改配置（需 `X-Token`，可 `wifi_reset`） |
| PUT | `/api/file?name=main.py` | 覆盖源码（需 `X-Token`） |
| POST | `/api/reboot` | 重启 |

定时规则示例：

```json
{"id":3,"ch":"1","on":0,"hh":23,"mm":0,"days":0,"en":1}
```

`days` 是位掩码（bit0=周一 … bit6=周日）；**`days=0` 表示单次**，触发后自动删除；
`127` = 每天。

## 硬件要点

| 信号 | 引脚 | 说明 |
|---|---|---|
| U7 CAT9554 | I2C `GPIO3/GPIO12` @20kHz，0x20 | P0~P2 按键，P4~P7 继电器（P7=总闸） |
| CSE7766 | UART0 swap 到 `GPIO15/13`，4800 8E1 | 计量；swap 后串口 REPL 会搬走 |
| 总按键 | GPIO16（下拉，按下为高） | 上电按住 = 安全模式，保留 REPL |
| LOGO 灯 | GPIO14（低有效） | — |

电路资料与反推过程见 [`hardware/README.md`](hardware/README.md) 与
[`hardware/DC1-原理框图.svg`](hardware/DC1-原理框图.svg)。

## 扩展新的设备类型

1. 固件里把 `TYPE` 改成新类型（如 `"SC1"`），`features` 列出能力；
2. `cli/dc1ctl.py` 无需改动（命令按能力走，`--json` 输出统一）；
3. `gui/qml/` 新增 `Sc1Panel.qml`，在 `Main.qml` 的 `sourceComponent` 里加一条映射：

```qml
sourceComponent: root.curType === "DC1" ? dc1Comp
               : root.curType === "SC1" ? sc1Comp
               : genericComp
```

未知类型自动落到 `GenericPanel`（显示原始 JSON），不会白屏。

## 已知限制

- **1MB Flash 无法整片固件 OTA**：MicroPython 内核只能串口刷；`fw update` 更新的是 Python 源码。
  想要整片 OTA 请走 ESPHome 路线（见 `docs/01-刷机方案.md`）。
- 计量精度取决于现场标定（`config.json` 的 `meter.p_gain` 等），出厂系数为 1.0。
- 配网窗口 30 秒是硬编码（`PROV_WINDOW_MS`），安全模型是"物理在场"。
- 定时依赖设备时间：开机 SNTP 校时一次，失败时用 `dc1ctl.py time` 手动校准。

## 文档

- [`hardware/README.md`](hardware/README.md) — 电路与引脚映射
- [`docs/01-刷机方案.md`](docs/01-刷机方案.md) — 拆机、备份、烧写、验收
- [`docs/02-手机配网.md`](docs/02-手机配网.md) — 强制门户配网
- [`docs/03-AI内网控制.md`](docs/03-AI内网控制.md) — ESPHome 路线的 AI 控制
- [`docs/04-MicroPython方案.md`](docs/04-MicroPython方案.md) — 可行性与 OTA 边界

## 许可

代码 MIT。硬件照片、引脚标注与 datasheet 来自社区项目
[Samuel-0-0/phicomm_dc1-esphome](https://github.com/Samuel-0-0/phicomm_dc1-esphome)，
仅作个人研究与维修使用；强电操作风险自负。
