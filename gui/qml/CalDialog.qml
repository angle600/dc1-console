import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// 单点标定：把外部电表的读数写进设备（设备用 raw 周期反算系数）
Dialog {
    id: dlg
    title: qsTr("电量标定")
    modal: true
    anchors.centerIn: parent
    width: 480
    closePolicy: Popup.CloseOnEscape

    property var meter: ({})
    signal apply(string kind, real value, bool hardware)

    readonly property var kinds: [
        { k: "power", t: qsTr("功率 (W)"), v: meter.power },
        { k: "voltage", t: qsTr("电压 (V)"), v: meter.voltage },
        { k: "current", t: qsTr("电流 (A)"), v: meter.current }
    ]

    ColumnLayout {
        anchors.fill: parent
        spacing: 12

        Text {
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            color: "#8a92a6"
            font.pixelSize: 12
            text: qsTr("设备当前读数：V=%1  A=%2  W=%3\n" +
                       "填外部电表测到的同一物理量。默认按原厂标定只求 V1R/V2R；" +
                       "取消勾选则改 gain（两者等价，前者更贴近手册语义）。")
                  .arg(meter.voltage === undefined ? "--" : meter.voltage)
                  .arg(meter.current === undefined ? "--" : meter.current)
                  .arg(meter.power === undefined ? "--" : meter.power)
        }

        GridLayout {
            Layout.fillWidth: true
            columns: 2
            columnSpacing: 10
            rowSpacing: 8

            Text { text: qsTr("参量"); color: "#cccccc" }
            ComboBox {
                id: kind
                Layout.fillWidth: true
                model: dlg.kinds.map(k => k.t)
            }

            Text { text: qsTr("电表读数"); color: "#cccccc" }
            TextField {
                id: real
                Layout.fillWidth: true
                placeholderText: qsTr("例如 40")
                validator: DoubleValidator { bottom: 0.001; top: 10000; decimals: 3 }
                font.family: "Consolas"
            }
        }

        CheckBox {
            id: hw
            text: qsTr("按原厂标定：只反求硬件常量 V1R/V2R（芯片出厂系数不动）")
            checked: true
            font.pixelSize: 11
        }

        Rectangle {
            Layout.fillWidth: true
            implicitHeight: tip.implicitHeight + 16
            radius: 8
            color: "#1b1f27"
            border.width: 1
            border.color: "#2a3040"
            Text {
                id: tip
                anchors.fill: parent
                anchors.margins: 8
                wrapMode: Text.Wrap
                color: "#b9a26a"
                font.pixelSize: 11
                text: qsTr("负载建议：纯阻性且稳定 —— 白炽灯、电热水壶、电暖器最好；" +
                           "电烙铁有温控通断、功率会周期性波动（40W 时约 0.18A，接近量程下限），" +
                           "若必须用烙铁，让它恒温 5 分钟、取 60 秒平均值再填。")
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Item { Layout.fillWidth: true }
            Button { text: qsTr("取消"); onClicked: dlg.close() }
            Button {
                text: qsTr("写入系数")
                highlighted: true
                enabled: real.acceptableInput
                onClicked: {
                    dlg.apply(dlg.kinds[kind.currentIndex].k, Number(real.text), hw.checked)
                    dlg.close()
                }
            }
        }
    }
}
