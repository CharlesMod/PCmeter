import QtQuick
import "Fmt.js" as F

// A horizontal meter. The fill is a full-width bar slid in behind a clip by an XAnimator (render thread).
Item {
    id: m
    property real frac: 0
    property color trackColor: "gray"
    property color fillColor: "white"
    property real r: height / 2
    property int duration: 450
    property real last: -1
    function xFor(f) { return -(1 - F.clamp(f)) * m.width }
    onFracChanged: {
        var f = F.clamp(frac)
        if (last < 0 || !visible) { xa.stop(); fill.x = xFor(f) }
        else if (f !== last) { xa.stop(); xa.from = xFor(last); xa.to = xFor(f); xa.restart() }
        last = f
    }
    onWidthChanged: { xa.stop(); fill.x = xFor(last < 0 ? frac : last) }
    Rectangle { anchors.fill: parent; radius: m.r; color: m.trackColor }
    Item {
        anchors.fill: parent; clip: true
        Rectangle { id: fill; width: m.width; height: m.height; radius: m.r; color: m.fillColor; x: m.xFor(m.frac) }
        XAnimator { id: xa; target: fill; duration: m.duration; easing.type: Easing.Linear }
    }
}
