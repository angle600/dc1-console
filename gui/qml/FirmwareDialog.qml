import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs

Dialog {
    id: dlg
    title: qsTr("更新固件（WiFi · 覆盖源码）")
    modal: true
    anchors.centerIn: parent
    width: 560
    closePolicy: Popup.CloseOnEscape

    signal start(string dir)

    property string dir: ""

    ColumnLayout {
        anchors.fill: parent
        spacing: 12

        Text {
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            color: "#8a92a6"
            font.pixelSize: 12
            text: qsTr("通过 HTTP PUT /api/file 逐个覆盖 .py 文件（MicroPython 1MB 无法整片 OTA）。\n" +
                       "传完需重启设备生效：命令行 `python cli/dc1ctl.py --ip <ip> time` 不会重启，" +
                       "请断电重插或在设备页下发重启。")
        }

        RowLayout {
            Layout.fillWidth: true
            TextField {
                id: pathField
                Layout.fillWidth: true
                text: dlg.dir
                placeholderText: qsTr("固件源码目录（含 main.py）")
            }
            Button {
                text: qsTr("浏览…")
                onClicked: folderDlg.open()
            }
        }

        Text {
            Layout.fillWidth: true
            color: "#6f7787"
            font.pixelSize: 11
            wrapMode: Text.Wrap
            text: qsTr("将上传：main.py / store.py / cat9554.py / cse7766.py / webui.py（存在 config.json 时也一并上传）")
        }

        RowLayout {
            Layout.fillWidth: true
            Item { Layout.fillWidth: true }
            Button { text: qsTr("取消"); onClicked: dlg.close() }
            Button {
                text: qsTr("开始更新")
                highlighted: true
                onClicked: {
                    dlg.start(pathField.text)
                    dlg.close()
                }
            }
        }
    }

    FolderDialog {
        id: folderDlg
        title: qsTr("选择固件源码目录")
        onAccepted: pathField.text = selectedFolder.toString().replace("file:///", "")
    }
}
