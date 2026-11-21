cmake_minimum_required(VERSION 3.13)

# ---------------------------------------------------------------------------
# pico-sdk 定位顺序：-DPICO_SDK_PATH > 环境变量 > ../.toolchain/pico-sdk
# ---------------------------------------------------------------------------
if(NOT DEFINED PICO_SDK_PATH)
    if(DEFINED ENV{PICO_SDK_PATH})
        set(PICO_SDK_PATH $ENV{PICO_SDK_PATH})
    elseif(EXISTS ${CMAKE_CURRENT_LIST_DIR}/../.toolchain/pico-sdk/pico_sdk_init.cmake)
        get_filename_component(_tc "${CMAKE_CURRENT_LIST_DIR}/../.toolchain" ABSOLUTE)
        set(PICO_SDK_PATH ${_tc}/pico-sdk)
    else()
        message(FATAL_ERROR "找不到 pico-sdk。设置 PICO_SDK_PATH 或跑 build.ps1。")
    endif()
endif()
message(STATUS "PICO_SDK_PATH = ${PICO_SDK_PATH}")

include(${PICO_SDK_PATH}/external/pico_sdk_import.cmake)

project(pico_esp_flasher C CXX ASM)
set(CMAKE_C_STANDARD 11)
set(CMAKE_CXX_STANDARD 17)

pico_sdk_init()

# WITH_DAP=ON   -> 3 路 CDC + CMSIS-DAP v2 (SWD)   => esp-flasher-dap.uf2
# WITH_DAP=OFF  -> 纯 3 路 CDC                      => esp-flasher.uf2
option(WITH_DAP "Enable CMSIS-DAP v2 SWD interface" ON)

set(APP_SRC
    main.c
    usb_descriptors.c
    tusb_config.h
)
if(WITH_DAP)
    list(APPEND APP_SRC cmsis_dap.c)
endif()

add_executable(pico_esp_flasher ${APP_SRC})

if(WITH_DAP)
    target_compile_definitions(pico_esp_flasher PRIVATE WITH_DAP=1)
    set(UF2_NAME "esp-flasher-dap")
else()
    set(UF2_NAME "esp-flasher")
endif()

# 本地 tusb_config.h 必须压过 SDK 自带那份（PRIVATE + BEFORE 双保险）
include_directories(BEFORE ${CMAKE_CURRENT_LIST_DIR})
target_include_directories(pico_esp_flasher PRIVATE ${CMAKE_CURRENT_LIST_DIR})

# 关掉默认 stdio，避免抢占 CDC0
pico_enable_stdio_usb(pico_esp_flasher 0)
pico_enable_stdio_uart(pico_esp_flasher 0)

target_link_libraries(pico_esp_flasher
    pico_stdlib
    hardware_uart
    hardware_gpio
    tinyusb_device
    tinyusb_board
)

# 默认 XIP（flash 直执行）+ 1KB USB 缓冲，够跑 2Mbps。
# 若发现高波特率下偶发掉字节，再打开下一行把代码搬进 RAM（代价：uf2 变大、RAM 吃紧）。
# pico_set_binary_type(pico_esp_flasher copy_to_ram)

# ---------------------------------------------------------------------------
# 不用 pico_add_extra_outputs：它会 FetchContent 拉 picotool 源码，而 picotool
# 需要【宿主】编译器（MSVC/MinGW），交叉编译器编不了。这里自己 objcopy + 转 uf2。
# ---------------------------------------------------------------------------
add_custom_command(TARGET pico_esp_flasher POST_BUILD
    COMMAND ${CMAKE_OBJCOPY} -O binary
            $<TARGET_FILE:pico_esp_flasher>
            $<TARGET_FILE_DIR:pico_esp_flasher>/${UF2_NAME}.bin
)

add_custom_command(TARGET pico_esp_flasher POST_BUILD
    COMMAND python ${CMAKE_CURRENT_LIST_DIR}/mkuf2.py
            $<TARGET_FILE_DIR:pico_esp_flasher>/${UF2_NAME}.bin
            $<TARGET_FILE_DIR:pico_esp_flasher>/${UF2_NAME}.uf2
)
