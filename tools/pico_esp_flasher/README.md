# Pico ESP Flasher

用一块 **Raspberry Pi Pico** 做 ESP8266 / ESP32 模块的固件下载器：

- **USB 转串口**：Pico 的 USB 枚举出 2 个虚拟串口，其中一个直通 UART0
- **控制 GPIO0**：进串口下载模式
- **控制 3.3V 通断**：MOS 高边开关，可断电复位、可冷启动、可测上电电流
- **EN/复位可控**：DTR/RTS 自动映射，`esptool` 裸用即可

```
┌────────┐   USB    ┌───────────────┐  UART0   ┌──────────────┐
│  PC    │◄────────►│  Pico RP2040  │◄────────►│  ESP 模块    │
│ esptool│  2×CDC   │  CDC0 = 数据  │  GP0/GP1 │  RXD/TXD     │
└────────┘          │  CDC1 = 控制台│  GP2/3/4 │  IO0/EN/3V3  │
                    └───────────────┘          └──────────────┘
```

---

## 1. 接线

详见 `hardware/wiring.svg`（信号）与 `hardware/schematic.svg`（器件级）。

| Pico 引脚 | 物理 Pin | 接到 | 中间器件 | 说明 |
|---|---|---|---|---|
| GP0 | 1 | ESP **RXD** | 1 kΩ 串联 | Pico TX → ESP RX |
| GP1 | 2 | ESP **TXD** | 直连 | ESP TX → Pico RX |
| GP2 | 4 | ESP **GPIO0** | Q2 2N7002 开漏 | 高电平 = 拉低 = 下载模式 |
| GP3 | 5 | ESP **EN/CH_PD** | Q3 2N7002 开漏 | 高电平 = 拉低 = 复位 |
| GP4 | 6 | 3.3V 高边开关 | Q4 → Q1 AO3401 | 高电平 = 上电 |
| GND | 3 / 8 / 13 / 18 | ESP GND | — | 必须共地 |
| VBUS | 40 | U1 AMS1117 输入 | — | 5V 源 |

**关键点**

- `GP2/GP3/GP4` 的逻辑是"**高有效**"：GPIO=1 → N-MOS 导通 → 目标被拉低 / 上电。
- Pico 复位后 GPIO 默认输入下拉，配合外部 100 kΩ 下拉，保证上电瞬间 ESP 不会误进下载模式。
- ESP 的 **EN 上拉 10 kΩ 必须接 ESP 自己的 3V3**（Q1 之后的节点），不能接 Pico 的 3V3。

物料见 `hardware/BOM.md`。

---

## 2. 固件

### 2a. C 版（推荐，功能完整）

需要 `pico-sdk` + `arm-none-eabi-gcc` + `cmake`。

```powershell
cd tools/pico_esp_flasher/firmware
.\build.ps1                 # 首次会自动拉 pico-sdk（约 3~5 分钟）
```

产物 `firmware/build/esp-flasher.uf2`。烧录：按住 BOOTSEL 插 USB → 出现 `RPI-RP2` 盘 → 拖进去。

没装工具链时（可选，官方一键包）：

```powershell
winget install Kitware.CMake
winget install Arm.GnuArmEmbeddedToolchain
winget install Ninja-build.Ninja
```

### 2b. MicroPython 版（零工具链，立即可用）

把 `firmware-mpy/main.py` 存成 Pico 的 `main.py`（直接拖进 `MICROPYTHON` 盘）。

功能少两点：**无 DTR/RTS 自动复位**、**建议波特率 ≤ 460800**。用法见文件头注释。

```powershell
# 刷固件（Pico 按住 BOOTSEL 插 USB，出现 RPI-RP2 盘后）
copy RPI_PICO-20260824-v1.29.0.uf2 E:\

# 传程序（v1.29 的 Pico 固件不在 Windows 挂载 MICROPYTHON 盘，必须用 mpremote）
python -m mpremote connect port:COM9 fs cp firmware-mpy/main.py :main.py
python -m mpremote connect port:COM9 fs ls

# 交互 REPL
python -m mpremote connect port:COM9 repl
```

> `mpremote` 随 Python 安装：`pip install mpremote`。

---

## 3. 两个串口

C 版固件以复合设备枚举，VID `2E8A` / PID `1001`，生成 **两个 COM 口**：

| 接口 | 用途 | 注意 |
|---|---|---|
| **CDC0（数据口）** | 直通 ESP 的 UART0 | 固件在此口**不输出任何字符**，避免污染 esptool 同步 |
| **CDC1（控制口）** | ASCII 命令控制台 | 上电打印 banner 和 `HELP` |

