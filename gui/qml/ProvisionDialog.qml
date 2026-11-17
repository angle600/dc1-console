import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: dlg
    title: qsTr("配网 · 发送 WiFi 信息")
    modal: true
    anchors.centerIn: parent
    width: 460
    closePolicy: Popup.CloseOnEscape

    signal provision(string ssid, string pass, string name)

    ColumnLayout {
        anchors.fill: parent
        spacing: 12

        Text {
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            color: "#ffb74d"
            font.pixelSize: 12
            text: qsTr("设备「上电后 30 秒内」才接受配网（UDP 54321 广播）。\n" +
                       "• 设备已入网：直接配，最省事\n" +
                       "• 设备没配过网：会开热点 DC1-Setup，先让本机连这个热点再配")
        }

        GridLayout {
            Layout.fillWidth: true
            columns: 2
            columnSpacing: 10
            rowSpacing: 8

            Text { text: qsTr("SSID"); color: "#cccccc" }
            TextField {
                id: ssid
                Layout.fillWidth: true
                placeholderText: qsTr("2.4GHz 网络名")
            }

            Text { text: qsTr("密码"); color: "#cccccc" }
            TextField {
                id: pass
                Layout.fillWidth: true
                echoMode: TextInput.Password
                placeholderText: qsTr("WiFi 密码")
            }

            Text { text: qsTr("设备名"); color: "#cccccc" }
            TextField {
                id: name
                Layout.fillWidth: true
                placeholderText: qsTr("可选，如：客厅排插")
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Item { Layout.fillWidth: true }
            Button { text: qsTr("取消"); onClicked: dlg.close() }
            Button {
                text: qsTr("发送配网")
                highlighted: true
                enabled: ssid.text.length > 0
                onClicked: {
                    dlg.provision(ssid.text, pass.text, name.text)
                    dlg.close()
                }
            }
        }
    }
}
