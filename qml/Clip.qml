import QtQuick
import "Skins.js" as Skins

// One line of text. If it does not fit it is elided; while the pointer is over it, it drifts back and
// forth to show the rest (an animation that exists only under the pointer, so a resting card has none).
Item {
    id: clip
    property string text: ""
    property color color: "white"
    property string family: "sans-serif"
    property real pixelSize: 13
    property int weight: Font.Normal
    property int align: Text.AlignLeft
    property real letterSpacing: 0
    property alias hovered: hover.hovered
    readonly property real over: Math.max(0, full.implicitWidth - width)
    implicitHeight: full.implicitHeight
    clip: true

    HoverHandler { id: hover }
    Text {
        id: full
        visible: false
        text: clip.text; font.pixelSize: Math.round(clip.pixelSize); font.weight: clip.weight; font.letterSpacing: clip.letterSpacing
        font.family: clip.family
    }
    Text {
        id: label
        text: clip.text
        color: clip.color
        font: full.font
        elide: clip.over > 1 && !clip.hovered ? Text.ElideRight : Text.ElideNone
        width: clip.hovered && clip.over > 1 ? implicitWidth : clip.width
        horizontalAlignment: clip.over > 1 ? Text.AlignLeft : clip.align
    }
    SequentialAnimation {
        running: clip.hovered && clip.over > 1
        loops: Animation.Infinite
        onStopped: label.x = 0
        PauseAnimation { duration: 600 }
        NumberAnimation { target: label; property: "x"; to: -(clip.over + 12); duration: 2500 + (clip.over + 12) * 45; easing.type: Easing.InOutQuad }
        PauseAnimation { duration: 600 }
        NumberAnimation { target: label; property: "x"; to: 0; duration: 2500 + (clip.over + 12) * 45; easing.type: Easing.InOutQuad }
    }
}
