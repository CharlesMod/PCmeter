import QtQuick
import "Skins.js" as Skins
import "Fmt.js" as F

// Sparkline with area fill, faint midline and a lit endpoint. The body is painted once per sample (and
// when the skin or size changes) into a canvas one step wider than the view. Render-thread animators do
// the rest over the sample interval: an XAnimator slides the canvas left by one step, a reveal window
// fixed at the head shows the line only up to the head, and the head dot's YAnimator runs from the last
// point's height to the new one's. The new point is drawn in as the scroll brings it, and the dot rides
// the line. The GUI thread does nothing between samples.
Item {
    id: sp
    property var sk
    property var acc
    property var series: []
    property real maxValue: 100
    property int points: 120
    property int rev: 0               // bumped by the card after it changes `series`
    property int glide: 500           // ms per step
    readonly property real pad: 3
    readonly property real step: Math.max(.5, (width - pad * 2) / (points - 1))
    property real yFrom: 0
    property real yTo: 0
    property bool ready: false
    function yOf(v) { return height - 2.5 - F.clamp(v / maxValue) * (height - 6) }
    onRevChanged: cv.requestPaint()
    onSkChanged: cv.requestPaint()
    onAccChanged: cv.requestPaint()
    onMaxValueChanged: cv.requestPaint()
    onWidthChanged: cv.requestPaint()
    onHeightChanged: cv.requestPaint()

    XAnimator { id: slide; target: cv; from: 0; to: -sp.step; duration: sp.glide; easing.type: Easing.Linear }
    YAnimator { id: rise; target: head; duration: sp.glide; easing.type: Easing.Linear }

    Item {                              // reveal window: the line shows up to the head (x = width - pad)
        x: 0; y: 0; width: sp.width - sp.pad; height: sp.height; clip: true
        Canvas {
            id: cv
            width: sp.width + sp.step; height: sp.height
            renderTarget: Canvas.Image
            renderStrategy: Canvas.Immediate
            onPainted: {
                var m = sp.series.length
                sp.ready = m >= 2 && !!sp.sk
                if (!sp.ready) return
                sp.yFrom = sp.yOf(sp.series[m - 2]); sp.yTo = sp.yOf(sp.series[m - 1])
                slide.stop(); slide.restart()
                rise.stop(); rise.from = sp.yFrom; rise.to = sp.yTo; rise.restart()
            }
            onPaint: {
                var w = width, h = height, sk = sp.sk, acc = sp.acc, series = sp.series, maxValue = sp.maxValue, points = sp.points
                if (w < 2 || h < 2 || !sk) return
                var ctx = getContext("2d"); ctx.reset()
                var a = function (al) { return Skins.css(acc, al) }
                ctx.strokeStyle = Skins.css([sk.fg[0], sk.fg[1], sk.fg[2], .06]); ctx.lineWidth = 1
                ctx.beginPath(); ctx.moveTo(0, h / 2 + .5); ctx.lineTo(w, h / 2 + .5); ctx.stroke()
                var m = series.length
                if (m < 2) return
                var pad = 3, step = sp.step, right = w - pad
                var y = function (v) { return h - 2.5 - F.clamp(v / maxValue) * (h - 6) }
                var pts = []
                for (var j = 0; j < m; j++) pts.push([right - (m - 1 - j) * step, y(series[j])])
                function curve() {      // Catmull-Rom through every sample
                    ctx.moveTo(pts[0][0], pts[0][1])
                    for (var i = 0; i < pts.length - 1; i++) {
                        var p0 = pts[i - 1] || pts[i], p1 = pts[i], p2 = pts[i + 1], p3 = pts[i + 2] || p2
                        ctx.bezierCurveTo(p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6,
                                          p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6, p2[0], p2[1])
                    }
                }
                var last = pts[pts.length - 1]
                ctx.beginPath(); curve(); ctx.lineTo(last[0], h); ctx.lineTo(pts[0][0], h); ctx.closePath()
                var g = ctx.createLinearGradient(0, 0, 0, h); g.addColorStop(0, a(.30)); g.addColorStop(1, a(0))
                ctx.fillStyle = g; ctx.fill()
                ctx.beginPath(); curve()
                ctx.strokeStyle = a(.95); ctx.lineWidth = 1.25; ctx.lineJoin = "round"; ctx.stroke()
            }
        }
    }
    Item {                              // the head: a glow and a core on the line's moving end
        id: head
        x: sp.width - sp.pad; y: sp.yFrom; visible: sp.ready
        Rectangle { property real r: sp.sk ? 2.4 + 3 * sp.sk.glow : 2.4; x: -r; y: -r; width: 2 * r; height: 2 * r; radius: r
                    color: sp.sk ? Skins.qt(sp.acc, .25) : "transparent" }
        Rectangle { x: -2.4; y: -2.4; width: 4.8; height: 4.8; radius: 2.4; color: sp.sk ? Skins.qt(sp.sk.needle) : "white" }
    }
}
