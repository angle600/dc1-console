import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// 未知设备类型的兜底界面：只显示原始状态。
// 新增设备类型时，在 Main.qml 的 sourceComponent 里加映射 + 新建对应 Panel 即可。
Item {
    id: generic

    property var state: ({})

    ColumnLayout {
        anchors.fill: parent
        spacing: 10

        Text {
            text: qsTr("该设备类型暂未提供专用界面，以下为原始状态（feature 驱动的方式待扩展）")
            color: "#8a92a6"
            wrapMode: Text.Wrap
            Layout.fillWidth: true
            font.pixelSize: 12
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            radius: 10
            color: "#171a21"
            border.width: 1
            border.color: "#262b36"

            ScrollView {
                anchors.fill: parent
                anchors.margins: 10
                TextArea {
                    readOnly: true
                    text: JSON.stringify(generic.state, null, 2)
                    color: "#9cdcfe"
                    font.family: "Consolas"
                    font.pixelSize: 12
                    selectByMouse: true
                    wrapMode: TextArea.WrapAnywhere
                }
            }
        }
    }
}
