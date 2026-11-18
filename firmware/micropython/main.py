# DC1 启动器（带自愈：崩溃 -> 下次启动留在 REPL）
import os
import sys
import time
import machine

# 上电即点亮 LOGO 灯（GPIO14 低电平亮），越早越好。
# GPIO14 不是下载脚，此操作不影响串口烧录。
try:
    from machine import Pin
    Pin(14, Pin.OUT, value=0)
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
