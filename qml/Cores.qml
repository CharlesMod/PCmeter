import QtQuick
import "Skins.js" as Skins
import "Fmt.js" as F

// Per-thread bars with peak-hold markers, like an audio level meter. Each sample moves every bar with
// animators on the render thread: the fill is a gradient (fixed to the full bar) seen through a clip
// whose top edge slides, so the gradient keeps its place while the fill grows. No frame is drawn by JS.
Item {
    id: cores
    property var sk
    property var acc
    property var values: []        // 0..1 per thread
    property var peaks: []
    property int glide: 450        // ms to move to the new values
    readonly property int n: values.length
    readonly property real gap: n > 24 ? 2 : 3
    readonly property real bw: n > 0 ? (width - gap * (n - 1)) / n : 0
    readonly property real barH: height - 14
    readonly property int every: n <= 16 ? 1 : n <= 32 ? 4 : 8

    Repeater {
        model: cores.n
        Item {
            id: bar
            x: index * (cores.bw + cores.gap); width: cores.bw; height: cores.height
            readonly property real tv: Math.max(1.5 / cores.barH, F.clamp(cores.values[index] || 0))
            readonly property real tp: Math.max(0, cores.barH - F.clamp(cores.peaks[index] || 0) * cores.barH - 1.5)
            property real lastV: -1
            property real lastP: -1
            function yOf(v) { return cores.barH * (1 - v) }
            function opOf(v) { return .35 + .65 * v }
            Component.onCompleted: { mover.y = yOf(tv); inner.y = -yOf(tv); mover.opacity = opOf(tv); lastV = tv; peak.y = tp; lastP = tp }
            onTvChanged: {
                if (lastV < 0 || !cores.visible) { ay.stop(); ac.stop(); ao.stop(); mover.y = yOf(tv); inner.y = -yOf(tv); mover.opacity = opOf(tv) }
                else {
                    ay.stop(); ac.stop(); ao.stop()
                    ay.from = yOf(lastV); ay.to = yOf(tv); ac.from = -yOf(lastV); ac.to = -yOf(tv)
                    ao.from = opOf(lastV); ao.to = opOf(tv)
                    ay.restart(); ac.restart(); ao.restart()
                }
                lastV = tv
            }
            onTpChanged: {
                if (lastP < 0 || !cores.visible) { ap.stop(); peak.y = tp }
                else { ap.stop(); ap.from = lastP; ap.to = tp; ap.restart() }
                lastP = tp
            }
            Rectangle {
                width: parent.width; height: cores.barH; radius: Math.min(2, cores.bw / 2)
                color: cores.sk ? Skins.qt([cores.sk.fg[0], cores.sk.fg[1], cores.sk.fg[2], .05]) : "transparent"
            }
            Item {                    // the bar's fixed frame
                width: parent.width; height: cores.barH; clip: true
                Item {                // its top edge slides down as the value falls
                    id: mover
                    width: parent.width; height: cores.barH; clip: true
                    Rectangle {
                        id: inner
                        width: parent.width; height: cores.barH
                        gradient: Gradient {
                            GradientStop { position: 0; color: cores.acc ? Skins.qt(cores.acc, 1) : "white" }
                            GradientStop { position: 1; color: cores.acc ? Skins.qt(cores.acc, .3) : "white" }
                        }
                    }
                }
                YAnimator { id: ay; target: mover; duration: cores.glide; easing.type: Easing.Linear }
                YAnimator { id: ac; target: inner; duration: cores.glide; easing.type: Easing.Linear }
                OpacityAnimator { id: ao; target: mover; duration: cores.glide; easing.type: Easing.Linear }
            }
            Rectangle {              // peak hold
                id: peak
                width: parent.width; height: 1.5
                color: cores.sk ? Skins.qt([cores.sk.fg[0], cores.sk.fg[1], cores.sk.fg[2], .8]) : "white"
                YAnimator { id: ap; target: peak; duration: cores.glide; easing.type: Easing.Linear }
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
