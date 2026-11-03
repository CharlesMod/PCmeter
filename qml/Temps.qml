import QtQuick
import "Skins.js" as Skins
import "Fmt.js" as F

// The processor card's Temps tab: coolant, CPU die and aGpu temperature history (the web card's chart).
// The sampler keeps a 5 s ring (up to 2 h) and this pane loads it whenever a new row arrives, so the
// chart is drawn once per row (every 5 s) and the numbers change once per sample. Nothing runs per frame.
Item {
    id: tp
    property var sk
    property var acc
    required property var host
    required property var feed
    property bool shown: false
    property var live: ({ liq: null, die: null, gpu: null })     // the latest readings (set by the card)
    property var fanOn: undefined                                // true / false once the sampler reports a fan state
    property real histT: 0                                       // the newest history row's time; a change reloads
    property int range: host.trange

    property var aT: []
    property var aLiq: []
    property var aDie: []
    property var aGpu: []
    property var ev: []

    readonly property var series: [["liq", "Coolant", "LIQ"], ["die", "Die", "die"], ["gpu", "GPU", "GPU"]]
    property int rev: 0

    function num(v) { return typeof v === "number" && isFinite(v) ? v : null }
    function load() {
        var p = feed.tempHistory(range), rows = p.samples || [], t = [], l = [], d = [], g = []
        for (var i = 0; i < rows.length; i++) { t.push(rows[i][0]); l.push(num(rows[i][1])); d.push(num(rows[i][2])); g.push(num(rows[i][3])) }
        aT = t; aLiq = l; aDie = d; aGpu = g
        ev = (p.events || []).map(function (e) { return { t: e.t, on: !!e.on, trigger: e.trigger } })
        rev++
    }
    onShownChanged: if (shown) load()
    onHistTChanged: if (shown) load()
    onRangeChanged: if (shown) load()

    // The coolant tint behind the chart, from the skin's own palette: a hot haze that fades out toward 50° (never a
    // flat slab) and, where the skin has a warn colour, a faint warm stripe at 40-50°. The matrix skin tints with its
    // accent instead (its red is a dial colour, not a background), and skips the warm stripe.
    function tints(sk) {
        var accent = sk.chartTint === "accent", k = sk.light ? .75 : 1
        var hot = accent ? sk.cpu : sk.hot
        var out = [{ from: 50, to: 100, top: Skins.css(hot, (accent ? .08 : .11) * k), bottom: Skins.css(hot, 0) }]
        if (!accent) out.push({ from: 40, to: 50, top: Skins.css(sk.warn, .05 * k), bottom: Skins.css(sk.warn, .05 * k) })
        return out
    }
    function arr(k) { return k === "liq" ? aLiq : k === "die" ? aDie : aGpu }
    // The window on screen: first sample index, which series have a value in it.
    function win() {
        var n = aT.length - 1
        if (n < 0) return null
        var tEnd = aT[n], tStart = tEnd - range, i0 = 0
        while (i0 < n && aT[i0] < tStart) i0++
        var has = {}, ks = ["liq", "die", "gpu"]
        for (var a = 0; a < 3; a++) {
            has[ks[a]] = false
            var A = arr(ks[a])
            for (var i = i0; i <= n; i++) if (A[i] !== null) { has[ks[a]] = true; break }
        }
        return { n: n, i0: i0, tEnd: tEnd, tStart: tStart, has: has, lead: has.liq ? "liq" : has.die ? "die" : has.gpu ? "gpu" : "" }
    }
    function lastOf(k) { var A = arr(k); for (var i = A.length - 1; i >= 0; i--) if (A[i] !== null) return A[i]; return null }
    function deg(v) { return F.temp(v) }

    // text: re-evaluated when the card sets `live` (per sample) or the history reloads
    readonly property var w: { rev; return win() }
    readonly property real rowDt: { rev; var n = aT.length - 1; return n > 0 ? aT[n] - aT[n - 1] : 0 }
    readonly property var vals: {
        var out = []
        if (!w) return out
        for (var i = 0; i < 3; i++) {
            var k = series[i][0]
            if (w.has[k]) out.push({ v: deg(live[k] !== null && live[k] !== undefined ? live[k] : lastOf(k)), lbl: series[i][2], liq: k === "liq" })
        }
        return out
    }
    readonly property string rangeLabel: range === 60 ? "1 min" : range === 300 ? "5 min" : range === 1800 ? "30 min" : "2 h"
    readonly property string capText: {
        if (!w || !w.lead) return "Collecting, last " + rangeLabel
        var A = arr(w.lead), first = null, last = null
        for (var i = w.i0; i <= w.n; i++) if (A[i] !== null) { if (first === null) first = A[i]; last = A[i] }
        return (w.lead === "liq" ? "Coolant" : w.lead === "die" ? "Die" : "GPU") + ", last " + rangeLabel + " " + F.dtemp(last - first)
    }
    readonly property string maxText: {
        if (!w || !w.lead) return ""
        var mx = null
        for (var i = w.i0; i <= w.n; i++) if (aDie[i] !== null && (mx === null || aDie[i] > mx)) mx = aDie[i]
        return mx === null ? "" : "die max " + F.temp(mx)
    }

    Column {
        anchors.verticalCenter: parent.verticalCenter
        width: parent.width
        spacing: 6

        Item {
            width: parent.width; height: 22
            Row {
                id: valRow
                spacing: 0
                y: 1
                Repeater {
                    model: tp.vals
                    Row {
                        required property var modelData
                        required property int index
                        spacing: 3
                        Text { text: "·"; visible: index > 0; leftPadding: 4; rightPadding: 4
                               font.pixelSize: 14; font.family: tp.sk.num; color: Skins.qt(tp.sk.ink3) }
                        Text { id: vtxt; text: modelData.v; font.pixelSize: 14; font.family: tp.sk.num
                               color: modelData.liq ? Skins.qt(tp.sk.cpu) : Skins.qt(tp.sk.ink) }
                        Text { text: modelData.lbl; y: 3; leftPadding: 1
                               font.pixelSize: 10; font.weight: Font.DemiBold; font.letterSpacing: 1.4
                               font.family: tp.sk.ui; color: Skins.qt(tp.sk.ink3) }
                    }
                }
            }
            Row {
                anchors.right: parent.right; y: 4
                spacing: 9
                Repeater {
                    model: [[60, "1m"], [300, "5m"], [1800, "30m"], [7200, "2h"]]
                    Text {
                        required property var modelData
                        text: modelData[1]
                        font.pixelSize: 10; font.letterSpacing: 1.1; font.family: tp.sk.ui
                        font.weight: tp.range === modelData[0] ? Font.Bold : Font.Normal
                        color: tp.range === modelData[0] ? Skins.qt(tp.acc) : Skins.qt(tp.sk.ink3)
                        MouseArea { anchors.fill: parent; anchors.margins: -4; onClicked: tp.host.trange = modelData[0] }
                    }
                }
            }
            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Skins.qt(tp.sk.hair) }
        }

        Item {
            id: plot
            width: parent.width; height: 108
            visible: tp.shown
            readonly property real lPad: 30
            readonly property real rPad: 4
            readonly property real tPad: 4
            readonly property real bPad: 3
            readonly property real pw: width - lPad - rPad
            readonly property real ph: height - tPad - bPad
            function yOf(v) { return tPad + ph - F.clamp((v - 20) / 80) * ph }
            readonly property real step: tp.rowDt > 0 && tp.rowDt < tp.range / 4 ? pw * tp.rowDt / tp.range : 0   // px the plot moves left between two rows
            readonly property real slideMs: step > 0 ? tp.rowDt * 1000 : 0
            property real yFrom: 0
            property real yTo: 0
            property bool headOn: false

            // The static layer: tint bands, grid, degree labels. Painted when the skin, size or scale changes.
            Canvas {
                id: grid
                anchors.fill: parent
                renderTarget: Canvas.Image
                renderStrategy: Canvas.Immediate
                property bool liqScale: tp.w ? tp.w.has.liq : false
                onLiqScaleChanged: requestPaint()
                Connections {
                    target: tp
                    function onSkChanged() { grid.requestPaint() }
                    function onShownChanged() { if (tp.shown) grid.requestPaint() }
                }
                onWidthChanged: requestPaint()
                onPaint: {
                    var sk = tp.sk
                    if (!sk) return
                    var ctx = getContext("2d"); ctx.reset()
                    var w = width, h = height, lPad = plot.lPad, W = plot.pw
                    var fg = function (a) { return Skins.css([sk.fg[0], sk.fg[1], sk.fg[2], 1], a) }
                    if (liqScale) {
                        var tint = tp.tints(sk)
                        for (var b = 0; b < tint.length; b++) {
                            var y0 = plot.yOf(tint[b].to), y1 = plot.yOf(tint[b].from)
                            var g = ctx.createLinearGradient(0, y0, 0, y1)
                            g.addColorStop(0, tint[b].top); g.addColorStop(1, tint[b].bottom)
                            ctx.fillStyle = g; ctx.fillRect(lPad, y0, W, y1 - y0)
                        }
                    }
                    ctx.font = "10px '" + sk.ui + "'"; ctx.textAlign = "right"; ctx.textBaseline = "middle"
                    var ticks = [20, 40, 60, 80, 100]
                    for (var q = 0; q < 5; q++) {
                        var v = ticks[q]
                        ctx.strokeStyle = fg(v === 60 ? .06 : .035); ctx.lineWidth = 1
                        ctx.beginPath(); ctx.moveTo(lPad, Math.round(plot.yOf(v)) + .5); ctx.lineTo(w - plot.rPad, Math.round(plot.yOf(v)) + .5); ctx.stroke()
                        ctx.fillStyle = Skins.css(sk.ink3); ctx.fillText(v + "°", lPad - 5, plot.yOf(v) + (v === 100 ? 3 : v === 20 ? -3 : 0))
                    }
                }
            }

            // The moving layer: fan spans and the traces, one step wider than the window, painted once per row. An
            // XAnimator slides it left by one step over the row interval, so the traces scroll continuously on the
            // render thread (5 s rows for 5m and up, every sample for 1m).
            Item {
                x: plot.lPad; y: 0; width: plot.pw + plot.rPad; height: plot.height; clip: true
                Canvas {
                    id: lines
                    width: plot.pw + plot.rPad + plot.step; height: plot.height
                    renderTarget: Canvas.Image
                    renderStrategy: Canvas.Immediate
                    XAnimator { id: slide; target: lines; from: 0; duration: Math.max(100, plot.slideMs) }
                    onPainted: {
                        slide.stop()
                        rise.stop()
                        if (plot.step > 0 && plot.slideMs > 0) { slide.to = -plot.step; slide.restart() }
                        if (plot.headOn && plot.slideMs > 0) { rise.from = plot.yFrom; rise.to = plot.yTo; rise.restart() }
                        else if (plot.headOn) head.y = plot.yTo
                    }
                    Connections {
                        target: tp
                        function onRevChanged() { if (tp.shown) lines.requestPaint() }
                        function onShownChanged() { if (tp.shown) lines.requestPaint() }
                        function onSkChanged() { lines.requestPaint() }
                        function onFanOnChanged() { if (tp.shown) lines.requestPaint() }
                    }
                    onWidthChanged: requestPaint()
                    onPaint: {
                        var sk = tp.sk, W8 = tp.w
                        var ctx = getContext("2d"); ctx.reset()
                        plot.headOn = false
                        if (!sk || !W8 || !W8.lead) return
                        var h = height, n = W8.n, i0 = W8.i0, tEnd = W8.tEnd, has = W8.has, lead = W8.lead
                        var tPad = plot.tPad, bPad = plot.bPad, H = plot.ph, R = tp.range, W = plot.pw
                        var step = plot.step
                        var fg = function (a) { return Skins.css([sk.fg[0], sk.fg[1], sk.fg[2], 1], a) }
                        var X = function (t) { return (t - (tEnd - R)) / R * W + step }
                        var Y = plot.yOf
                        ctx.font = "600 9px '" + sk.ui + "'"; ctx.textAlign = "left"; ctx.textBaseline = "top"
                        var spans = [], open = null, e, q
                        for (q = 0; q < tp.ev.length; q++) {
                            e = tp.ev[q]
                            if (e.on && open === null) open = e.t
                            else if (!e.on && open !== null) { spans.push([open, e.t]); open = null }
                        }
                        if (open !== null) spans.push([open, tp.fanOn === false ? open : Infinity])
                        for (q = 0; q < spans.length; q++) {
                            var a = spans[q][0], b = spans[q][1]
                            var x0 = Math.max(0, X(a)), x1 = Math.min(W + step, X(Math.min(b, tEnd)))
                            if (x1 > x0) { ctx.fillStyle = fg(.04); ctx.fillRect(x0, tPad, x1 - x0, H) }
                            var fx = X(a)
                            if (fx > 0 && fx < W + step) {
                                ctx.strokeStyle = fg(.28); ctx.lineWidth = 1; ctx.setLineDash([3, 3])
                                ctx.beginPath(); ctx.moveTo(Math.round(fx) + .5, tPad); ctx.lineTo(Math.round(fx) + .5, h - bPad); ctx.stroke(); ctx.setLineDash([])
                                var lw = ctx.measureText("fans 100%").width
                                ctx.fillStyle = Skins.css(sk.ink3)
                                if (fx + 4 + lw > W) { ctx.textAlign = "right"; ctx.fillText("fans 100%", fx - 4, tPad + 1); ctx.textAlign = "left" }
                                else ctx.fillText("fans 100%", Math.max(2, fx + 4), tPad + 1)
                            }
                        }
                        var runs = function (k) {          // a null or a long hole in the data ends a run
                            var out = [], cur = [], A = tp.arr(k)
                            for (var i = i0 > 0 ? i0 - 1 : 0; i <= n; i++) {
                                var vv = A[i]
                                if (vv === null || (cur.length && tp.aT[i] - tp.aT[i - 1] > 30)) { if (cur.length) out.push(cur); cur = [] }
                                if (vv !== null) cur.push([X(tp.aT[i]), Y(vv)])
                            }
                            if (cur.length) out.push(cur)
                            return out
                        }
                        var curve = function (pts) {       // Catmull-Rom through every point
                            ctx.moveTo(pts[0][0], pts[0][1])
                            for (var i = 0; i < pts.length - 1; i++) {
                                var p0 = pts[i - 1] || pts[i], p1 = pts[i], p2 = pts[i + 1], p3 = pts[i + 2] || p2
                                ctx.bezierCurveTo(p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6,
                                                  p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6, p2[0], p2[1])
                            }
                        }
                        var stroke = function (k, style, width, dash, area) {
                            ctx.strokeStyle = style; ctx.lineWidth = width; ctx.setLineDash(dash)
                            var end = null, rs = runs(k)
                            for (var r = 0; r < rs.length; r++) {
                                var p = rs[r]
                                if (p.length === 1) { ctx.beginPath(); ctx.arc(p[0][0], p[0][1], width / 2 + .4, 0, Math.PI * 2); ctx.fillStyle = style; ctx.fill(); end = p[0]; continue }
                                ctx.beginPath(); curve(p); ctx.stroke(); end = p[p.length - 1]
                                if (area) {
                                    ctx.beginPath(); curve(p); ctx.lineTo(p[p.length - 1][0], h); ctx.lineTo(p[0][0], h); ctx.closePath()
                                    var g = ctx.createLinearGradient(0, 0, 0, h); g.addColorStop(0, Skins.css(sk.cpu, .22)); g.addColorStop(1, Skins.css(sk.cpu, 0))
                                    ctx.fillStyle = g; ctx.fill()
                                }
                            }
                            ctx.setLineDash([]); return end
                        }
                        ctx.lineJoin = "round"; ctx.lineCap = "round"
                        if (has.gpu && lead !== "gpu") stroke("gpu", Skins.css(sk.gpu, .8), 1, [1, 3])
                        if (has.die && lead !== "die") stroke("die", fg(.55), 1, [4, 3])
                        var en = stroke(lead, Skins.css(lead === "gpu" ? sk.gpu : sk.cpu, .95), 1.75, [], true)
                        var A = tp.arr(lead)
                        if (en && A[n] !== null) {         // the head dot rides the newest segment: its height runs from the last reading to the new one
                            plot.yTo = Y(A[n]); plot.yFrom = n > 0 && A[n - 1] !== null ? Y(A[n - 1]) : plot.yTo
                            plot.headOn = true
                        }
                    }
                }
            }
            Item {
                id: head
                x: plot.lPad + plot.pw; visible: plot.headOn
                YAnimator { id: rise; target: head; duration: Math.max(100, plot.slideMs); easing.type: Easing.Linear }
                Rectangle { x: -4.4; y: -4.4; width: 8.8; height: 8.8; radius: 4.4; color: tp.sk ? Skins.qt(tp.sk.cpu, .10) : "transparent" }
                Rectangle { x: -2.4; y: -2.4; width: 4.8; height: 4.8; radius: 2.4; color: tp.sk ? Skins.qt(tp.sk.cpu, .95) : "white" }
            }
        }

        Item {
            width: parent.width; height: 14
            Text { text: tp.capText; font.pixelSize: 10; font.letterSpacing: .4; font.family: tp.sk.ui; color: Skins.qt(tp.sk.ink3) }
            Text { anchors.right: parent.right; text: tp.maxText; font.pixelSize: 10; font.letterSpacing: .4; font.family: tp.sk.ui; color: Skins.qt(tp.sk.ink3) }
        }
    }
}
