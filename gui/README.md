# 上位机（Qt 6.8.5 MinGW64 + QML）

## 结构

```
gui/
├── CMakeLists.txt          # qt_add_executable + qt_add_resources（QML 打进 qrc）
├── main.cpp                # QGuiApplication + Fusion 风格 + 注入 Cli 到 QML
├── clirunner.{h,cpp}       # Cli：QProcess 调 cli/dc1ctl.py，单通道、JSON 回传
└── qml/
    ├── Main.qml            # 侧栏设备列表 + 主区 Loader（按 type 选面板）
    ├── Dc1Panel.qml        # DC1 面板：开关/计量/电量/定时/倒计时
    ├── GenericPanel.qml    # 未知类型兜底：原始 JSON
    ├── ProvisionDialog.qml # UDP 配网（30s 窗口提示）
    └── FirmwareDialog.qml  # WiFi 覆盖源码（选目录 + 上传）
```

**GUI 不做业务逻辑**：所有设备操作都变成 `python cli/dc1ctl.py ... --json`，
`Cli` 只负责起进程、收 stdout、把最后一段 JSON 抛给 QML。

## 构建

```bat
:: 依赖：Qt 6.8.5 MinGW64（含 Quick / QuickControls2）、CMake 3.21+、MinGW g++
cd gui
cmake -S . -B build -G "MinGW Makefiles" ^
      -DCMAKE_PREFIX_PATH=D:/Qt/Qt6.8.5MinGW64 ^
      -DCMAKE_C_COMPILER=D:/Qt/Tools/mingw1310_64/bin/gcc.exe ^
      -DCMAKE_CXX_COMPILER=D:/Qt/Tools/mingw1310_64/bin/g++.exe
cmake --build build -j
build\dc1console.exe
```

打包成**自包含目录**（推荐，双击即用）：

```bat
cd gui
:: 若被执行策略拦住，可先用 PowerShell 执行：Set-ExecutionPolicy -Scope Process Bypass
.\deploy.ps1
:: 产物：gui\release\  （约 178 MB，含 Qt6Core.dll 等 38 个库 + qml/ 模块）
.\release\dc1console.exe
```

目录里已经放好 `Qt6Core.dll / Qt6Gui.dll / Qt6Qml.dll / Qt6Quick*.dll`、`platforms\qwindows.dll`、
`qml\` 模块树、`libstdc++-6.dll` 等 MinGW 运行库，**不需要 Qt 在 PATH 里**。

## 运行时定位 CLI

`Cli::resolveCli()` 按顺序找：

1. 环境变量 `DC1_CLI`（指向 `cli/dc1ctl.py`）
2. `./cli/dc1ctl.py`、`../cli/dc1ctl.py`、`../../cli/dc1ctl.py`（相对 exe）
3. 编译期写入的源码树路径 `DC1_SOURCE_DIR`

Python 解释器取 `DC1_PYTHON`，否则默认 `python`（Linux/macOS 为 `python3`）。
需要 Python ≥ 3.8，仅标准库。

## 扩展新设备类型

1. 新建 `qml/Sc1Panel.qml`，属性按需（参考 `Dc1Panel.qml` 的 `state` / `rules`）；
2. `Main.qml` 里加 `Component { id: sc1Comp; Sc1Panel { ... } }`；
3. `sourceComponent` 增加映射，未知类型保持 `genericComp` 兜底；
4. `CMakeLists.txt` 的 `FILES` 里加上新 qml 文件。

## 常见问题

| 现象 | 原因 / 处理 |
|---|---|
| **提示找不到 `Qt6Core.dll`** | 直接跑 `build\dc1console.exe` 就会这样——构建产物不含运行库。改跑 `release\dc1console.exe`（由 `deploy.ps1` 生成），或临时把 `D:\Qt\Qt6.8.5MinGW64\bin` 加进 PATH 调试 |
| `Failed to start python` | Python 不在 PATH；设 `DC1_PYTHON` 或装 Python |
| 界面空、状态栏报失败 | 先看 CLI 单独跑是否通：`python cli/dc1ctl.py discover` |
| `Module "QtQuick.Controls" is not installed` | 用 `windeployqt --qmldir qml` 打包，或检查 `QML2_IMPORT_PATH` |
| 中文乱码 | 已强制 `PYTHONIOENCODING=utf-8`；若自改 CLI 输出，保持 UTF-8 |
