import QtQuick
import QtMultimedia

// The Geiger clicks. Loaded only while this card is unmuted with the master on, so QtMultimedia (and its
// audio stream) never starts for a muted card. Two effects alternate so that bursts can overlap.
Item {
    id: clicks
    property string key: "cpu"
    property real volume: .6
    property bool flip: false
    SoundEffect { id: a; source: Qt.resolvedUrl("click-" + clicks.key + ".wav") }
    SoundEffect { id: b; source: Qt.resolvedUrl("click-" + clicks.key + ".wav") }
    function play(amp) {
        clicks.flip = !clicks.flip
        var e = clicks.flip ? a : b
        e.volume = clicks.volume * clicks.volume * (.2 + .8 * amp)
        e.play()
    }
}
