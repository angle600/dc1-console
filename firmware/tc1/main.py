# TC1 启动器（换脑板 ESP-12S；带自愈：崩溃 -> 下次启动留在 REPL）
import os
import sys
import time
import machine

# 上电 LED 自检：灭 0.5 s 再点亮，确认板子活着。
# GPIO2 是启动 strap（必须为高才正常启动），LED 低电平点亮，所以这里先写 1。
try:
    from machine import Pin
    _led = Pin(2, Pin.OUT, value=1)     # 高电平 = 灭
    time.sleep_ms(500)
    _led.value(0)                       # 低电平 = 亮
    del _led
except Exception:
    pass

time.sleep(3)                      # 3 秒窗口：可 Ctrl-C 打断 / 救砖

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
        # 一级回滚：升级前的备份还在，直接还原
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
