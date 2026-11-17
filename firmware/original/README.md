# 本机原厂固件备份（Phicomm DC1）

> ⚠️ **版权提示**：本目录下的 `dc1_original_1M_user.bin` 是从**主人自己的设备**读出的原厂固件，
> 版权归原厂（斐讯 / 方案商）所有。此处仅作为**个人设备的还原点**存档，
> 请勿用于商业分发；需要给他人刷机时，应让对方 dump 自己的机器。

## 文件

| 文件 | 说明 |
|---|---|
| `dc1_original_1M_user.bin` | 本机原厂固件完整镜像（1 MB，逐字节 dump） |
| `MANIFEST.json` | 读法、时间、校验值、版本串 |

## 校验

```
size    : 1,048,576 B
sha256  : a547ffd1c890713ca2264533d78eab6a41f7c970a1d11c8ab9eaa22e45302610
magic   : 0xE9（合法 ESP8266 镜像）  flash 1MB  3 段  entry 0x4010057C
MAC     : dc:4f:22:04:9c:ef  （镜像内 0x0FD484）
版本串  : PHI_CS-EHS707_V0.2.34A_240 / PHI_CS-EHS707_V0.2.39B_324
```

镜像结构：

```
0x000000  bootloader  (0xE9, 3 段, 至 0xFE0)
0x001000  app         (0xEA, seg0 = 337,744 B, 约至 0x054000)
0x054000  文件系统 / NVS
0x060000–0x07FFFF、0x0E0000   全 0xFF（空白）
0x0FD484  MAC / system param
```

## 读法（可复现）

```bash
# 断线可续：8KB 分块 + 每块独立进程（详见 docs/05 §2.1）
python tools/dump_flash.py --port COM8 --size 1MB --chunk 0x2000 --baud 115200 \
    --out backup/dc1_original_1M.bin
python tools/join_dump.py --dir backup/dc1_original_1M.bin.chunks --size 1MB \
    --out backup/dc1_original_1M.bin
```

校验方式：读完后在 `0x0 / 0x1E000 / 0x54000 / 0xF0000` 四点抽样回读，逐字节一致。

## 与社区镜像的关系

社区项目 `Samuel-0-0/phicomm_dc1-esphome` 提供的 `dc1_original_1M.bin`
版本串为 `PHI_CS-EHS707_V0.2.39A_324`，与本机（`0.34A/0.39B`）**app 区差异 96.3%**，
不是同一版固件；`0xF0000` 段另有 623 B 差异（MAC / RF 校准 / NVS）。

→ 社区镜像只能救"能开机"，**本机这份才是逐字节还原点**。

## 回滚

```bash
python -m esptool --port COM8 --baud 115200 --no-stub \
    --before no-reset --after no-reset write-flash 0x0 firmware/original/dc1_original_1M_user.bin
```
