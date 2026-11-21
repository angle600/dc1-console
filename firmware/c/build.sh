#!/usr/bin/env bash
# DC1 C 版固件构建脚本（ESP8266_RTOS_SDK v3.4 + xtensa-lx106-elf-gcc 8.4.0）
#
# 用法:
#   ./build.sh          增量构建
#   ./build.sh clean    清理后重建
#   ./build.sh flash    构建并烧录（需要 Pico 在 COM15 数据口）
set -e

HERE="$(cd "$(dirname "$0")" && pwd)"
SDK="${DC1_SDK:-/d/work/toolchain/ESP8266_RTOS_SDK_v3.4}"
TC="${DC1_TC:-/d/work/toolchain/xtensa-lx106-elf}"
PY="${DC1_PY:-C:/Users/Administrator/.workbuddy/binaries/python/versions/3.13.12/python.exe}"

[ -d "$SDK" ] || { echo "找不到 SDK: $SDK"; exit 1; }
[ -d "$TC/bin" ] || { echo "找不到工具链: $TC"; exit 1; }

export IDF_PATH="$SDK"
export PATH="$TC/bin:$PATH"
export PATH="/c/Windows/System32:/c/Windows:$PATH"

# ninja 优先（picotool 自带的那个）
if command -v ninja >/dev/null 2>&1; then
    GEN="Ninja"
else
    GEN="MinGW Makefiles"
fi

cd "$HERE"

if [ "$1" = "clean" ]; then
    rm -rf build
    shift || true
fi

if [ ! -d build ]; then
    echo "== configure (generator: $GEN) =="
    cmake -S . -B build -G "$GEN" \
        -DIDF_PATH="$SDK" \
        -DCMAKE_TOOLCHAIN_FILE="$SDK/tools/cmake/toolchain-esp8266.cmake" \
        -DPYTHON="$PY"
fi

echo "== build =="
cmake --build build -j 8

BIN="build/dc1.bin"
if [ -f "$BIN" ]; then
    echo
    echo "BUILD OK -> $BIN  ($(stat -c%s "$BIN") bytes)"
    echo "烧录: python tools/factory_program.py --c-fw"
else
    echo "构建失败：没找到 $BIN"
    exit 1
fi
