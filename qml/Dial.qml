import QtQuick
import "Skins.js" as Skins
import "Fmt.js" as F

// Dual dial: load on the main face, temperature on the inset sub-dial in the bottom gap.
// The face (ticks, labels, bands) is one canvas painted once per skin; everything that moves is an item:
// the lit ticks, the value arc, the needles and the readouts, so a moving needle repaints a few small regions.
Item {
    id: dial
    property var sk
    property var acc                // accent [r,g,b,a]
    property var zones: [70, 85]
    property real load: 0           // 0..1, the spring's position
    property real loadFrom: 0       // where it was when the shutter opened
    property real temp: 0           // normalised 0..1
    property real tempFrom: 0
    property var tempC: null        // degrees, or null
    width: 176; height: 176

    readonly property real a0: 0.75 * Math.PI
    readonly property real sweep: 1.5 * Math.PI
    readonly property real cx: width / 2
    readonly property real cy: height * 0.455
    readonly property real rad: Math.min(width / 2 - 6, cy - 4)
    readonly property real ra: rad - 30
    readonly property real sx: cx
    readonly property real sy: cy + rad * .73
    readonly property real sr: rad * .34
    readonly property int lit: load > 0.001 ? Math.floor(F.clamp(load) * 50 + 0.05) : -1
    readonly property int pct: Math.round(F.clamp(load) * 100)
    readonly property color accent: Skins.qt(acc)
    function ang(v) { return a0 + sweep * F.clamp(v) }
    function tnorm(t) { return (t - 20) / 80 }
    function zoneColor(c) {
        return Skins.qt(c >= zones[1] ? sk.hot : c >= zones[0] ? sk.warn : sk.cool, .9)
    }

    Canvas {
        id: face
        anchors.fill: parent
        renderTarget: Canvas.Image
        renderStrategy: Canvas.Immediate
        Connections { target: dial; function onSkChanged() { face.requestPaint() } function onZonesChanged() { face.requestPaint() } }
        onPaint: {
            var sk = dial.sk
            if (!sk) return
            var ctx = getContext("2d"); ctx.reset()
            var cx = dial.cx, cy = dial.cy, rad = dial.rad, fgc = function (a) { return Skins.css([sk.fg[0], sk.fg[1], sk.fg[2], a]) }
            var g = ctx.createRadialGradient(cx, cy - rad * .5, rad * .1, cx, cy, rad)
            g.addColorStop(0, Skins.css(sk.faceHi)); g.addColorStop(1, Skins.css(sk.faceLo))
            ctx.beginPath(); ctx.arc(cx, cy, rad, 0, Math.PI * 2); ctx.fillStyle = g; ctx.fill()
            ctx.lineWidth = 1; ctx.strokeStyle = fgc(.10); ctx.stroke()
            for (var pass = 0; pass < 2; pass++) {        // minors, then majors
                ctx.beginPath()
                for (var i = 0; i <= 50; i++) {
                    if ((i % 5 === 0) !== (pass === 1)) continue
                    var a = dial.ang(i / 50), r0 = rad - (pass ? 12 : 7), r1 = rad - 4
                    ctx.moveTo(cx + Math.cos(a) * r0, cy + Math.sin(a) * r0); ctx.lineTo(cx + Math.cos(a) * r1, cy + Math.sin(a) * r1)
                }
                ctx.lineWidth = pass ? 1.6 : 1; ctx.strokeStyle = fgc(pass ? .30 : .12); ctx.stroke()
            }
            ctx.font = "8.5px '" + sk.num + "'"; ctx.fillStyle = Skins.css(sk.ink3); ctx.textBaseline = "middle"; ctx.textAlign = "center"
            var marks = [[0, "0"], [0.5, "50"], [1, "100"]]
            for (var m = 0; m < 3; m++) {
                var am = dial.ang(marks[m][0]); ctx.fillText(marks[m][1], cx + Math.cos(am) * (rad - 21), cy + Math.sin(am) * (rad - 21))
            }
            ctx.lineCap = "round"; ctx.lineWidth = 4
            ctx.beginPath(); ctx.arc(cx, cy, dial.ra, dial.a0, dial.a0 + dial.sweep); ctx.strokeStyle = fgc(.06); ctx.stroke()
            ctx.font = "600 8px '" + sk.ui + "'"; ctx.textBaseline = "alphabetic"
            ctx.fillText("LOAD", cx, cy - rad * .26)
            ctx.beginPath(); ctx.arc(dial.sx, dial.sy, dial.sr, 0, Math.PI * 2)
            ctx.fillStyle = Skins.css(sk.disc); ctx.fill(); ctx.strokeStyle = fgc(.14); ctx.lineWidth = 1; ctx.stroke()
            var z = dial.zones, bands = [[20, z[0], sk.cool], [z[0], z[1], sk.warn], [z[1], 100, sk.hot]]
            ctx.lineCap = "butt"; ctx.lineWidth = 2.5
            for (var b = 0; b < 3; b++) {
                ctx.beginPath()
                ctx.arc(dial.sx, dial.sy, dial.sr - 4, dial.ang(dial.tnorm(bands[b][0])) + .03, dial.ang(dial.tnorm(bands[b][1])) - .03)
                ctx.strokeStyle = Skins.css(bands[b][2], .55); ctx.stroke()
            }
        }
    }

    // ticks lit up to the needle
    Repeater {
        model: 51
        Rectangle {
            readonly property bool major: index % 5 === 0
            readonly property real a: dial.ang(index / 50)
            visible: index <= dial.lit
            x: dial.cx + Math.cos(a) * (dial.rad - (major ? 12 : 7))
            y: dial.cy + Math.sin(a) * (dial.rad - (major ? 12 : 7)) - height / 2
            width: major ? 8 : 3; height: major ? 1.6 : 1
            rotation: a * 180 / Math.PI
            transformOrigin: Item.Left
            color: Skins.qt(dial.acc, major ? 1 : .75)
            antialiasing: true
        }
    }

    // The moving parts (value arc and both needles) are one canvas, repainted on each animation tick.
    // Shapes would re-triangulate their paths in every polish pass, which cost several times more.
    // One call per animation tick: set all four positions, then repaint once.
    function move(l, lf, t, tf) { load = l; loadFrom = lf; temp = t; tempFrom = tf; live.requestPaint() }

    Canvas {
        id: live
        anchors.fill: parent
        renderTarget: Canvas.Image
        renderStrategy: Canvas.Immediate
        Connections {
            target: dial
            function onTempCChanged() { live.requestPaint() }
            function onSkChanged() { live.requestPaint() }
            function onAccChanged() { live.requestPaint() }
        }
        function needle(ctx, cx, cy, r0, r1, hw, fill, halo, haloW, from, to) {
            var smear = Math.abs(to - from) * r1 > 1.2
            for (var i = smear ? 0 : 3; i < 4; i++) {      // a few faint exposures, then the sharp one
                ctx.save(); ctx.translate(cx, cy); ctx.rotate(from + (to - from) * i / 3)
                ctx.globalAlpha = i === 3 ? 1 : .22
                ctx.beginPath(); ctx.moveTo(r0, -hw); ctx.lineTo(r1, 0); ctx.lineTo(r0, hw); ctx.closePath()
                if (i === 3 && haloW > 0) { ctx.strokeStyle = halo; ctx.lineWidth = haloW; ctx.lineJoin = "round"; ctx.stroke() }
                ctx.fillStyle = fill; ctx.fill(); ctx.restore()
            }
        }
        onPaint: {
            var sk = dial.sk
            if (!sk || width < 2) return
            var ctx = getContext("2d"); ctx.reset()
            var glow = Math.min(1, sk.glow), u = F.clamp(dial.load), segs = [.35, .55, .75, .92, 1]
            if (u > 0.004) {
                ctx.lineCap = "round"; ctx.strokeStyle = Skins.css(dial.acc, .22 * glow); ctx.lineWidth = 4 + 6 * glow
                ctx.beginPath(); ctx.arc(dial.cx, dial.cy, dial.ra, dial.a0, dial.a0 + dial.sweep * u); ctx.stroke()
                ctx.lineWidth = 4
                for (var k = 0; k < 5; k++) {       // alpha rises along the sweep (a canvas stroke has no gradient)
                    var lo = k * .2, hi = Math.min(u, lo + .2)
                    if (hi <= lo) break
                    ctx.lineCap = k === 0 || k === 4 ? "round" : "butt"; ctx.strokeStyle = Skins.css(dial.acc, segs[k])
                    ctx.beginPath(); ctx.arc(dial.cx, dial.cy, dial.ra, dial.a0 + dial.sweep * lo, dial.a0 + dial.sweep * hi); ctx.stroke()
                }
            }
            needle(ctx, dial.cx, dial.cy, dial.ra - 9, dial.rad - 2, 2.2, Skins.css(sk.needle),
                   Skins.css(dial.acc, .9 * .28 * glow), 1 + 3 * glow, dial.ang(dial.loadFrom), dial.ang(dial.load))
            if (dial.tempC !== null) {
                var c = dial.tempC, tc = c >= dial.zones[1] ? sk.hot : c >= dial.zones[0] ? sk.warn : sk.cool
                needle(ctx, dial.sx, dial.sy, dial.sr * .5, dial.sr - 1, 1.4, Skins.css(tc, .9), "", 0,
                       dial.ang(dial.tempFrom), dial.ang(dial.temp))
            }
        }
    }

    // readout: the big percentage, centred with its unit
    Text {
        id: big
        text: dial.pct
        font.family: dial.sk ? dial.sk.num : ""; font.pixelSize: Math.round(dial.rad * .38); font.weight: Font.Light
        color: dial.sk ? Skins.qt(dial.sk.ink) : "white"
        x: dial.cx - (width + unit.width + 2) / 2; y: dial.cy + dial.rad * .1 - baselineOffset
    }
    Text {
        id: unit
        text: "%"
        font.family: dial.sk ? dial.sk.num : ""; font.pixelSize: 11; font.weight: Font.Medium
        color: dial.sk ? Skins.qt(dial.sk.ink3) : "white"
        x: big.x + big.width + 2; y: dial.cy + dial.rad * .1 - baselineOffset
    }

    // temperature reading
    Text {
        text: dial.tempC === null ? "—" : Math.round(dial.tempC) + "°"
        font.family: dial.sk ? dial.sk.num : ""; font.pixelSize: Math.round(dial.sr * .42)
        color: dial.sk ? Skins.qt(dial.sk.ink) : "white"
        x: dial.sx - width / 2; y: dial.sy + 1 - height / 2
    }
}
