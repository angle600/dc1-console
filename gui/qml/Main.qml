import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs

ApplicationWindow {
    id: root
    width: 1200
    height: 780
    visible: true
    title: qsTr("DC1 Console · 局域网智能排插上位机")
    color: "#0f1115"
    font.family: "Microsoft YaHei"
    font.pixelSize: 13

    readonly property color accent: "#c62828"
    readonly property color card: "#171a21"
    readonly property color line: "#262b36"
    readonly property color dim: "#8a92a6"

    property var devices: []
    property var rules: []
    property var st: ({})
    property string curIp: ""
    property string curType: ""
    property string curName: ""
    property string status: qsTr("就绪 · 点「发现设备」")
    property string pending: ""

    function call(tag, args) {
        if (Cli.busy)
            return
        pending = tag
        Cli.run(args)
    }

    function selectDevice(d) {
        curIp = d.ip || ""
        curType = d.type || ""
        curName = d.name || d.id || ""
        st = ({})
        rules = []
        status = qsTr("已选择 ") + curName + " @ " + curIp
        refresh()
    }

    function refresh() {
        if (curIp === "")
            return
        call("state", ["state", "--ip", curIp, "--json"])
    }

    Connections {
        target: Cli
        function onFinished(ok, json) {
            var data = null
            try { data = JSON.parse(json) } catch (e) { data = null }

            if (!ok && (!data || data.ok === undefined || data.ok === 0)) {
                status = qsTr("失败：") + (json.length > 160 ? json.slice(0, 160) : json)
            }

            if (pending === "discover") {
                if (Array.isArray(data)) {
                    devices = data
                    status = qsTr("发现 ") + data.length + " 台设备"
                    if (data.length === 1)
                        selectDevice(data[0])
                } else {
                    devices = []
                    status = qsTr("未发现设备：确认与设备同网段，或连上其热点 DC1-Setup")
                }
            } else if (pending === "state") {
                if (data && data.type !== undefined) {
                    st = data
                    curType = data.type
                    status = qsTr("在线 · ") + (data.ip || curIp) +
                             " · " + (data.ap ? qsTr("AP 模式") : (data.rssi + " dBm"))
                }
            } else if (pending === "rules") {
                if (Array.isArray(data))
                    rules = data
            } else if (pending === "switch") {
                if (data && data.type !== undefined)
                    st = data
                status = qsTr("已下发开关指令")
                rulesRefreshSoon.restart()
            } else if (pending === "sched" || pending === "sched-del") {
                status = qsTr("定时已更新")
                call("rules", ["sched", "list", "--ip", curIp, "--json"])
            } else if (pending === "cd" || pending === "cd-del") {
                status = qsTr("倒计时已更新")
                refresh()
            } else if (pending === "reboot") {
                status = qsTr("重启指令已发送")
            } else if (pending === "time") {
                status = qsTr("设备时间已校准")
            } else if (pending === "prov") {
                status = (data && data.ok) ? qsTr("配网成功，设备重启中…")
                                           : qsTr("配网失败：") + (data ? data.err : json)
            } else if (pending === "fw") {
                status = (data && data.ok) ? qsTr("固件已更新（重启后生效）")
                                           : qsTr("固件更新失败：") + json
            }
            pending = ""
        }
    }

    Timer {
        id: rulesRefreshSoon
        interval: 600
        onTriggered: if (curIp !== "") call("rules", ["sched", "list", "--ip", curIp, "--json"])
    }

    Timer {
        interval: 2500
        repeat: true
        running: curIp !== ""
        onTriggered: {
            if (!Cli.busy && pending === "")
                call("state", ["state", "--ip", curIp, "--json"])
        }
    }

    Component.onCompleted: {
        if (DemoMode) {
            devices = [{ name: "客厅排插", type: "DC1", id: "dc1-a1b2",
                         ip: "192.168.1.9", fw: "1.1.0", prov: 0 },
                       { name: "卧室排插", type: "DC1", id: "dc1-c3d4",
                         ip: "192.168.1.10", fw: "1.1.0", prov: 1 }]
            curIp = "192.168.1.9"
            curType = "DC1"
            curName = "客厅排插"
            status = qsTr("演示模式 · 未连接真实设备")
            st = { type: "DC1", fw: "1.1.0", ip: "192.168.1.9", rssi: -58,
                   total: true, "1": true, "2": false, "3": true, logo: true,
                   ap: 0, prov: 0,
                   meter: { voltage: 220.4, current: 0.271, power: 59.8 },
                   energy: { total_kwh: 12.483, today_kwh: 0.912 },
                   cd: [{ id: 2, ch: "1", on: false, left: 843 }],
                   sched: 2 }
            rules = [{ id: 1, ch: "total", on: true, hh: 7, mm: 30, days: 31, en: 1 },
                     { id: 2, ch: "1", on: false, hh: 23, mm: 0, days: 0, en: 1 }]
        }
    }

    // ------------------------------------------------------------ 布局
    RowLayout {
        anchors.fill: parent
        spacing: 0

        // ---------------- 侧栏
        Rectangle {
            Layout.preferredWidth: 300
            Layout.fillHeight: true
            color: "#12151b"

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 14
                spacing: 10

                Text {
                    text: "DC1 Console"
                    color: "white"
                    font.pixelSize: 20
                    font.bold: true
                }
                Text {
                    text: qsTr("局域网发现 · 配网 · 控制 · OTA")
                    color: root.dim
                    font.pixelSize: 12
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8
                    Button {
                        text: qsTr("发现设备")
                        enabled: !Cli.busy
                        Layout.fillWidth: true
                        onClicked: call("discover", ["discover", "--json"])
                    }
                    Button {
                        text: qsTr("刷新")
                        enabled: curIp !== "" && !Cli.busy
                        onClicked: refresh()
                    }
                }

                Rectangle { Layout.fillWidth: true; height: 1; color: root.line }

                ListView {
                    id: devList
                    Layout.fillWidth: true
                    Layout.preferredHeight: Math.max(120, contentHeight)
                    clip: true
                    spacing: 6
                    model: root.devices

                    delegate: Rectangle {
                        required property var modelData
                        width: devList.width
                        height: 66
                        radius: 8
                        color: (root.curIp === modelData.ip) ? "#232833" : root.card
                        border.width: 1
                        border.color: (root.curIp === modelData.ip) ? root.accent : root.line

                        Column {
                            anchors.fill: parent
                            anchors.margins: 9
                            spacing: 3
                            Row {
                                spacing: 6
                                Text {
                                    text: modelData.name || modelData.id
                                    color: "#eaeaea"
                                    font.bold: true
                                }
                                Rectangle {
                                    width: tagText.width + 12
                                    height: 18
                                    radius: 4
                                    color: "#2a3140"
                                    Text {
                                        id: tagText
                                        anchors.centerIn: parent
                                        text: modelData.type || "?"
                                        color: "#9ecbff"
                                        font.pixelSize: 11
                                    }
                                }
                            }
                            Text {
                                text: (modelData.ip || "") + "   " +
                                      (modelData.id || "") + "   fw " + (modelData.fw || "?")
                                color: root.dim
                                font.pixelSize: 11
                            }
                            Text {
                                text: modelData.prov ? qsTr("可配网（上电 30s 内）") : qsTr("已入网")
                                color: modelData.prov ? "#ffb74d" : "#66bb6a"
                                font.pixelSize: 11
                            }
                        }
                        MouseArea {
                            anchors.fill: parent
                            onClicked: root.selectDevice(modelData)
                        }
                    }
                }

                Item { Layout.fillHeight: true }

                Button {
                    Layout.fillWidth: true
                    text: qsTr("配网（发送 SSID / 密码）")
                    enabled: !Cli.busy
                    onClicked: provDlg.open()
                }
                Button {
                    Layout.fillWidth: true
                    text: qsTr("更新固件")
                    enabled: curIp !== "" && !Cli.busy
                    onClicked: fwDlg.open()
                }
                Button {
                    Layout.fillWidth: true
                    text: qsTr("重启设备")
                    enabled: curIp !== "" && !Cli.busy
                    onClicked: call("reboot", ["reboot", "--ip", curIp, "--json"])
                }
                Button {
                    Layout.fillWidth: true
                    text: qsTr("校准设备时间")
                    enabled: curIp !== "" && !Cli.busy
                    onClicked: call("time", ["time", "--ip", curIp, "--json"])
                }

                Text {
                    Layout.fillWidth: true
                    text: root.status
                    color: root.dim
                    wrapMode: Text.Wrap
                    font.pixelSize: 11
                }
            }
        }

        // ---------------- 主区
        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: root.color

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 18
                spacing: 12

                RowLayout {
                    Layout.fillWidth: true
                    Text {
                        text: root.curIp === "" ? qsTr("未选择设备") : root.curName
                        color: "white"
                        font.pixelSize: 20
                        font.bold: true
                    }
                    Item { Layout.fillWidth: true }
                    Text {
                        text: root.curIp === "" ? "" : (root.curType + " · " + root.curIp)
                        color: root.dim
                    }
                }

                Text {
                    visible: root.curType === ""
                    Layout.fillWidth: true
                    horizontalAlignment: Text.AlignHCenter
                    color: root.dim
                    font.pixelSize: 14
                    text: qsTr("先点左侧「发现设备」（设备需与本机同一网段）")
                }

                Loader {
                    id: pageLoader
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    sourceComponent: root.curType === "" ? null
                                   : root.curType === "DC1" ? dc1Comp : genericComp
                }
            }
        }
    }

    // ---------------- 设备面板
    Component {
        id: dc1Comp
        Dc1Panel {
            state: root.st
            rules: root.rules
            onToggle: function (ch, on) {
                root.call("switch", ["switch", "--ch", ch, "--on", on ? "1" : "0",
                                     "--ip", root.curIp, "--json"])
            }
            onAddSched: function (rule) {
                var args = ["sched", "add", "--ch", rule.ch, "--on", rule.on ? "1" : "0",
                            "--at", rule.at]
                if (rule.once)
                    args.push("--once")
                else
                    args.push("--mask", rule.days)
                args.push("--ip", root.curIp, "--json")
                root.call("sched", args)
            }
            onDelSched: function (id) {
                root.call("sched-del", ["sched", "del", "--id", String(id),
                                        "--ip", root.curIp, "--json"])
            }
            onAddCd: function (item) {
                root.call("cd", ["countdown", "add", "--ch", item.ch,
                                 "--on", item.on ? "1" : "0", "--min", String(item.min),
                                 "--ip", root.curIp, "--json"])
            }
            onDelCd: function (id) {
                root.call("cd-del", ["countdown", "del", "--id", String(id),
                                     "--ip", root.curIp, "--json"])
            }
        }
    }

    Component {
        id: genericComp
        GenericPanel { state: root.st }
    }

    ProvisionDialog {
        id: provDlg
        onProvision: function (ssid, pass, name) {
            var args = ["prov", "--ssid", ssid, "--pass", pass, "--json"]
            if (name !== "")
                args.push("--name", name)
            root.call("prov", args)
        }
    }

    FirmwareDialog {
        id: fwDlg
        onStart: function (dir) {
            root.call("fw", ["fw", "update", "--dir", dir, "--ip", root.curIp, "--json"])
        }
    }
}
