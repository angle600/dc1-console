# DC1 启动器（带自愈：崩溃 -> 下次启动留在 REPL）
import os
import sys
import time
import machine

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
            with open("safe.flag", "w") as f:
                f.write("1")
        except Exception:
            pass
        print("APP FAIL:", e)
        time.sleep(1)
        machine.reset()
