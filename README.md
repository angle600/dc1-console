# 斐讯 DC1 智能排插 —— 硬件资料 + 刷机 + 手机配网 + AI 内网控制

官方云服务早已停运，原厂 App 不可用。本项目把 DC1 改成**纯内网自持**设备：
一次 TTL 线刷 ESPHome 固件 → 手机热点配网 → AI/脚本通过局域网 Native API 控制。

```
手机（热点配网） ─┐
                 ├── 2.4G WiFi ── DC1（ESP8266 + CAT9554 + CSE7766）
AI / 脚本 ───────┘                 ├ 总开关 + 3 路插孔 + LOGO 灯
                                   └ 电压 / 电流 / 功率 / 电量
```

## 快速导航

| 文档 | 内容 |
|---|---|
| [`hardware/README.md`](hardware/README.md) | **电路图资料 + 整机拓扑 + 引脚映射表**（官方无原理图，此为社区反推全套） |
| [`hardware/DC1-原理框图.svg`](hardware/DC1-原理框图.svg) | 自绘功率/信号框图，一张图看懂 |
| [`docs/01-刷机方案.md`](docs/01-刷机方案.md) | 拆机 → 备份原厂固件 → 编译 → 线刷 → 验收 → 回滚 |
| [`docs/02-手机配网.md`](docs/02-手机配网.md) | `DC1-Setup` 强制门户配网，无需装 App |
| [`docs/03-AI内网控制.md`](docs/03-AI内网控制.md) | Native API / Web / MQTT 三通道，`dc1ctl.py` 用法与自动化 |
| [`firmware/dc1-ai.yaml`](firmware/dc1-ai.yaml) | 定制 ESPHome 配置（AP 配网 + API + Web + OTA） |
| [`tools/dc1ctl.py`](tools/dc1ctl.py) | 局域网 CLI：`state / on / off / toggle / meter` |

## 三条命令跑通

```bash
cd firmware && cp secrets.example.yaml secrets.yaml   # 填 WiFi 与密钥
esphome run dc1-ai.yaml --device COM5                 # 线刷（仅首次）

pip install aioesphomeapi
python tools/dc1ctl.py state                          # 读状态
python tools/dc1ctl.py on 1                           # 开插孔1
```

## 目录结构

```
.
├── README.md
├── hardware/                 # 电路与硬件资料（图片/datasheet/自绘框图）
│   ├── README.md
│   ├── DC1-原理框图.svg
│   ├── image/                # 标注 PCB 图（正面/背面）、芯片特写
│   ├── datasheet/            # CAT9554 / CSE7766 / CSM64F02 / ESP-WROOM-02 / YX201
│   ├── logs/                 # 原厂固件日志（行为对照）
│   ├── cookbook/             # TTL 接线实拍
│   └── upstream-README.md    # 上游项目原始说明（保留出处）
├── docs/                     # 刷机 / 配网 / AI 控制
├── firmware/
│   ├── dc1-ai.yaml           # 主配置
│   ├── secrets.example.yaml
│   ├── components/cat9554/   # 本地驱动（U7 简配版需 20kHz）
│   └── upstream/             # 上游参考配置（api / mqtt 模板）
└── tools/dc1ctl.py           # 内网控制 CLI
```

## 关键结论（省你三天）

1. **模组就是 ESP8266**：芯海 CSM64F02 ≙ ESP-WROOM-02，1MB Flash，直接烧 ESPHome。
2. **U7 不是正品 CAT9554**：I2C 只能 ~20kHz，官方 `pca9554` 默认频率会挂 → 用本地组件或降频。
3. **GPIO 复用要小心**：UART0 已 swap 到 GPIO13/15 给 CSE7766，GPIO3 才空出来做 I2C SDA。
4. **GPIO0 双重身份**：板载 WiFi 灯 + 下载模式拉低脚；刷完务必断开 IO0-GND。
5. **总继电器 RELY0 串在入口**：关总闸 = 全排 + USB 断电，所以固件用 `shadow1~3` 记忆分路状态。
6. **原厂固件先 dump 再刷**：`read_flash 0 0x100000`，这是唯一的后悔药。

## 来源与许可

- 硬件照片、引脚标注、datasheet、上游 YAML：来自
  [Samuel-0-0/phicomm_dc1-esphome](https://github.com/Samuel-0-0/phicomm_dc1-esphome)（已停更，社区最全资料）。
- 本仓库新增：`DC1-原理框图.svg`、`firmware/dc1-ai.yaml`、`tools/dc1ctl.py`、`docs/*`。
- 仅限个人研究与自用，禁止商用；强电操作自负风险。
