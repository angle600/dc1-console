# DC1 硬件与电路资料

> **重要前提**：斐讯从未公开 DC1 的原理图（PDF/EDA 源文件），社区所有"电路图"都是
> **实物反推**：拆机高清照 + 标注图 + 逻辑分析仪抓包 + 芯片 datasheet 典型电路。
> 本目录收录的就是这套可用资料（来源：Samuel-0-0/phicomm_dc1-esphome，见文末许可）。
> 另有本仓库自绘的 `DC1-原理框图.svg`，把散落的引脚结论整理成一张图，便于快速理解。

## 1. 资料清单

| 文件 | 内容 | 用途 |
|---|---|---|
| `image/主控制板正面_标注.jpg` | 模组引脚逐条标注（3.3V/SCL/RX1/IO0/LOGO 等） | **刷机接线第一手依据** |
| `image/主控制板背面_标注.jpg` | U7(CAT9554) 引脚 → P0~P7、P1 排线定义 | 继电器/按键位映射 |
| `image/主控制板正面.jpg` / `背面.jpg` | 未标注原图（1600px 级） | 自己复核走线 |
| `image/U7.jpg`、`image/U7底部线路走线.jpg` | IO 扩展芯片及背面走线 | 判断哪根线去哪 |
| `image/U11电量统计芯片.jpg` | CSE7766 位置与周边 | 计量电路定位 |
| `image/WiFi模组.jpg` | CSM64F02 模组（≙ ESP-WROOM-02） | 确认可刷 ESP8266 固件 |
| `image/电源板背面全景.jpg` | 电源/继电器板 | 强电侧结构与安全间距 |
| `datasheet/CAT9554.PDF` | IO 扩展芯片手册（寄存器 0/1/3） | 写驱动 |
| `datasheet/pcf8574.pdf` | PCF8574 手册（准双向口对照） | 理解差异 |
| `datasheet/U11_CSE7766用户手册.pdf`、`Cse7766-CHIPSEA.pdf` | 计量芯片手册 | 4800 8E1 帧格式 |
| `datasheet/CSE7766典型电路.png` | 官方典型应用电路（采样电阻/分压） | 校核硬件接法 |
| `datasheet/CSM64F02_WiFi模组用户手册V1.3.pdf`、`esp-wroom-02技术规格书.pdf` | 模组手册 | 引脚/Flash/天线 |
| `datasheet/永能家用继电器YX201.pdf` | 继电器手册（5V 线圈） | 驱动电流/触点额定 |
| `logs/原版固件log信息.pdf` | 原厂固件启动日志 | 对照 UART 行为 |
| `cookbook/TTL接线.jpg` | TTL 刷机接线实拍 | 线刷 |
| `DC1-原理框图.svg` | 本仓库自绘信号/功率流向图 | 一图看懂整机 |

## 2. 整机拓扑（自绘，依据实物与抓包）

```
AC L ── 保险丝/ NTC ── RELY0(总继电器) ─┬─ RELY1 ── 插孔1
                                        ├─ RELY2 ── 插孔2
                                        ├─ RELY3 ── 插孔3
                                        └─ USB 4 口降压电路
AC N ───────────────────────────────────┴─ 各插孔 N 公共

强电侧取电 ── PN8016 非隔离降压 ── 5V(继电器/逻辑) ── LDO ── 3.3V(模组/芯片)
火线串 1mΩ 级采样电阻 ── CSE7766(U11) ── 脉冲/ UART ── ESP8266 GPIO13
```

## 3. 主控模组（WiFi 模组 U1，丝印 CSM64F02）

- 芯海 CSM64F02，**与乐鑫 ESP-WROOM-02 引脚/内核一致**（ESP8266EX + 1MB Flash，
  板上丝印 `2AGM5CSM64F02`），因此可直接烧 ESP8266 固件。
- 板载 PCB 天线（ANT1 处），金属外壳内，实测信号一般，**别把排插塞进铁盒**。
- 正面标注的模组引脚（**逐脚对齐 ESP-WROOM-02 datasheet**，左侧 pin1~9 / 右侧 pin18~10）：

