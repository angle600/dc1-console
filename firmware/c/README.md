# DC1 C 版固件（ESP8266_RTOS_SDK v3.4）

> 与 `firmware/micropython/` **并存**，不是替换。MicroPython 版保持原样。

## 为什么做 C 版

MicroPython 版在 1 MB flash 上已经撞到天花板：

| 约束 | 数值 |
|---|---|
| `app.mpy` 上限 | **16,205 B**（再多 1,300 B 就 `MemoryError: allocating 1336 bytes`）|
| 编译出来 | 17,454 B |
| 可用堆 | 稳态 ~5.6 KB |
| 后果 | `app.py` 一行都改不动；`/api/reboot` 有概率把文件系统写坏 |

C 版把内存占用从「30 KB 解释器 + 5 KB 堆」压到「几 KB 全局变量 + 80 KB 系统堆」，
不再有 mpy 体积红线，`/api/reboot` 也没有文件系统元数据悬挂的问题（配置走 NVS，
每次写入即 commit）。

## 目录

```
firmware/c/
├── CMakeLists.txt          顶层工程
├── sdkconfig.defaults      1MB flash / 160MHz / 关掉用不到的大件
├── build.sh                构建脚本（固化环境变量）
└── main/
    ├── CMakeLists.txt
    ├── dc1.h               公共定义（通道、配置结构、模块接口）
    ├── main.c              启动顺序 + WiFi + 事件 + 主循环
    ├── cfg.c               配置存储（NVS）
    ├── relay.c             CAT9554 继电器 / 按键 / LOGO 灯
    ├── sched.c             定时任务 + 倒计时
    └── httpd.c             HTTP 服务与全部 API
```

## 构建

```bash
# 依赖
#   工具链: D:\work\toolchain\xtensa-lx106-elf\        (gcc 8.4.0 esp-2020r3)
#   SDK   : D:\work\toolchain\ESP8266_RTOS_SDK_v3.4    (v3.4, 2021-04-08)

cd firmware/c
./build.sh          # 增量
./build.sh clean    # 清理重建
```

产物：`build/dc1.bin`，烧到 `0x10000`（app 分区）。

## 与 MicroPython 版的功能对照

| 功能 | MP 版 | C 版 | 备注 |
|---|---|---|---|
| `/api/ping` `/info` `/state` | ✅ | ✅ | 字段一致 |
| `/api/switch` | ✅ | ✅ | 含 total 联动、logo 跟随 |
| `/api/schedule` | ✅ | ✅ | `days` 位掩码，0=单次 |
| `/api/countdown` | ✅ | ✅ | 到点执行后自删 |
| `/api/config` | ✅ | ✅ | STA 需 token，AP 免 |
| `/api/reboot` | ⚠️ 有概率挂死 | ✅ | 配置走 NVS，无 FS 悬挂 |
| `/api/put` `/api/get` | ✅ 文件级 OTA | ⚠️ 返回 501 | C 版没有解释器，做固件级 OTA 需双分区 |
| HTTP 首页 | ✅ 7,788 B | ✅ 内嵌 ~1.7 KB | 极简控制页 |
| 继电器 4 路 | ✅ | ✅ | CAT9554 P7~P4，500 ms 间隔 |
| 按键 | ✅ | ✅ | U7 P0~P2 + GPIO16，短按翻转 |
| LOGO 灯 | ✅ GPIO14 | ✅ GPIO14 | 低电平点亮 |
| 计量 | 已禁用 | 占位 `{}` | CSE7766 硬件损坏 |

## 与 MP 版的关键差异

**时间基准**
- MP 版：RTC 直接存北京时间 → `/api/state.time` 比标准 epoch **大 28800**，客户端必须减掉
- C 版：RTC 走 UTC + `TZ=CST-8` → `time()` 是真 UTC，`localtime_r()` 是北京时间

**复位可靠性**
- MP 版：`machine.reset()` 时 LittleFS 元数据可能与 SDK 写 rf cal 冲突 → FS 损坏 → 卡在启动早期
- C 版：配置全部走 NVS（`nvs_commit` 同步落盘），没有待提交的文件系统元数据

**OTA**
- MP 版：HTTP 分片写 `app.mpy`，256 B/片（请求体 >600 B 会被截断）
- C 版：`/api/put` 暂返回 501。真固件 OTA 需要 `ota_0`/`ota_1` 双分区，
  1 MB flash 放不下两个 ~400 KB 的 app，所以固件升级走串口烧录

## 已知限制

1. **`/api/put` 未实现**：返回 `501`，C 版没有文件系统
2. **UDP 54321 未实现**：MP 版的 UDP 发现/快速配网在 C 版里还没做（MP 版本身也不通）
3. **AP 配网模式下 HTTP 免 token**：与 MP 版一致，热点本身就是门槛

## 硬件接线（与 MP 版相同）

| 信号 | 引脚 |
|---|---|
| U7 SCL / SDA | GPIO12 / GPIO3（20 kHz，简配片） |
| LOGO 灯 | GPIO14（低电平亮） |
| 总键 | GPIO16（低有效） |
| 烧录 UART | GPIO1(TX) / GPIO3(RX) |
