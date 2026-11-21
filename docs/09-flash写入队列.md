# 09 · Flash 写入队列设计（DC1 / ESP8266 1MB）

> 状态：**设计完成，未上线**。
> L1（reset 守卫）已部署在 `main.py`；L2（写入队列）源码已写出但受 1 MB 版体积限制无法编译部署；
> L3（文件系统自愈）未实现。换 4 MB 模块后可直接落地本方案。

---

## 1. 要解决的问题

### 1.1 现象

`/api/reboot` 三次坏两次：

```
POST /api/reboot  ->  {"ok":1}
（之后）
Ping          : 通（TTL 255，说明网络栈活着）
TCP 80        : 拒绝（RST）
串口          : 只有 74880 的 ROM 日志，没有 MicroPython banner
断电重上电    : 无效，症状完全一样
硬复位 2s 泄放: 无效
全片擦除+重烧 : 立即恢复
```

### 1.2 定位

ROM 层每次都是正常的：

```
ets Jan  8 2013,rst cause:1, boot mode:(3,7)
load 0x40100000, len 31212, room 16
…
csum 0xd0
rf cal sector: 251
```

`boot mode:(3,7)` 是从 flash 启动，`csum 0xd0` 校验通过，且 40 秒抓取里 **只出现一次 `ets Jan`**
（不是重启循环）。但没有 MicroPython banner。

ROM 之后到 banner 之间只有四步：

```
ROM boot2 → MicroPython 初始化 → 挂载 LittleFS → 执行 boot.py → 打印 banner
                    ✅                ❓
```

`boot.py` 或 `import app` 失败会打印 `APP FAIL` / `SAFE BOOT`，串口能抓到；实际一个字都没有，
所以**卡在挂载文件系统**。LittleFS 挂载失败会尝试格式化，反复失败就一直不出声。
这解释了「断电也没用」——坏掉的 superblock 还在 flash 里。

### 1.3 根因（推断，非实测）

`machine.reset()` 触发时，**LittleFS 的元数据提交与 SDK 重启时写 rf cal（sector 251）
撞在同一颗 flash 上**。

时序对得上：

1. G10 测试前连续写了 `state.json`、`sched.json`、`count.json`
2. 紧接着 `/api/reboot` → 主循环执行 `machine.reset()`
3. 重启瞬间 SDK 要写 rf cal，与尚未完全落盘的 FS 元数据冲突

没能抓到挂载阶段的报错原文（没有 banner，后面就再没有输出），所以这一条只能算最符合证据的推断。

---

## 2. 设计目标

| 目标 | 说明 |
|---|---|
| **单一入口** | 所有关键状态写 flash 只走一个函数，不再散落 `open(...).write()` |
| **串行化** | 同一时刻只有一个写操作，天然满足（MicroPython 单线程），但要防止一次循环里连写多个文件 |
| **限速** | 两次写之间留最小间隔，避免连续擦写把 flash 控制器压满 |
| **可等待** | 重启/OTA 前能「刷完队列 + 等静默」，把写窗口与 rf cal 错开 |
| **不阻塞** | 队列满时丢最旧的，绝不阻塞主循环——1 MB 版堆只有 ~5 KB，阻塞会让 HTTP 直接超时 |

---

## 3. 三级防护

```
L1  reset 守卫   ← 已部署（main.py，830 B，不受 mpy 体积限制）
L2  写入队列     ← 本设计，源码完成但未部署
L3  FS 自愈      ← 未实现，需 4 MB 模块腾出空间
```

### L1 · reset 守卫（当前唯一生效的）

`app.py` 里所有复位都走 `machine.reset()`（模块属性访问），所以在 `main.py` 里替换即可：

```python
import gc as _gc
_orig_reset = machine.reset

def _safe_reset():
    try:
        _gc.collect()
        time.sleep_ms(1200)      # 给 LittleFS 元数据一个落盘窗口
    except Exception:
        pass
    _orig_reset()

machine.reset = _safe_reset
```

**实测效果**

| 版本 | 连续 5 次 `/api/reboot` |
|---|---|
| 改之前 | 3 次坏 2 次 |
| 加守卫后 | **5/5 恢复，9–15 秒** |
| 跑完全量压测后 | 仍会卡在启动早期 |

**结论**：L1 降低概率，未根治。它只保证「复位那一刻」错开，管不到运行期的连续写。

### L2 · 写入队列（本设计主体）

#### 数据结构

```python
_fq = []          # [(path, text)]，text 为已序列化字符串
_fq_at = 0        # 上次写完的 ticks_ms
_FQ_GAP = 80      # 两次写之间的最小间隔 ms
_MAXQ = 3         # 队列上限
```

**为什么存字符串而不是对象**：入队后如果对象被改，落盘内容和调用时刻的语义不一致。
序列化在入队时一次做完，之后不再碰原对象。

**为什么队列上限只有 3**：1 MB 版堆 ~5 KB，每条 JSON 几百字节。超过 3 条说明调用方在短时间内
改了多个状态，此时丢最旧的（后写的更新），比让队列无限增长安全。

#### 接口

