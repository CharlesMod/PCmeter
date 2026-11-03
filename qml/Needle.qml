import QtQuick
import QtQuick.Shapes
import "Skins.js" as Skins

// A meter needle, drawn as items so that turning it only changes a transform. While it moves it is
// smeared from `from` to `to` (radians): a few fainter exposures plus the sharp one, like a film frame.
Item {
    id: n
    property real cx
    property real cy
    property real r0
    property real r1
    property real halfWidth: 2
    property color fill: "white"
    property color halo: "transparent"
    property real haloWidth: 0
    property real from: 0
    property real to: 0
    readonly property bool smear: Math.abs(to - from) * r1 > 1.2
    x: cx; y: cy; width: 0; height: 0

    Repeater {
        model: 4
        Item {
            readonly property bool sharp: index === 3
            visible: sharp || n.smear
            rotation: (n.from + (n.to - n.from) * index / 3) * 180 / Math.PI
            opacity: sharp ? 1 : 0.22
            Shape {
                preferredRendererType: Shape.GeometryRenderer
                ShapePath {
                    strokeColor: sharp ? n.halo : "transparent"; strokeWidth: n.haloWidth; fillColor: "transparent"
                    joinStyle: ShapePath.RoundJoin
                    startX: n.r0; startY: -n.halfWidth
                    PathLine { x: n.r1; y: 0 }
                    PathLine { x: n.r0; y: n.halfWidth }
                    PathLine { x: n.r0; y: -n.halfWidth }
                }
                ShapePath {
                    strokeColor: "transparent"; fillColor: n.fill
                    startX: n.r0; startY: -n.halfWidth
                    PathLine { x: n.r1; y: 0 }
                    PathLine { x: n.r0; y: n.halfWidth }
                    PathLine { x: n.r0; y: -n.halfWidth }
                }
            }
        }
    }
}
