import QtQuick
import "Skins.js" as Skins
import "Fmt.js" as F

// Per-thread bars with peak-hold markers, like an audio level meter. The bars are plain items with
// Behaviors: each glides from one sample to the next over the sample interval and then rests. Nothing
// is redrawn by script; between samples the scene graph moves rectangles, and only while they move.
Item {
    id: cores
    property var sk
    property var acc
    property var values: []        // 0..1 per thread
    property var peaks: []
    property int interval: 500     // ms between samples
    readonly property int n: values.length
    readonly property real gap: n > 24 ? 2 : 3
    readonly property real bw: n > 0 ? (width - gap * (n - 1)) / n : 0
    readonly property real barH: height - 14
    readonly property int every: n <= 16 ? 1 : n <= 32 ? 4 : 8

    Repeater {
        model: cores.n
        Item {
            x: index * (cores.bw + cores.gap); width: cores.bw; height: cores.height
            Rectangle {
                width: parent.width; height: cores.barH; radius: Math.min(2, cores.bw / 2)
                color: cores.sk ? Skins.qt([cores.sk.fg[0], cores.sk.fg[1], cores.sk.fg[2], .05]) : "transparent"
            }
            Item {
                id: fill
                readonly property real v: F.clamp(cores.values[index] || 0)
                width: parent.width; clip: true
                height: Math.max(1.5, v * cores.barH); y: cores.barH - height
                opacity: .35 + .65 * v
                Behavior on height { NumberAnimation { duration: cores.interval * .9; easing.type: Easing.InOutQuad } }
                Behavior on opacity { NumberAnimation { duration: cores.interval * .9 } }
                Rectangle {          // the gradient spans the full bar height, so short bars show only its dim foot
                    width: parent.width; height: cores.barH; y: fill.height - cores.barH
                    gradient: Gradient {
                        GradientStop { position: 0; color: cores.acc ? Skins.qt(cores.acc, 1) : "white" }
                        GradientStop { position: 1; color: cores.acc ? Skins.qt(cores.acc, .3) : "white" }
                    }
                }
            }
            Rectangle {              // peak hold
                width: parent.width; height: 1.5
                y: Math.max(0, cores.barH - F.clamp(cores.peaks[index] || 0) * cores.barH - 1.5)
                color: cores.sk ? Skins.qt([cores.sk.fg[0], cores.sk.fg[1], cores.sk.fg[2], .8]) : "white"
                Behavior on y { NumberAnimation { duration: cores.interval * .9 } }
            }
            Text {
                visible: index % cores.every === 0
                text: index; font.pixelSize: 9; font.family: cores.sk ? cores.sk.num : ""
                color: cores.sk ? Skins.qt(cores.sk.ink3) : "white"
                x: (parent.width - width) / 2; y: cores.height - height + 1
            }
        }
    }
}
