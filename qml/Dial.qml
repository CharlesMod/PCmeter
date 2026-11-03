import QtQuick
import "Skins.js" as Skins
import "Fmt.js" as F

// Dual dial: load on the main face, temperature on the inset sub-dial in the bottom gap.
// Nothing here is drawn per frame. The face is one canvas painted once per skin. The value arc and the lit
// ticks are a second canvas painted once, shown through a shader that reveals it up to `u`. The needles are
// static shapes. `u` and the needle angles are animated by animators on the render thread, started once per
// sample by aim(): the GUI thread is idle while they move.
Item {
    id: dial
    property var sk
    property var acc                // accent [r,g,b,a]
    property var zones: [70, 85]
    property real loadV: 0          // 0..1, the target
    property var tempC: null        // degrees, or null
    property real tmin: 20          // the temperature scale: 20..100 for a die, 20..60 for coolant
    property real tmax: 100
    property string tag: ""         // source tag under the reading ("LIQ" for coolant)
    property int glideMs: 700
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
    readonly property int pct: Math.round(F.clamp(loadV) * 100)
    readonly property color accent: Skins.qt(acc)
    function ang(v) { return a0 + sweep * F.clamp(v) }
    function tnorm(t) { return (t - tmin) / (tmax - tmin) }
    function zoneColor(c) {
        return Skins.qt(c >= zones[1] ? sk.hot : c >= zones[0] ? sk.warn : sk.cool, .9)
    }

    Canvas {
        id: face
        anchors.fill: parent
        renderTarget: Canvas.Image
        renderStrategy: Canvas.Immediate
        Connections { target: dial; function onSkChanged() { face.requestPaint() } function onZonesChanged() { face.requestPaint() } function onTminChanged() { face.requestPaint() } function onTmaxChanged() { face.requestPaint() } }
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
            var z = dial.zones, bands = [[dial.tmin, z[0], sk.cool], [z[0], z[1], sk.warn], [z[1], dial.tmax, sk.hot]]
            ctx.lineCap = "butt"; ctx.lineWidth = 2.5
            for (var b = 0; b < 3; b++) {
                ctx.beginPath()
                ctx.arc(dial.sx, dial.sy, dial.sr - 4, dial.ang(dial.tnorm(bands[b][0])) + .03, dial.ang(dial.tnorm(bands[b][1])) - .03)
                ctx.strokeStyle = Skins.css(bands[b][2], .55); ctx.stroke()
            }
        }
    }

    // The lit part: glow, value arc and lit ticks, painted once per skin, revealed up to the needle by a shader.
    Canvas {
        id: art
        anchors.fill: parent
        renderTarget: Canvas.Image
        renderStrategy: Canvas.Immediate
        onPainted: arcTex.scheduleUpdate()
        Connections { target: dial; function onSkChanged() { art.requestPaint() } function onAccChanged() { art.requestPaint() } }
        onPaint: {
            var sk = dial.sk
            if (!sk || width < 2) return
            var ctx = getContext("2d"); ctx.reset()
            var glow = Math.min(1, sk.glow), segs = [.35, .55, .75, .92, 1], cx = dial.cx, cy = dial.cy
            ctx.lineCap = "round"; ctx.strokeStyle = Skins.css(dial.acc, .22 * glow); ctx.lineWidth = 4 + 6 * glow
            ctx.beginPath(); ctx.arc(cx, cy, dial.ra, dial.a0, dial.a0 + dial.sweep); ctx.stroke()
            ctx.lineWidth = 4
            for (var k = 0; k < 5; k++) {       // alpha rises along the sweep (a canvas stroke has no gradient)
                ctx.lineCap = k === 0 || k === 4 ? "round" : "butt"; ctx.strokeStyle = Skins.css(dial.acc, segs[k])
                ctx.beginPath(); ctx.arc(cx, cy, dial.ra, dial.a0 + dial.sweep * k * .2, dial.a0 + dial.sweep * (k + 1) * .2); ctx.stroke()
            }
            ctx.lineCap = "butt"
            for (var pass = 0; pass < 2; pass++) {       // lit ticks: minors, then majors
                ctx.beginPath()
                for (var i = 0; i <= 50; i++) {
                    if ((i % 5 === 0) !== (pass === 1)) continue
                    var a = dial.ang(i / 50), r0 = dial.rad - (pass ? 12 : 7), r1 = dial.rad - 4
                    ctx.moveTo(cx + Math.cos(a) * r0, cy + Math.sin(a) * r0); ctx.lineTo(cx + Math.cos(a) * r1, cy + Math.sin(a) * r1)
                }
                ctx.lineWidth = pass ? 1.6 : 1; ctx.strokeStyle = Skins.css(dial.acc, pass ? 1 : .75); ctx.stroke()
            }
        }
    }
    ShaderEffect {
        id: arcFx
        anchors.fill: parent
        property variant src: ShaderEffectSource { id: arcTex; sourceItem: art; hideSource: true; live: false }
        property real u: 0
        property real a0: dial.a0
        property real sweep: dial.sweep
        property vector2d center: Qt.vector2d(dial.cx / width, dial.cy / height)
        fragmentShader: Qt.resolvedUrl("shaders/sweep.frag.qsb")
    }
    UniformAnimator {
        id: arcAnim
        target: arcFx; uniform: "u"
        duration: dial.glideMs; easing.type: Easing.OutCubic
    }

    // Needles. The load needle has two fainter copies that lag it, which is the motion blur.
    Needle { id: ghost1; x: dial.cx; y: dial.cy; opacity: .22; r0: dial.ra - 9; r1: dial.rad - 2; hw: 2.2
             fill: dial.sk ? Skins.qt(dial.sk.needle) : "white"; duration: Math.round(dial.glideMs * 1.3); easing: Easing.OutCubic }
    Needle {
        id: needle; x: dial.cx; y: dial.cy; r0: dial.ra - 9; r1: dial.rad - 2; hw: 2.2
        fill: dial.sk ? Skins.qt(dial.sk.needle) : "white"
        halo: dial.sk ? Skins.qt(dial.acc, .9 * .28 * Math.min(1, dial.sk.glow)) : "transparent"
        haloW: dial.sk ? 1 + 3 * Math.min(1, dial.sk.glow) : 0
        duration: dial.glideMs
    }
    Needle {
        id: tneedle; x: dial.sx; y: dial.sy; visible: dial.tempC !== null
        r0: dial.sr * .5; r1: dial.sr - 1; hw: 1.4
        fill: {
            if (!dial.sk || dial.tempC === null) return "white"
            var c = dial.tempC
            return Skins.qt(c >= dial.zones[1] ? dial.sk.hot : c >= dial.zones[0] ? dial.sk.warn : dial.sk.cool, .9)
        }
        duration: dial.glideMs
    }

    // One call per sample: aim the arc and the needles at the new load and temperature (0..1 each).
    function aim(l, t, ms) {
        loadV = l
        var first = needle.shown !== needle.shown
        needle.go(ang(l) * 180 / Math.PI); ghost1.go(ang(l) * 180 / Math.PI)
        tneedle.go(ang(t) * 180 / Math.PI)
        if (first) { arcFx.u = l; seededU = l; arcFrom = l }
        else if (Math.abs(l - seededU) > .0005) {
            var p = Math.min(1, (Date.now() - arcT0) / glideMs)
            var cur = arcFrom + (seededU - arcFrom) * (1 - Math.pow(1 - p, 3))
            arcAnim.stop(); arcAnim.from = cur; arcAnim.to = l; arcAnim.restart()
            arcFrom = cur; arcT0 = Date.now(); seededU = l
        }
    }
    property real seededU: 0
    property real arcFrom: 0
    property real arcT0: 0

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
    Text {
        visible: dial.tag !== "" && dial.tempC !== null
        text: dial.tag
        font.family: dial.sk ? dial.sk.ui : ""; font.pixelSize: Math.max(6, Math.round(dial.sr * .26)); font.weight: Font.DemiBold
        color: dial.sk ? Skins.qt(dial.sk.ink3) : "white"
        x: dial.sx - width / 2; y: dial.sy + dial.sr * .5 - height / 2
    }
}