| 左侧标注 | GPIO / 真实身份 | 右侧标注 | GPIO / 真实身份 |
|---|---|---|---|
| 3.3V | pin1 = 3V3 供电 | GND | pin18 = 地 |
| 空 | pin2 = EN（使能） | KEY1 | pin17 = GPIO16 主电源按键（低有效） |
| RELAYLED | pin3 = GPIO14，LOGO 灯负极 | ADC1 | pin16 = TOUT（`ADTEST`，检测 VDD3P3） |
| SCL | pin4 = GPIO12，I²C 时钟 | 空 | pin15 = RST |
| **RX1** | pin5 = **GPIO13 = CSE7766 计量数据输入** | ZERO | pin14 = GPIO5 过零检测（未用） |
| 空 | pin6 = GPIO15（UART0 swap TX） | GND | pin13 = 地 |
| **LOG_TX** | pin7 = **GPIO2 = UART1 TXD（独立日志口）** | TX | pin12 = GPIO1 = UART0 TXD（刷机） |
| IO0 (WiFi_LED 负极) | pin8 = GPIO0，WiFi 灯/下载拉低脚 | RX | pin11 = GPIO3 = UART0 RXD（刷机，兼 I²C SDA） |
| GND | pin9 = 地 | NT | pin10 = GPIO4 = U7 中断 |

> ⚠️ **重要更正（2026-11-20 实测）**：旧版把 `RX1` 误标为 GPIO3、`LOG_TX` 误标为 GPIO1。
> 真相是 **RX1=GPIO13（计量线）**、**LOG_TX=GPIO2（UART1 日志口）**、右侧 RX 才是 GPIO3。
> 运行时 GPIO3 是 U7 的 I²C SDA——**刷机线烧完必须拔掉，否则 TTL 顶住 I²C 导致反复重启**。


## 4. 引脚映射表（固件唯一真相，对照 `firmware/dc1-ai.yaml`）

### 4.1 ESP8266 直连信号

| 功能 | GPIO | 方向 | 说明 |
|---|---|---|---|
| I2C SDA → U7 | GPIO3 | IO | UART0 已 swap 到 GPIO13/15，GPIO3 让给 I2C |
| I2C SCL → U7 | GPIO12 | O | 需限速 **20kHz**（U7 为简配 CAT9554，非 400kHz 正品） |
| U7 INT | GPIO4 | I | 下降沿中断，按键唤醒读 IO |
| CSE7766 RXD | GPIO13 | I | 4800bps / 8E1，模组单向发送 |
| 总按键 | GPIO16 | I | **实测空闲=高、按下=拉低（低有效）**；ESP8266 无 `PULLDOWN_16`，勿写死高有效 |
| 板载 WiFi 灯 | GPIO0 | O | 低电平亮；同时是**下载模式拉低脚** |
| LOGO 灯 | GPIO14 | O | 经 RELAYLED，低电平点亮（yaml `inverted: true`） |
| 调试/刷机 TX | GPIO1 | O | 模组右侧 `TX` 测试点（下载模式 UART0 TXD） |
| 调试/刷机 RX | GPIO3 | I | 模组右侧 `RX` 测试点（UART0 RXD，运行时是 I²C SDA，**烧完必拔**） |
| **日志 TX** | **GPIO2** | O | 模组左侧 `LOG_TX` 测试点（**UART1 TXD，独立日志口**） |
| 计量 RX | GPIO13 | I | 模组左侧 `RX1` 测试点（CSE7766 数据输入，勿接外部线） |

> **日志方案（v1.2.6 起，最终版）**：
> `log()` / `log_err()` 同时输出到 `boot.log`（HTTP `GET /api/log` 读）和 **UART1(GPIO2 / LOG_TX, 115200)**。
> UART1 只发不收、独立于 UART0——**计量(CSE7766@GPIO13/15)、刷机(UART0@GPIO1/3)、I²C(U7@GPIO3/12)、日志(UART1@GPIO2) 四者零冲突**。
> 现场打 LOG：只接 `LOG_TX → TTL 的 RX` 一根线，TTL 的 TX 悬空（GPIO2 是启动 strap，勿被拉低）。
> 灯行为：LOGO 灯（GPIO14）上电先灭 0.5s 再常亮；WiFi 灯（GPIO0）未连/AP 时 1s 周期慢闪、连上已记录 SSID 后长亮。