`picoflash.py --list` 可看号；`--info` 会自动探测哪个是控制口。

控制台命令（115200 8N1，回车结束）：

**裸 GPIO（极性不写死）**

| 命令 | 作用 |
|---|---|
| `GP <n> <0\|1>` | 直接驱动任意 GPIO（2..28，GP23/24 是芯片内部，禁用） |
| `GPIN <n>` | 设为输入 |
| `GPREAD <n>` | 回读电平 |
| `PIN pwr\|io0\|en <n>` | 重新指定语义命令用哪个引脚 |
| `POL pwr\|io0\|en <0\|1>` | 重新定义有效电平：1=高有效，0=低有效 |
| `PINS` | 打印状态 + 当前映射 |

**语义命令（引脚/极性按上表映射）**

| 命令 | 作用 |
|---|---|
| `PWR 0\|1\|CYCLE` | 3.3V 断电 / 上电 / 断电 300ms 再上电 |
| `IO0 0\|1` | 释放 / 拉低 ESP GPIO0 |
| `EN 0\|1` | 释放 / 拉低 ESP EN |
| `BOOT` | 进下载模式：上电 → IO0 有效 → EN 脉冲 |
| `RUN` | 复位运行：IO0 释放 → EN 脉冲 |
| `AUTO 0\|1` | DTR/RTS 自动映射开关（默认 1） |
| `BAUD n` | 强制 UART 波特率 |
| `STATUS` | `pwr/io0/en/auto/baud/rx/tx` + 映射行 |
| `BOOTSEL` | 跳回 UF2 bootloader（升级固件不用再按按键） |

**注意 `POL` 的语义**：`POL PWR 0` 表示"低电平上电"，即 P-MOS 栅极直驱的接法。
改完会立即把三个控制脚恢复到非激活电平。

### 默认映射按 DC1 / TC1 模组实测设定

| 项 | 默认值 | 说明 |
|---|---|---|
| PWR | **GP4 / L** | GP4 经 NMOS 接 `3V3_EN`，**GP4=0 上电**（反相） |
| IO0 | **GP2 / L** | GP2 **直连**模组 IO0，**GP2=0 即拉低** |
| EN | GP3 / H | 模组**没有 CH_PD/EN 线**，此脚保留不参与时序 |
| AUTO | **0** | 模组无 EN，DTR/RTS 映射无意义，默认关闭 |
| BAUD | **115200** | 模组实测固定用这个 |

**烧录时序**（无 EN 线，只能靠上电复位采样 IO0）：

```
断电 0.5s → IO0 拉低 → 上电 0.5s   = BOOT
断电 0.5s → IO0 释放 → 上电 0.5s   = RUN
```

有 EN 线的模组用同一套也能进 bootloader（上电复位一样生效），所以不需要分两套。

---

## 4. 烧录

### 方式 A：上位机封装（推荐）

```powershell
# 查看端口
python host/picoflash.py --list

# 一键烧录：断电 → 上电 → 进下载 → esptool → 复位运行
python host/picoflash.py --flash ..\..\firmware\micropython-1m-v1.29.0.bin --addr 0x0

# 高速 + 整片擦除 + 校验
python host/picoflash.py --flash fw.bin --addr 0x0 -b 921600 --erase --verify

# 只控电源（测上电电流、冷启动）
python host/picoflash.py --power cycle
python host/picoflash.py --power off

# 串口监视
python host/picoflash.py --monitor -b 115200
```

脚本做的事：

1. 打开控制口，发 `AUTO 0` 接管（防止 pyserial 打开数据口时的 DTR/RTS 把 ESP 按在复位）
2. `PWR 0` → 400ms → `PWR 1`
3. `BOOT`（IO0 拉低 + EN 脉冲）
4. 调 esptool：`--before no-reset --after no-reset`（esptool 4.x/5.x 参数差异已自动适配）
5. `RUN` 复位运行，恢复 `AUTO 1`

### 方式 B：esptool 裸用

固件把 DTR/RTS 映射到 IO0/EN（nodemcu 反相约定），所以可以直接：

```powershell
python -m esptool --port COM7 -b 921600 --before default-reset --after hard-reset `
    write-flash -z 0x0 firmware.bin
