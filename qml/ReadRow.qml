import QtQuick
import "Skins.js" as Skins
import "Fmt.js" as F

// One readout: a label on the left, a value with a small unit on the right, a hairline under it.
// With `meter`, a thin bar under the value shows `frac`; it eases to each new fraction.
Item {
    id: row
    property var sk
    property var acc
    property string label
    property string value: "—"
    property string unit: ""
    property bool meter: false
    property real frac: 0
    property bool last: false
    implicitHeight: 14 + Math.max(lbl.implicitHeight, val.implicitHeight) + (meter ? 8 : 0)

    Text {
        id: lbl
        y: 7; text: row.label; font.pixelSize: 12; font.family: row.sk ? row.sk.ui : ""
        color: row.sk ? Skins.qt(row.sk.ink2) : "white"
    }
    Row {
        id: val
        y: 7; anchors.right: parent.right; layoutDirection: Qt.LeftToRight; spacing: 3
        Text {
            text: row.value; font.pixelSize: 14; font.family: row.sk ? row.sk.num : ""
            color: row.sk ? Skins.qt(row.sk.ink) : "white"
        }
        Text {
            visible: row.unit !== ""; anchors.baseline: parent.children[0].baseline
            text: row.unit; font.pixelSize: 11; font.family: row.sk ? row.sk.ui : ""
            color: row.sk ? Skins.qt(row.sk.ink3) : "white"
        }
    }
    Rectangle {
        visible: row.meter
        y: parent.height - 8 - 1 + 0; anchors.left: parent.left; anchors.right: parent.right; height: 3; radius: 1.5
        color: row.sk ? Skins.qt([row.sk.fg[0], row.sk.fg[1], row.sk.fg[2], .08]) : "gray"
        Rectangle {
            height: parent.height; radius: 1.5; width: parent.width * F.clamp(row.frac)
            color: row.acc ? Skins.qt(row.acc) : "white"
            Behavior on width { NumberAnimation { duration: 450; easing.type: Easing.OutCubic } }
        }
    }
    Rectangle {
        visible: !row.last
        anchors.bottom: parent.bottom; width: parent.width; height: 1
        color: row.sk ? Skins.qt(row.sk.hair) : "transparent"
    }
}