### 4.2 U7（CAT9554，I2C 0x20）—— IO 扩展，**不是计量芯片**

> 主控板背面照片确认：U7 = 16 脚 IO 扩展，周边走线为 `SCL/SDA(GPIO3)/NT(GPIO4)` + `RE1~RE4/KEY1~KEY3`；
> **计量芯片是旁边的 U9（8 脚，CSE7766）**，其数据从模组左侧 `RX1`(GPIO13) 进入。勿把 U7 当计量芯片。


| U7 位 | 网络 | 功能 | 固件实体 |
|---|---|---|---|
| P0 | KEY1 | 插孔1 按键（低有效） | `key1` → `relay1` |
| P1 | KEY2 | 插孔2 按键 | `key2` → `relay2` |
| P2 | KEY3 | 插孔3 按键 | `key3` → `relay3` |
| P3 | — | 未用 | — |
| P4 | RELY3 | 插孔3 继电器线圈 | `out_r3` |
| P5 | RELY2 | 插孔2 继电器 | `out_r2` |
| P6 | RELY1 | 插孔1 继电器 | `out_r1` |
| P7 | RELY0 | **总继电器**（整排 + USB） | `out_r0` |

CAT9554 寄存器：`0x00` 输入口、`0x01` 输出口、`0x02` 极性反转、`0x03` 方向配置
（1=输入）。本仓库 `firmware/components/cat9554/` 即按此实现（对上电恢复顺序做了特殊处理）。

### 4.3 刷机接线（读/烧同一组线）

| TTL | 板测试点 | 备注 |
|---|---|---|
| 3V3 | `3.3V` | 勿接 5V |
| GND | `GND` | 共地 |
| RX | `LOG_TX` | 交叉 |
| TX | `RX1` | 交叉 |
| — | `IO0`→GND | 仅下载模式短接 |

完整流程（含无 DTR/RTS 的手动进下载、`--before no_reset`、读/写/回滚命令）见
[`docs/01-刷机方案.md`](../docs/01-刷机方案.md) §5。

### 4.4 P1 排线（主控板 ↔ 电源板）

`5V | RE4 | RE3 | RE2 | K4 | K3 | K2 | GND`
—— 5V 与 4 路继电器驱动/回路信号 + 地，**拆机时先拔这条小排线**（焊盘极脆，
社区多人拽脱，见 `docs/01-刷机方案.md` 风险节）。

## 5. 关键电气参数

| 项 | 值 | 备注 |
|---|---|---|
| 输入 | 220V~ 50Hz | 强电，拆机必须断电并放电 |
| 单路继电器 | YX201 系列，5V 线圈 | 触点额定见 datasheet |
| 总回路 | RELY0 串在入口 | 关总开关 = 全排 + USB 断电 |
| 计量 | CSE7766，整排总功率 | 精度一般，仅作趋势/自动化触发 |
| USB | 4 口，经总继电器 | 无独立计量 |
| 逻辑电源 | 5V → 3.3V | 非隔离（PN8016），**调试时勿碰强电侧** |

## 6. 许可与致谢

硬件照片/标注图/datasheet 来自 [Samuel-0-0/phicomm_dc1-esphome](https://github.com/Samuel-0-0/phicomm_dc1-esphome)
（原项目已停止维护，资料仍为社区最完整的一套），仅作个人研究与维修使用。
致谢：killadm（原始固件导出、U7 抓包）、老妖（U7 驱动）、实验幼儿园小二班扛把子（引脚走向）、
Heller / 巴山耗子（资料整理）、风中的 summer（拆机照）、OttoWinter（ESPHome）。