```

前提：`AUTO 1`（默认值），且控制口没被别人占着。

| esptool 信号 | 固件动作 |
|---|---|
| DTR = 1 | ESP GPIO0 拉低 |
| RTS = 1 | ESP EN 拉低（复位） |

这正是 `ClassicReset` 序列的假设，所以 `default-reset` / `hard-reset` 都能直接工作。

---

## 4.1 SWD 调试版（可选，`-DWITH_DAP=ON`）

同一块 Pico 再加一路 **CMSIS-DAP v2** 调试器，与 3 路 CDC 共存于一个 USB 设备（4 个接口，端点 11/15）。

```powershell
# 3 路 CDC（纯串口）
cmake -S firmware -B firmware/build -DWITH_DAP=OFF; cmake --build firmware/build
#   -> firmware/build/esp-flasher.uf2        (94 KB)

# 3 路 CDC + SWD
cmake -S firmware -B firmware/build -DWITH_DAP=ON; cmake --build firmware/build
#   -> firmware/build/esp-flasher-dap.uf2    (99 KB)
```

| Pico | 目标板 |
|---|---|
| GP14 | SWCLK |
| GP15 | SWDIO |
| GP13 | nRESET（可选，低有效） |
| GND | GND |

```bash
# pyOCD
pyocd list
pyocd flash -t <target> firmware.bin

