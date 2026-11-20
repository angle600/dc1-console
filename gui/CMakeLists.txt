cmake_minimum_required(VERSION 3.21)

project(Dc1Console VERSION 1.1 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)

# 源码树位置：给 Cli 找 cli/dc1ctl.py 兜底用
add_compile_definitions(DC1_SOURCE_DIR="${CMAKE_CURRENT_SOURCE_DIR}/..")

find_package(Qt6 6.5 REQUIRED COMPONENTS Quick QuickControls2 Gui)

qt_standard_project_setup()

qt_add_executable(dc1console
    main.cpp
    clirunner.cpp
    clirunner.h
)

# 资源路径即 qrc:/qml/Main.qml（prefix "/" + 文件相对路径 qml/...）
qt_add_resources(dc1console "qml"
    PREFIX "/"
    FILES
        qml/Main.qml
        qml/StripPanel.qml
        qml/GenericPanel.qml
        qml/ProvisionDialog.qml
        qml/FirmwareDialog.qml
        qml/CalDialog.qml
)

target_link_libraries(dc1console PRIVATE Qt6::Quick Qt6::QuickControls2 Qt6::Gui)

# Windows 下不弹控制台
if (WIN32)
    set_target_properties(dc1console PROPERTIES WIN32_EXECUTABLE TRUE)
endif()

install(TARGETS dc1console
    BUNDLE  DESTINATION .
    RUNTIME DESTINATION bin
)

# windeployqt 提示（构建后手动跑一次即可分发）
message(STATUS "打包: windeployqt --qmldir ${CMAKE_CURRENT_SOURCE_DIR}/qml <build>/dc1console.exe")
