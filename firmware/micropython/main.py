# DC1 启动器（带自愈：崩溃 -> 下次启动留在 REPL）
import os
import sys
import time
import machine

# ---------------------------------------------------------------- reset 守卫
# app.py 里所有 machine.reset() 都会先 GC 再等 1.2s 才真正复位。
# 目的：让 LittleFS 的元数据提交先落盘，与 SDK 重启时写 rf cal（sector 251）
#       错开时间 —— 2026-11-21 实测 /api/reboot 三次坏两次，重启后 FS 挂载卡死，
#       断电也救不回，只能重烧。该守卫不需要改 app.mpy（1MB 版体积已到上限）。
try:
    import gc as _gc
    _orig_reset = machine.reset

    def _safe_reset():
        try:
            _gc.collect()
            time.sleep_ms(1200)
        except Exception:
            pass
        _orig_reset()

    machine.reset = _safe_reset
    print("reset-guard on")
except Exception as _e:
    print("reset-guard fail:", _e)

# 开机 LOGO/ZERO 灯自检：上电先灭 0.5s，再点亮，然后才执行正常程序。
# GPIO14 低电平点亮 / 高电平熄灭；GPIO14 不是下载脚，不影响串口烧录。
try:
    from machine import Pin
    _logo = Pin(14, Pin.OUT, value=1)   # 高电平 = 灭
    time.sleep_ms(500)                  # 灭 0.5 秒
    _logo.value(0)                      # 低电平 = 亮
    del _logo
except Exception:
    pass

time.sleep(3)                      # 3 秒窗口：可 Ctrl-C 打断

if "safe.flag" in os.listdir():
    try:
        os.remove("safe.flag")
    except Exception:
        pass
    print("SAFE BOOT: app 已跳过（上次启动失败），REPL 可用")
    print("查看原因: print(open('err.txt').read())")
else:
    try:
        import app  # noqa: F401
    except Exception as e:
        try:
            with open("err.txt", "w") as f:
                sys.print_exception(e, f)
        except Exception:
            pass
        # 一级回滚：升级前的备份还在，直接还原（不占用 safe 窗口）
        try:
            if "app.bak" in os.listdir():
                os.remove("app.mpy")
                os.rename("app.bak", "app.mpy")
                print("APP FAIL -> 回滚 app.bak 并重启")
                time.sleep(0.5)
                machine.reset()
        except Exception as ee:
            print("rollback err", ee)
        try:
            with open("safe.flag", "w") as f:
                f.write("1")
        except Exception:
            pass
        print("APP FAIL:", e)
        time.sleep(1)
        machine.reset()