# OpenOCD
openocd -f interface/cmsis-dap.cfg -f target/<chip>.cfg
```

**实现要点**

| 项 | 说明 |
|---|---|
| 协议 | CMSIS-DAP v2（Vendor Bulk 0x02/0x8A，Interface 6，VID 2E8A / PID 1001） |
| SWD 时序 | CPU 位翻转，默认 1 MHz；`DAP_SWJ_Clock` 可调 100 kHz ~ 4 MHz |
| 实现范围 | **只做 DAP 传输层**（DP/AP 读写、Transfer/TransferBlock），flash 算法由 pyOCD 写目标 RAM 执行 |
| 支持命令 | `DAP_Info` `Connect` `Disconnect` `Transfer` `TransferBlock` `WriteABORT` `Delay` `ResetTarget` `SWJ_Pins` `SWJ_Clock` `SWJ_Sequence` |
| 为什么不用 PIO | DAP 事务在主循环串行执行，期间 CDC 数据由 ring 兜住；换成 PIO 需要在双路高速场景才划算 |

⚠️ **Windows 需要驱动**：DAP 接口是 Vendor Bulk，用 [Zadig](https://zadig.akeo.ie/) 给
`Pico ESP Flasher` 的 **CMSIS-DAP** 接口装 WinUSB；或直接用 pyOCD 自带的 libusb 驱动。
CDC 接口不受影响，仍是免驱 COM 口。

---

## 5. 已验证 / 未验证
**2026-11-21 本机实测（RP2040 / FW 1.1.0 / C 版）**

| 项 | 结果 |
|---|---|
| 编译（pico-sdk 2.1.0 + gcc 14.2.1） | ✅ 91,624 B bin → 183,296 B uf2 |
| **双 CDC 枚举** | ✅ **COM12 = 控制口，COM15 = 数据口** |
| 控制口 banner | ✅ `PICO-ESP-FLASHER 1.1.0 ready` |
| 数据口静默 | ✅ 发 `INFO` 无任何回显（不污染 esptool 同步） |
| `GP / GPIN / GPREAD` | ✅ `GP 5 1` → `GPREAD 5` 回 1 |
| `PIN / POL / PINS` | ✅ `POL PWR 0` → MAP 变 `GP4/L`；`PIN EN 6` → MAP 变 `GP6/H` |
| `PWR` | ✅ `pwr=1` / `pwr=0` 状态正确 |
| `--info` 自动识别双口 | ✅ 自动判出 COM12/COM15 |
| **真实 ESP8266 联调** | ✅ MAC `dc:4f:22:04:9c:ef`，Chip ID `0x00049cef`，flash 1MB(a1) |
| **完整烧录 + Hash 校验** | ✅ 64KB 写入 4.0s @131 kbit/s，`Hash of data verified`，写回后 chksum 与原值一致 |
| 内部回环 `LOOPBACK` | ✅ `TX 55 55 55 55 AA AA AA AA` 原样回显 |

**MicroPython 版（2026-11-20 实测）**：v1.29.0 刷入成功、`main.py` 自动运行、`pwr/io0/en/boot/run`
状态位正确、`select.poll` 可用、`passthru()` 能进能出。

### 踩过的三个坑（都已修）

| 坑 | 症状 | 原因 |
|---|---|---|
| uf2 的 `magicEnd` 放错位置 | bootloader 收下文件但不重启 | 数据区是 476 字节，magicEnd 固定在第 508 字节，不是 `32+payload` |
| 缺 `CFG_TUSB_RHPORT0_MODE` | 烧完完全没反应，设备节点都不出现 | 默认 `OPT_MODE_NONE`，USB 控制器根本不启动 |
| `bDeviceClass` 写成 CDC(0x02) | 描述符非法被拒 | `TUD_CDC_DESCRIPTOR` 自带 IAD，设备类必须是 `0xEF/0x02/0x01` |

### Windows 上 RTS 不可靠

`pyserial` 设 `rts` 走 `EscapeCommFunction(SETRTS)`，**不会**立刻发出
`SET_CONTROL_LINE_STATE`，要等下一次 `SetCommState` 才捎带过去 —— 逐位跟随会让 EN 晚一拍。

固件因此改成：看到 **DTR=1 且 RTS=1** 这个组合才走完整时序，不依赖两根线的先后。
esptool 的 `ClassicReset` 第 3 步 `setDTR(True)` 正好落在 RTS=True 上，能触发。
但更稳的做法仍是 `picoflash.py --flash`（控制口发 `BOOT` + esptool 加 `--before no-reset`）。

---

## 5.1 本机当前状态

```
Pico: RP2040，C 固件 1.1.0
控制口: COM12   (VID 2E8A / PID 1001)
数据口: COM15   (同一个复合设备的另一接口)
默认映射: pwr=GP4/H  io0=GP2/H  en=GP3/H
```

以后升级固件：控制口发 `BOOTSEL` → 出现 `RPI-RP2` 盘 → 拖 uf2，不用再按按键。

---

## 6. 已知坑

| 现象 | 原因 | 处理 |
|---|---|---|
| ESP 一发射就重启，Pico 也掉线 | 从 Pico 3V3(Pin36) 取电，RT6150 扛不住 | 必须走 VBUS → AMS1117 → Q1 |
| 烧录校验随机失败 / 同步超时 | 缺 470µF 储能，WiFi 峰值拉垮 3.3V | 加 C3；或降波特率到 460800 |
| ESP 断电后仍有微弱电流 | GP0(TX) 高电平经 ESP RX 的 ESD 二极管倒灌 | 已串 1kΩ；固件断电前会把 TX 拉低转输入 |
| 打开串口后板子不动 | pyserial 默认 DTR=RTS=True → EN 被拉低 | 用 `--monitor`（脚本已设 dtr/rts=False），或发 `RUN` |
| 只枚举出一个 COM 口 | Windows 未识别复合设备 | 设备管理器看是否有黄色感叹号；重插；换 USB 口 |
| esptool 报 `no serial data received` | `AUTO 0` 还开着 / IO0 没拉低 | 发 `STATUS` 确认 `io0=1`，或先 `BOOT` 再烧 |
| AMS1117 烫手 | 5V→3.3V 线性，500mA 时耗散 0.85W | 铺铜散热，或换 MP1584/SY8088 同步 Buck |

---

## 7. 目录

```
pico_esp_flasher/
├── README.md
├── firmware/              C 固件（pico-sdk）
│   ├── main.c             透传 + DTR/RTS 映射 + 控制台
│   ├── tusb_config.h      双 CDC + USB 身份
│   ├── CMakeLists.txt
│   └── build.ps1          一键拉 SDK 并编译
├── firmware-mpy/          MicroPython 备选
│   └── main.py
├── hardware/
│   ├── wiring.svg         信号接线图
│   ├── schematic.svg      电源与开关器件级图
│   └── BOM.md
└── host/
    └── picoflash.py       上位机：电源 / IO0 / BOOT / esptool 封装
```

---

## ⚠️ DC1 模组烧录时序（2026-11-21 主人确认，强制遵守）

**接线**：`GP2` 直连模组 `IO0`；`GP4` 经 NMOS **反相**控制 `3V3_EN`（GP4=0 上电，GP4=1 断电）。
**波特率**：**115200**（固定，不得更改）。
**复位手段**：只靠 `IO0` + `3V3` 通断，**模组没有 CH_PD/EN 控制线**（板载已处理，勿再纠结）。

### 烧录（进下载模式）
```
GP4 高(断电) 0.5s  →  IO0 拉低  →  GP4 低(上电) 0.5s
```

### 运行（正常启动）
```
GP4 高(断电) 0.5s  →  IO0 拉高  →  GP4 低(上电) 0.5s
```

### 实测备注
- `GP4=0` 时 `3V3_EN` 被拉高到 **11V**，`3.3V` 输出正常。
- 不要先上电再断电（旧写法多一次上电，时序错误）。
- 烧录期间**不要用 mpremote 连 COM9**（会发 Ctrl-C 打断透传桥）。
