import QtQuick
import "Skins.js" as Skins

// The menu and dismiss buttons in a card's corner, in the skin's own dress (round dots on macOS, a
// boxy pair on Matrix, caption buttons on Windows...).
Rectangle {
    id: b
    property var sk
    property bool isClose: false
    property bool groupHover: false      // macOS shows its glyphs while the pointer is on either button
    signal clicked()
    readonly property var spec: sk ? sk.btn : ({ w: 24, h: 24, r: 8, bg: .06, bgHover: .14 })
    readonly property bool hov: area.containsMouse
    readonly property bool mac: spec.mac === true
    width: spec.w; height: spec.h; radius: spec.r
    border.width: spec.border || (spec.hoverBorder && hov) ? 1 : 0
    border.color: !sk ? "transparent" : (spec.border && hov) ? Skins.qt(sk.ink) : Skins.qt(sk.edge)
    color: {
        if (!sk) return "transparent"
        var fg = sk.fg
        if (mac) return isClose ? "#ff5f57" : "#c7c7cc"
        if (isClose && spec.closeBg) return Skins.qt(hov && spec.closeHoverBg ? spec.closeHoverBg : spec.closeBg)
        if (isClose && hov && spec.closeHover) return spec.closeHover === "hot" ? Skins.qt(sk.hot) : Skins.qt(spec.closeHover)
        if (isClose && hov && !spec.bgFixed) return Skins.qt(sk.hot)
        if (spec.bgFixed && !hov) return Skins.qt(spec.bgFixed)
        return Skins.qt([fg[0], fg[1], fg[2], hov ? spec.bgHover : spec.bg])
    }
    readonly property color ink: {
        if (!sk) return "white"
        if (mac) return Qt.rgba(0, 0, 0, .55)
        if (isClose && spec.closeFg) return Skins.qt(spec.closeFg)
        if (isClose && hov) return Skins.qt(spec.closeHoverFg || [255, 255, 255, 1])
        return hov ? Skins.qt(sk.ink) : Skins.qt(sk.ink2)
    }
    Item {
        anchors.centerIn: parent
        width: 10; height: 10; scale: mac ? .6 : 1
        opacity: mac ? (groupHover ? 1 : 0) : 1
        Repeater {        // dismiss: a cross
            model: b.isClose ? 2 : 0
            Rectangle {
                anchors.centerIn: parent; width: 8.5; height: 1.3; radius: .65; color: b.ink
                rotation: index ? -45 : 45; antialiasing: true
            }
        }
        Repeater {        // menu: three dots
            model: b.isClose ? 0 : 3
            Rectangle {
                width: 2.1; height: 2.1; radius: 1.05; color: b.ink; y: 4
                x: [.75, 3.95, 7.15][index]
            }
        }
    }
    MouseArea { id: area; anchors.fill: parent; hoverEnabled: true; onClicked: b.clicked() }
}
