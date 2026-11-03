import QtQuick
import QtQuick.Shapes
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

    // value arc: five segments whose alpha rises along the sweep (Shapes cannot stroke with a gradient)
    Shape {
        anchors.fill: parent
        visible: dial.load > 0.004
        preferredRendererType: Shape.GeometryRenderer
        ShapePath {      // glow under the arc
            strokeColor: Skins.qt(dial.acc, .22 * Math.min(1, dial.sk ? dial.sk.glow : 1)); strokeWidth: 4 + 6 * (dial.sk ? dial.sk.glow : 1)
            fillColor: "transparent"; capStyle: ShapePath.RoundCap
            PathAngleArc { centerX: dial.cx; centerY: dial.cy; radiusX: dial.ra; radiusY: dial.ra; startAngle: 135; sweepAngle: 270 * F.clamp(dial.load) }
        }
        ShapePath {
            strokeColor: Skins.qt(dial.acc, .35); strokeWidth: 4; fillColor: "transparent"; capStyle: ShapePath.RoundCap
            PathAngleArc { centerX: dial.cx; centerY: dial.cy; radiusX: dial.ra; radiusY: dial.ra; startAngle: 135; sweepAngle: 270 * Math.min(F.clamp(dial.load), .2) }
        }
        ShapePath {
            strokeColor: Skins.qt(dial.acc, .55); strokeWidth: 4; fillColor: "transparent"; capStyle: ShapePath.FlatCap
            PathAngleArc { centerX: dial.cx; centerY: dial.cy; radiusX: dial.ra; radiusY: dial.ra; startAngle: 135 + 270 * .2; sweepAngle: 270 * F.clamp(dial.load - .2, 0, .2) }
        }
        ShapePath {
            strokeColor: Skins.qt(dial.acc, .75); strokeWidth: 4; fillColor: "transparent"; capStyle: ShapePath.FlatCap
            PathAngleArc { centerX: dial.cx; centerY: dial.cy; radiusX: dial.ra; radiusY: dial.ra; startAngle: 135 + 270 * .4; sweepAngle: 270 * F.clamp(dial.load - .4, 0, .2) }
        }
        ShapePath {
            strokeColor: Skins.qt(dial.acc, .92); strokeWidth: 4; fillColor: "transparent"; capStyle: ShapePath.FlatCap
            PathAngleArc { centerX: dial.cx; centerY: dial.cy; radiusX: dial.ra; radiusY: dial.ra; startAngle: 135 + 270 * .6; sweepAngle: 270 * F.clamp(dial.load - .6, 0, .2) }
        }
        ShapePath {
            strokeColor: Skins.qt(dial.acc, 1); strokeWidth: 4; fillColor: "transparent"; capStyle: ShapePath.RoundCap
            PathAngleArc { centerX: dial.cx; centerY: dial.cy; radiusX: dial.ra; radiusY: dial.ra; startAngle: 135 + 270 * .8; sweepAngle: 270 * F.clamp(dial.load - .8, 0, .2) }
        }
    }

    Needle {
        cx: dial.cx; cy: dial.cy; r0: dial.ra - 9; r1: dial.rad - 2; halfWidth: 2.2
        fill: dial.sk ? Skins.qt(dial.sk.needle) : "white"
        halo: Skins.qt(dial.acc, .9 * .28 * Math.min(1, dial.sk ? dial.sk.glow : 1)); haloWidth: 1 + 3 * (dial.sk ? dial.sk.glow : 1)
        from: dial.ang(dial.loadFrom); to: dial.ang(dial.load)
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

    // temperature needle and reading
    Needle {
        visible: dial.tempC !== null
        cx: dial.sx; cy: dial.sy; r0: dial.sr * .5; r1: dial.sr - 1; halfWidth: 1.4
        fill: dial.tempC === null || !dial.sk ? "white" : dial.zoneColor(dial.tempC)
        halo: fill; haloWidth: 0
        from: dial.ang(dial.tempFrom); to: dial.ang(dial.temp)
    }
    Text {
        text: dial.tempC === null ? "—" : Math.round(dial.tempC) + "°"
        font.family: dial.sk ? dial.sk.num : ""; font.pixelSize: Math.round(dial.sr * .42)
        color: dial.sk ? Skins.qt(dial.sk.ink) : "white"
        x: dial.sx - width / 2; y: dial.sy + 1 - height / 2
    }
}
