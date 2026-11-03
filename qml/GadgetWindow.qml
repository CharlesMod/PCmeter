import QtQuick
import QtQuick.Window

// The native window of one card. Made by the QML engine, not by Python (see Gadget in backend/desktop.py):
// nothing on the render thread ever calls back into Python.
Window {
    id: win
    required property string cardKey
    required property var host
    required property var feed
    color: "transparent"
    onClosing: function (close) { close.accepted = host.closeRequested() }
    Card {
        anchors.fill: parent
        cardKey: win.cardKey; host: win.host; feed: win.feed
    }
}
