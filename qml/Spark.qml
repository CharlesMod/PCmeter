import QtQuick
import "Skins.js" as Skins
import "Fmt.js" as F

// Sparkline with area fill, faint midline and a lit endpoint. Painted once per sample (and when the
// skin or size changes), never per frame: the trace steps left by one sample, the newest point appended.
Canvas {
    id: sp
    property var sk
    property var acc
    property var series: []
    property real maxValue: 100
    property int points: 120
    property int rev: 0               // bumped by the card after it changes `series`
    renderTarget: Canvas.Image
    renderStrategy: Canvas.Immediate
    onRevChanged: requestPaint()
    onSkChanged: requestPaint()
    onAccChanged: requestPaint()
    onMaxValueChanged: requestPaint()
    onWidthChanged: requestPaint()
    onHeightChanged: requestPaint()

    onPaint: {
        var w = width, h = height
        if (w < 2 || h < 2 || !sk) return
        var ctx = getContext("2d"); ctx.reset()
        var a = function (al) { return Skins.css(acc, al) }
        ctx.strokeStyle = Skins.css([sk.fg[0], sk.fg[1], sk.fg[2], .06]); ctx.lineWidth = 1
        ctx.beginPath(); ctx.moveTo(0, h / 2 + .5); ctx.lineTo(w, h / 2 + .5); ctx.stroke()
        var m = series.length
        if (m < 2) return
        var pad = 3, step = (w - pad * 2) / (points - 1), right = w - pad
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
        ctx.globalAlpha = .25; ctx.fillStyle = a(1)
        ctx.beginPath(); ctx.arc(last[0], last[1], 2.4 + 3 * sk.glow, 0, Math.PI * 2); ctx.fill()
        ctx.globalAlpha = 1; ctx.fillStyle = Skins.css(sk.needle)
        ctx.beginPath(); ctx.arc(last[0], last[1], 2.4, 0, Math.PI * 2); ctx.fill()
    }
}