```python
def fq_put(path, text):
    """入队一次写入。"""
    if len(_fq) >= _MAXQ:
        _fq.pop(0)          # 丢最旧的，绝不阻塞
    _fq.append((path, text))


def fq_pump():
    """主循环调用：间隔够就落一个文件。返回本次是否写了。"""
    global _fq_at
    if not _fq or time.ticks_diff(time.ticks_ms(), _fq_at) < _FQ_GAP:
        return 0
    p, t = _fq.pop(0)
    try:
        with open(p, "w") as f:
            f.write(t)
    except Exception:
        pass
    _fq_at = time.ticks_ms()
    return 1


def fq_drain(tmo=800):
    """重启前调用：清空队列 + 等静默期。"""
    global _fq_at
    t0 = time.ticks_ms()
    while _fq and time.ticks_diff(time.ticks_ms(), t0) < tmo:
        p, t = _fq.pop(0)
        try:
            with open(p, "w") as f:
                f.write(t)
        except Exception:
            pass
        _fq_at = time.ticks_ms()
    # 静默期：确保最后一次写的元数据提交完成
    while time.ticks_diff(time.ticks_ms(), _fq_at) < 150:
        time.sleep_ms(10)
```

#### 接入点

只有三处改动：

```python
# 1) save_json 改为入队（所有 state/config/sched/count 都经过它）
def save_json(path, obj):
    try:
        fq_put(path, json.dumps(obj))
        return True
    except Exception:
        return False

# 2) 主循环每轮泵一次（放在 reset_pending 判断之前）
fq_pump()
if reset_pending:
    fq_drain()
    time.sleep_ms(150)
    machine.reset()

# 3) 不再有第四处 —— 日志仍同步写，量小且丢了不影响功能
```

#### 时序对比

```
改造前：  HTTP 请求 → open/write → 返回       ← 写发生在请求处理中，可能连续多次
          /api/reboot → reset                 ← 可能撞上刚写完的元数据

改造后：  HTTP 请求 → fq_put → 返回            ← 只入队，不碰 flash
          主循环   → fq_pump（≥80ms 一次）    ← 写被摊到多个循环
          /api/reboot → fq_drain → 等 150ms → reset  ← 写窗口与 rf cal 错开
```

### L3 · FS 自愈（未实现）

启动时探测文件系统，异常则格式化重建，避免「卡死只能重烧」。难点在于 ESP8266 上格式化需要
直接操作块设备（`esp.flash_erase` 之类），要极其小心别把固件区擦了。优先级低于换 4 MB 模块。

---

## 4. 体积约束：为什么 L2 没能上线

`app.py` 编译产物与设备上限的对照：

| 阶段 | mpy 大小 | 说明 |
|---|---|---|
| 加内联队列（未精简） | 17,838 | 只加了 ~40 行 |
| 删计量实现 + UDP put | 17,234 | `init_meter/free_meter/set_gains` 改空壳；UDP `put` 分支删掉 |
| 删计量路由 | 17,001 | `/api/meter`、`/api/energy` 合并为返回 `{}` |
| 删 LOG_TX 日志口 | 16,779 | 去掉 UART1/GPIO2 日志输出与初始化 |
| **设备上限** | **16,205** | 1 MB 版 `free` 约 5 KB，超过就 `MemoryError: allocating 1336 bytes` |

**还差 574 B（约 57 行）**，剩下的全是核心功能：按键轮询、配网、继电器时序、HTTP 路由。

`mpy-cross` 的 `-O/-Os/-O0/-msmall-int-bits/-march` **对体积完全无影响**（都是同一个数）；
删 10 行死代码只省 80 B。所以「靠删代码挤出 1.3 KB」不现实。

**结论**：`app.py` 任何改动都无法部署到 1 MB 版。仓库里 `app.py` 保持原状（`git checkout`）。

---

## 5. 迁移步骤（换 4 MB 模块后）

1. 刷 4 MB 版固件（`micropython-4m-*.bin`），堆从 ~5 KB 升到 ~40 KB
2. `app.py` 加回第 3.2 节的三个接入点（约 40 行）
3. 重新编译：`mpy-cross -o app.mpy app.py`，此时体积不再是约束
4. 用 `tools/factory_program.py --skip-flash` 部署
5. 用 `tools/test_reboot.py 10` 跑 10 次连续重启，确认恢复率
6. 用 `tools/test_dc1.py --groups 1,2,3,4,5,6,7,8,9 --with-reboot --with-ota` 全量回归

**验收标准**：连续 10 次 reboot 全部恢复，且压测后不出现「卡在启动早期」。

---

## 6. 附：相关缺陷清单（本次测试发现）

| 缺陷 | 状态 | 说明 |
|---|---|---|
| reboot 后卡在启动早期 | L1 缓解，未根治 | 需 4 MB 模块 + L2 |
| UDP 54321 无响应 | 未修 | `discover/who/log/prov` 全超时，HTTP 正常；疑似 `us=None` 且没记日志 |
| `/api/get` 大偏移断连 | 不修 | LittleFS seek + 堆限制，1 MB 版无解；OTA 校验改用抽样 + 设备端 sha256 |
| `/api/state.time` 偏大 28800 | **不是缺陷** | `sntp_sync` 与 `/api/time` 都主动 `+8*3600`，RTC 存北京时间；客户端需减 28800 才当 UTC 用 |
