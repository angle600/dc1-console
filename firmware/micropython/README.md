# 设备端固件（MicroPython / ESP8266 1MB）

## 文件

| 文件 | 作用 | 备注 |
|---|---|---|
| `main.py` | 启动、UDP 发现/配网、HTTP API、主循环（按键/计量/定时/倒计时） | 单线程 select 模型，无 asyncio |
| `store.py` | 定时规则、倒计时、电量累计的持久化 | 20 行级 JSON 落盘，限流写 |
| `cat9554.py` | U7 IO 扩展（P0~P7） | I2C **必须 20kHz** |
| `cse7766.py` | 电量计帧解析（24B / 4800 8E1） | 输出原始计数 + 可标定系数 |
| `webui.py` | 手机控制页（开关/定时/倒计时/电量） | 无外部 JS/CSS |
| `config.example.json` | 配置模板 | 复制到设备根目录为 `config.json` |

## 部署

```bash
# 首次：串口烧内核 + 传代码（见 README「快速开始」）
python tools/mp_deploy.py --serial COM5 --erase --flash-img ESP8266_GENERIC-FLASH_1M-*.bin
python cli/dc1ctl.py --ip 192.168.4.1 fw update --dir firmware/micropython

# 之后：纯 WiFi 更新代码（设备需已入网）
python cli/dc1ctl.py fw update --dir firmware/micropython
```

`fw update` 走 `PUT /api/file?name=...`，逐个覆盖 `.py`；**重启后生效**（`cli/dc1ctl.py reboot`）。

## 可选：自己交叉编译内核 `.bin`

本机没有 `xtensa-lx106-elf-gcc`，所以仓库**不含** `.bin`；需要自建时：

```bash
git clone -b v1.29.0 --depth 1 https://github.com/micropython/micropython
cd micropython/ports/esp8266
make BOARD=ESP8266_GENERIC BOARD_VARIANT=FLASH_1M -j8   # 需 xtensa-lx106-elf 工具链在 PATH
# 产物：build-ESP8266_GENERIC-FLASH_1M/firmware.bin
esptool.py --port COM5 write_flash --flash_size=1MB 0x0 firmware.bin
```

已在 Windows 验证的只有前半段：`py_compile` 全通过、`mpy-cross`（v1.29.0 / mpy v6.3）
把 5 个模块编成 `.mpy`（`firmware/build/mpy/`），可用 `fw update --mpy` 上传以省 RAM。

## 运行期文件（设备根目录）

```
config.json     WiFi / 设备名 / api_token / meter.gain
state.json      各路开关状态（断电记忆）
sched.json      定时规则 {"rules":[...],"next":n}
count.json      倒计时条目
energy.json     {"total":kWh,"today":kWh,"doy":天}
```

## 定时语义

```json
{"id":3,"ch":"1","on":0,"hh":23,"mm":0,"days":0,"en":1}
```

- `days = 0` → **单次**，到点触发一次后规则自动删除；
- `days != 0` → 每周重复，位掩码 bit0=周一 … bit6=周日，`127` = 每天；
- 触发判定为"每分钟一次"，靠 `_last_key` 防重复；
- 依赖设备时间：开机 `sntp_sync()` 一次；失败时用 `cli/dc1ctl.py time` 校准。

## 救援模式

上电时按住**总按键**（GPIO16）→ 跳过 CSE7766 初始化，UART0 保持 GPIO1/3，
串口 REPL（115200）可用。原因：CSE7766 需要把 UART0 swap 到 GPIO15/13，会把 REPL 一起搬走。

## 上机前必看

- 未在实物上验证过；先跑 `docs/04-MicroPython方案.md` 第 9 节的 8 项验收；
- 计量系数默认 1.0，按第 8 节用 60W 白炽灯标定 `meter.p_gain`；
- 1MB Flash **不支持整片固件 OTA**，只能串口刷内核（`fw core`）。
