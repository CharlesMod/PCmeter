import QtQuick
import "Skins.js" as Skins
import "Fmt.js" as F

// Top processes: name, PID, utilisation (with a thin bar) and memory. Rows are replaced when a new list
// arrives (every few seconds), not animated.
Column {
    id: tbl
    property var sk
    property var acc
    property var rows: []
    property string colUtil: "CPU"
    property string colMem: "RAM"
    property string memKey: "mem_mb"
    property string empty: "No process data."
    readonly property real wPid: 62
    readonly property real wUtil: 64
    readonly property real wMem: 78
    readonly property real wName: width - wPid - wUtil - wMem

    Row {
        height: 16
        Repeater {
            model: [["Process", 0], ["PID", 1], [tbl.colUtil, 2], [tbl.colMem, 3]]
            Text {
                width: [tbl.wName, tbl.wPid, tbl.wUtil, tbl.wMem][modelData[1]]; height: 16
                text: modelData[0].toUpperCase(); font.pixelSize: 10; font.weight: Font.DemiBold; font.letterSpacing: 1.1
                font.family: tbl.sk ? tbl.sk.ui : ""; color: tbl.sk ? Skins.qt(tbl.sk.ink3) : "white"
                horizontalAlignment: modelData[1] === 0 ? Text.AlignLeft : Text.AlignRight
            }
        }
    }
    Rectangle { width: parent.width; height: 1; color: tbl.sk ? Skins.qt(tbl.sk.hair) : "transparent" }
    Text {
        visible: tbl.rows.length === 0
        topPadding: 5; bottomPadding: 5
        text: tbl.empty; font.pixelSize: 12; font.family: tbl.sk ? tbl.sk.ui : ""; color: tbl.sk ? Skins.qt(tbl.sk.ink3) : "white"
    }
    Repeater {
        model: tbl.rows
        Item {
            width: tbl.width; height: 27
            readonly property var p: modelData
            Clip {
                x: 0; y: 5; width: tbl.wName - 8; height: 17
                text: p.name; pixelSize: 13; family: tbl.sk ? tbl.sk.ui : ""; color: tbl.sk ? Skins.qt(tbl.sk.ink) : "white"
            }
            Text {
                x: tbl.wName; width: tbl.wPid; y: 5; horizontalAlignment: Text.AlignRight
                text: p.pid; font.pixelSize: 12; font.family: tbl.sk ? tbl.sk.num : ""; color: tbl.sk ? Skins.qt(tbl.sk.ink2) : "white"
            }
            Text {
                x: tbl.wName + tbl.wPid; width: tbl.wUtil; y: 5; horizontalAlignment: Text.AlignRight
                text: F.fmt(p.util) + "%"; font.pixelSize: 12; font.family: tbl.sk ? tbl.sk.num : ""; color: tbl.sk ? Skins.qt(tbl.sk.ink) : "white"
            }
            Rectangle {
                anchors.right: parent.right; anchors.rightMargin: tbl.wMem; y: 23; height: 2; radius: 1
                width: tbl.wUtil * F.clamp(p.util / 100); color: tbl.acc ? Skins.qt(tbl.acc) : "white"
            }
            Text {
                x: tbl.wName + tbl.wPid + tbl.wUtil; width: tbl.wMem; y: 5; horizontalAlignment: Text.AlignRight
                text: F.mem(p[tbl.memKey] || 0); font.pixelSize: 12; font.family: tbl.sk ? tbl.sk.num : ""; color: tbl.sk ? Skins.qt(tbl.sk.ink2) : "white"
            }
            Rectangle {
                visible: index < tbl.rows.length - 1
                anchors.bottom: parent.bottom; width: parent.width; height: 1; color: tbl.sk ? Skins.qt(tbl.sk.hair) : "transparent"
            }
        }
    }
}
