import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// DC1 面板：开关 / 计量 / 电量 / 定时（单次或每周重复）/ 倒计时
Item {
    id: panel

    property var state: ({})
    property var rules: []

    signal toggle(string ch, bool on)
    signal addSched(var rule)
    signal delSched(int id)
    signal addCd(var item)
    signal delCd(int id)
    signal calibrate()

    readonly property color card: "#171a21"
    readonly property color line: "#262b36"
    readonly property color dim: "#8a92a6"
    readonly property color accent: "#c62828"

    readonly property var plugs: [
        { k: "total", n: "总开关" }, { k: "1", n: "插孔 1" },
        { k: "2", n: "插孔 2" }, { k: "3", n: "插孔 3" },
        { k: "logo", n: "LOGO 灯" }
    ]
    readonly property var chNames: ["总开关", "插孔1", "插孔2", "插孔3"]
    readonly property var chKeys: ["total", "1", "2", "3"]
    readonly property var dayNames: ["一", "二", "三", "四", "五", "六", "日"]

    property var daySel: [true, true, true, true, true, true, true]
    property bool onceMode: true

    function daysMask() {
        var m = 0
        for (var i = 0; i < 7; i++)
            if (daySel[i]) m |= (1 << i)
        return m
    }

    function ruleWhen(r) {
        if (!r.days)
            return qsTr("单次")
        if (r.days === 127)
            return qsTr("每天")
        var s = ""
        for (var i = 0; i < 7; i++)
            if ((r.days >> i) & 1) s += dayNames[i]
        return qsTr("每周 ") + s
    }

    ScrollView {
        anchors.fill: parent
        clip: true
        contentWidth: availableWidth

        ColumnLayout {
            width: panel.width
            spacing: 14

            // ---------------- 开关
            GridLayout {
                Layout.fillWidth: true
                columns: 3
                columnSpacing: 12
                rowSpacing: 12

                Repeater {
                    model: panel.plugs
                    delegate: Rectangle {
                        required property var modelData
                        Layout.fillWidth: true
                        implicitHeight: 76
                        radius: 10
                        color: panel.card
                        border.width: 1
                        border.color: panel.state[modelData.k] ? panel.accent : panel.line

                        RowLayout {
                            anchors.fill: parent
                            anchors.margins: 14
                            Column {
                                Layout.fillWidth: true
                                spacing: 4
                                Text {
                                    text: modelData.n
                                    color: "#e8e8e8"
                                    font.pixelSize: 14
                                }
                                Text {
                                    text: panel.state[modelData.k] ? qsTr("通电") : qsTr("断电")
                                    color: panel.state[modelData.k] ? "#ef9a9a" : panel.dim
                                    font.pixelSize: 11
                                }
                            }
                            Button {
                                text: panel.state[modelData.k] ? "ON" : "OFF"
                                highlighted: panel.state[modelData.k]
                                Layout.preferredWidth: 68
                                onClicked: panel.toggle(modelData.k, !panel.state[modelData.k])
                            }
                        }
                    }
                }
            }

            // ---------------- 计量 + 电量
            RowLayout {
                Layout.fillWidth: true
                spacing: 12

                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: 92
                    radius: 10
                    color: panel.card
                    border.width: 1
                    border.color: panel.line

                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 12
                        RowLayout {
                            Layout.fillWidth: true
                            Text { text: qsTr("实时计量"); color: panel.dim; font.pixelSize: 11 }
                            Item { Layout.fillWidth: true }
                            Button {
                                text: qsTr("标定…")
                                implicitHeight: 24
                                font.pixelSize: 11
                                onClicked: panel.calibrate()
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 18
                            Repeater {
                                model: [
                                    { t: "电压", u: "V", v: (panel.state.meter || {}).voltage },
                                    { t: "电流", u: "A", v: (panel.state.meter || {}).current },
                                    { t: "功率", u: "W", v: (panel.state.meter || {}).power }
                                ]
                                delegate: Column {
                                    required property var modelData
                                    Text {
                                        text: (modelData.v === undefined ? "--" : modelData.v) + " " + modelData.u
                                        color: "#9cdcfe"
                                        font.pixelSize: 20
                                        font.family: "Consolas"
                                    }
                                    Text { text: modelData.t; color: panel.dim; font.pixelSize: 11 }
                                }
                            }
                        }
                    }
                }

                Rectangle {
                    Layout.preferredWidth: 260
                    implicitHeight: 92
                    radius: 10
                    color: panel.card
                    border.width: 1
                    border.color: panel.line

                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 12
                        Text { text: qsTr("电量"); color: panel.dim; font.pixelSize: 11 }
                        Text {
                            text: ((panel.state.energy || {}).today_kwh || 0).toFixed(3) + " kWh"
                            color: "#ffd54f"
                            font.pixelSize: 20
                            font.family: "Consolas"
                        }
                        Text {
                            text: qsTr("累计 ") + ((panel.state.energy || {}).total_kwh || 0).toFixed(3) + " kWh"
                            color: panel.dim
                            font.pixelSize: 11
                        }
                    }
                }
            }

            // ---------------- 倒计时
            Rectangle {
                Layout.fillWidth: true
                implicitHeight: cdCol.implicitHeight + 24
                radius: 10
                color: panel.card
                border.width: 1
                border.color: panel.line

                ColumnLayout {
                    id: cdCol
                    anchors.fill: parent
                    anchors.margins: 12
                    spacing: 10

                    Text { text: qsTr("倒计时"); color: "#e8e8e8"; font.bold: true }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        ComboBox { id: cdCh; model: panel.chNames; Layout.preferredWidth: 110 }
                        ComboBox { id: cdAct; model: [qsTr("到时关闭"), qsTr("到时打开")]; Layout.preferredWidth: 110 }
                        SpinBox {
                            id: cdMin
                            from: 1; to: 1440; value: 30
                            editable: true
                            Layout.preferredWidth: 130
                        }
                        Text { text: qsTr("分钟"); color: panel.dim }
                        Item { Layout.fillWidth: true }
                        Button {
                            text: qsTr("开始")
                            onClicked: panel.addCd({
                                ch: panel.chKeys[cdCh.currentIndex],
                                on: cdAct.currentIndex === 1,
                                min: cdMin.value
                            })
                        }
                    }

                    Repeater {
                        model: panel.state.cd || []
                        delegate: RowLayout {
                            required property var modelData
                            Layout.fillWidth: true
                            Text {
                                Layout.fillWidth: true
                                color: "#dddddd"
                                text: qsTr("#") + modelData.id + "  " +
                                      (panel.chNames[Math.max(0, panel.chKeys.indexOf(String(modelData.ch)))] || modelData.ch) +
                                      "  " + (modelData.on ? qsTr("打开") : qsTr("关闭")) +
                                      "  " + Math.ceil(modelData.left) + qsTr(" 秒后")
                            }
                            Button {
                                text: qsTr("删除")
                                onClicked: panel.delCd(modelData.id)
                            }
                        }
                    }
                }
            }

            // ---------------- 定时
            Rectangle {
                Layout.fillWidth: true
                implicitHeight: schCol.implicitHeight + 24
                radius: 10
                color: panel.card
                border.width: 1
                border.color: panel.line

                ColumnLayout {
                    id: schCol
                    anchors.fill: parent
                    anchors.margins: 12
                    spacing: 10

                    Text { text: qsTr("定时"); color: "#e8e8e8"; font.bold: true }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        ComboBox { id: schCh; model: panel.chNames; Layout.preferredWidth: 110 }
                        ComboBox { id: schAct; model: [qsTr("打开"), qsTr("关闭")]; Layout.preferredWidth: 100 }

                        TextField {
                            id: schTime
                            text: "07:30"
                            Layout.preferredWidth: 90
                            horizontalAlignment: TextInput.AlignHCenter
                            validator: RegularExpressionValidator { regularExpression: /^([01]?\d|2[0-3]):[0-5]\d$/ }
                            font.family: "Consolas"
                        }

                        RadioButton {
                            id: rbOnce
                            text: qsTr("单次")
                            spacing: 8
                            leftPadding: 0
                            checked: true
                            onCheckedChanged: panel.onceMode = checked
                        }
                        RadioButton {
                            id: rbWeek
                            text: qsTr("每周重复")
                            spacing: 8
                            leftPadding: 0
                            onCheckedChanged: if (checked) panel.onceMode = false
                        }

                        Item { Layout.fillWidth: true }
                        Button {
                            text: qsTr("添加")
                            enabled: schTime.acceptableInput
                            onClicked: panel.addSched({
                                ch: panel.chKeys[schCh.currentIndex],
                                on: schAct.currentIndex === 0,
                                at: schTime.text,
                                once: panel.onceMode,
                                days: String(panel.daysMask())
                            })
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 6
                        visible: !panel.onceMode
                        Text { text: qsTr("重复日"); color: panel.dim; font.pixelSize: 11 }
                        Repeater {
                            model: 7
                            delegate: Button {
                                required property int index
                                text: panel.dayNames[index]
                                checkable: true
                                checked: panel.daySel[index]
                                Layout.preferredWidth: 42
                                onClicked: {
                                    var s = panel.daySel.slice()
                                    s[index] = checked
                                    panel.daySel = s
                                }
                            }
                        }
                        Item { Layout.fillWidth: true }
                    }

                    Text {
                        text: panel.onceMode ? qsTr("单次：到点触发一次后自动删除")
                                             : qsTr("每周：仅在勾选的星期触发")
                        color: panel.dim
                        font.pixelSize: 11
                    }

                    Repeater {
                        model: panel.rules
                        delegate: RowLayout {
                            required property var modelData
                            Layout.fillWidth: true
                            Text {
                                Layout.fillWidth: true
                                color: "#dddddd"
                                text: qsTr("#") + modelData.id + "  " +
                                      (panel.chNames[Math.max(0, panel.chKeys.indexOf(String(modelData.ch)))] || modelData.ch) +
                                      "  " + (modelData.on ? qsTr("打开") : qsTr("关闭")) +
                                      "  " + ("0" + modelData.hh).slice(-2) + ":" + ("0" + modelData.mm).slice(-2) +
                                      "  " + panel.ruleWhen(modelData) +
                                      (modelData.en ? "" : qsTr("（已停用）"))
                            }
                            Button {
                                text: qsTr("删除")
                                onClicked: panel.delSched(modelData.id)
                            }
                        }
                    }

                    Text {
                        visible: panel.rules.length === 0
                        text: qsTr("暂无定时规则")
                        color: panel.dim
                        font.pixelSize: 11
                    }
                }
            }

            Item { Layout.preferredHeight: 8 }
        }
    }
}
