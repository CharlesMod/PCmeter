import QtQuick
import QtQuick.Shapes

// A needle: a static triangle (a Shape that is built once) on a zero-size holder. `go(deg)` starts a
// RotationAnimator, which the render thread runs; the GUI thread does nothing while the needle swings.
Item {
    id: n
    property real r0: 0                 // inner and outer radius, half width
    property real r1: 40
    property real hw: 2
    property color fill: "white"
    property color halo: "transparent"
    property real haloW: 0
    property int duration: 450
    property int easing: Easing.OutCubic
    property real from0: 0
    property real t0: 0
    property real shown: NaN            // where the last animation was sent
    width: 0; height: 0

    // Where the needle is now, worked out from the running animation (OutCubic), so a new sample
    // retargets from the true angle and the needle never jumps or re-starts from the old target.
    function current() {
        if (!(t0 > 0)) return shown
        var p = Math.min(1, (Date.now() - t0) / n.duration)
        return from0 + (shown - from0) * (1 - Math.pow(1 - p, 3))
    }
    function go(deg) {
        if (shown !== shown) { rotation = deg; shown = deg; return }      // first value: no swing
        if (Math.abs(deg - shown) < .02) return
        var c = current()
        ra.stop()
        ra.from = c; ra.to = deg
        ra.restart()
        from0 = c; t0 = Date.now(); shown = deg
    }
    Shape {
        preferredRendererType: Shape.CurveRenderer
        ShapePath {
            fillColor: n.fill
            strokeColor: n.haloW > 0 ? n.halo : "transparent"; strokeWidth: n.haloW; joinStyle: ShapePath.RoundJoin
            startX: n.r0; startY: -n.hw
            PathLine { x: n.r1; y: 0 }
            PathLine { x: n.r0; y: n.hw }
        }
    }
    RotationAnimator {
        id: ra
        target: n
        direction: RotationAnimator.Numerical
        duration: n.duration
        easing.type: n.easing
    }
}
