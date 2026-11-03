import QtQuick
import QtQuick.Shapes
import "Skins.js" as Skins
import "Fmt.js" as F

// The activity strip: random counts whose rate climbs steeply with load, drawn as ticks on a tape that
// scrolls left over 6 s, with a counts-per-minute readout and a speaker with its own mute and volume.
// Counts are events. Each sample schedules the counts of the next interval (Poisson); the card's frame
// loop (which runs only while something is moving) fires them, lights the LED and plays the click.
Rectangle {
    id: strip
    property var sk
    property var acc
    property var host
    property string key: "cpu"
    property real u: 0                  // load 0..1 that sets the rate
    property real lit: 0                // LED brightness 0..1
    property real shut: 0               // shutter smear, seconds
    property string cpm: "0"
    readonly property bool audible: host && host.sound && !host.muted && host.volume > 0
    property bool busy: false
    property var evT: []                // event times, seconds
    property var evA: []                // amplitudes
    property int head: 0                // oldest event still counted (10 s)
    property int fired: 0               // events before this index have happened
    property real lastCpm: 0
    property real pan: key === "cpu" ? -.6 : .6

    implicitHeight: 70
    radius: 12
    color: sk ? Skins.qt(sk.well) : "transparent"
    border.color: sk ? Skins.qt(sk.hair) : "transparent"

    function schedule(interval, now) {
        var rate = .4 + 80 * Math.pow(F.clamp(u), 1.7)
        var n = F.poisson(rate * interval), list = []
        for (var i = 0; i < n; i++) list.push([now + Math.random() * interval, F.clamp(.25 + u * .55 + (Math.random() - .5) * .5, .15, 1)])
        list.sort(function (a, b) { return a[0] - b[0] })
        for (i = 0; i < list.length; i++) { evT.push(list[i][0]); evA.push(list[i][1]) }
    }
    function reset(now) {            // after a pause: forget the counts that never happened
        evT = []; evA = []; head = 0; fired = 0; lit = 0; busy = false; lastCpm = 0; cpm = "0"
        tape.requestPaint()
    }
    // Advance to `now`; returns true while anything is still moving (counts pending, on the tape or glowing).
    function step(now, dt) {
        while (fired < evT.length && evT[fired] <= now) {
            lit = Math.min(1, lit + .45 + evA[fired] * .5)
            if (audible && sfx.item) sfx.item.play(evA[fired])
            fired++
        }
        lit *= Math.exp(-dt * 20)
        if (lit < .01) lit = 0
        while (head < fired && now - evT[head] > 10) head++
        if (head > 1024) { evT = evT.slice(head); evA = evA.slice(head); fired -= head; head = 0 }
        if (now - lastCpm > .5) { lastCpm = now; var c = ((fired - head) * 6).toLocaleString(Qt.locale(), "f", 0); if (c !== cpm) cpm = c }
        var onTape = fired > head && now - evT[fired - 1] < 6.2
        busy = fired < evT.length || onTape || lit > 0
        return busy
    }
    function paintTape(now) { tape.nowS = now; tape.requestPaint() }

    Item {
        anchors.fill: parent; anchors.margins: 6; anchors.leftMargin: 10; anchors.rightMargin: 10
        Item {
            id: lbl
            width: 12; height: parent.height
            Text {
                anchors.centerIn: parent; rotation: -90
                text: "ACTIVITY"; font.pixelSize: 10; font.weight: Font.DemiBold; font.letterSpacing: 1.3
                font.family: strip.sk ? strip.sk.ui : ""
                color: strip.sk ? Skins.qt(strip.sk.ink3) : "white"
            }
        }
        Canvas {
            id: tape
            property real nowS: 0
            anchors.left: lbl.right; anchors.leftMargin: 10
            anchors.right: cpmBox.left; anchors.rightMargin: 10
            anchors.verticalCenter: parent.verticalCenter
            height: 34
            renderTarget: Canvas.Image
            onWidthChanged: requestPaint()
            property bool blank: false
            onPaint: {
                var w = width, h = height, sk = strip.sk
                if (w < 2 || !sk) return
                var mid = h / 2, speed = w / 6, now = nowS || Date.now() / 1000
                var smear = speed * strip.shut, lw = 1.4 + smear, fade = 1.4 / lw
                var buckets = [], fresh = [], any = false, i
                for (i = 0; i < 8; i++) buckets.push([])
                for (i = strip.head; i < strip.fired; i++) {
                    var age = now - strip.evT[i], x = w - 2 - age * speed
                    if (x < 0) continue
                    var amp = strip.evA[i], hh = (h - 4) * amp
                    if (age < .18) fresh.push([x, hh])
                    else {
                        var alpha = .15 + .8 * Math.exp(-age * .45) * F.clamp(x / 40) * amp
                        buckets[Math.min(7, Math.floor(alpha * 8))].push([x, hh])
                    }
                    any = true
                }
                if (!any && blank) return
                blank = !any
                var ctx = getContext("2d"); ctx.reset()
                ctx.strokeStyle = Skins.css([sk.fg[0], sk.fg[1], sk.fg[2], .07]); ctx.lineWidth = 1
                ctx.beginPath(); ctx.moveTo(0, mid + .5); ctx.lineTo(w, mid + .5); ctx.stroke()
                var acc = strip.acc
                ctx.lineWidth = lw
                for (var b = 0; b < 8; b++) {
                    if (!buckets[b].length) continue
                    ctx.beginPath()
                    for (i = 0; i < buckets[b].length; i++) { ctx.moveTo(buckets[b][i][0], mid - buckets[b][i][1] / 2); ctx.lineTo(buckets[b][i][0], mid + buckets[b][i][1] / 2) }
                    ctx.strokeStyle = Skins.css(acc, (b + .5) / 8 * fade); ctx.stroke()
                }
                if (fresh.length) {
                    ctx.beginPath()
                    for (i = 0; i < fresh.length; i++) { ctx.moveTo(fresh[i][0], mid - fresh[i][1] / 2); ctx.lineTo(fresh[i][0], mid + fresh[i][1] / 2) }
                    ctx.globalAlpha = .3; ctx.strokeStyle = Skins.css(acc); ctx.lineWidth = lw + 3 * sk.glow; ctx.stroke()
                    ctx.globalAlpha = 1; ctx.lineWidth = lw; ctx.strokeStyle = Skins.css(sk.needle); ctx.stroke()
                }
            }
        }
        Column {
            id: cpmBox
            anchors.right: snd.left; anchors.rightMargin: 10
            anchors.verticalCenter: parent.verticalCenter
            width: Math.max(cpmNum.implicitWidth, cpmCap.implicitWidth)
            Text {
                id: cpmNum; width: parent.width; horizontalAlignment: Text.AlignRight
                text: strip.cpm; font.pixelSize: 14; font.family: strip.sk ? strip.sk.num : ""
                color: strip.sk ? Skins.qt(strip.sk.ink) : "white"
            }
            Text {
                id: cpmCap; width: parent.width; horizontalAlignment: Text.AlignRight
                text: "CPM"; font.pixelSize: 10; font.letterSpacing: 1.1; font.family: strip.sk ? strip.sk.ui : ""
                color: strip.sk ? Skins.qt(strip.sk.ink3) : "white"
            }
        }
        Item {
            id: snd
            anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
            width: 26; height: 26
            opacity: strip.host && strip.host.sound ? 1 : .5
            HoverHandler { id: btnHover }
            Rectangle {
                id: btn
                anchors.fill: parent; radius: 13
                color: strip.sk ? Skins.qt([strip.sk.fg[0], strip.sk.fg[1], strip.sk.fg[2], btnHover.hovered ? .10 : .04]) : "transparent"
                border.width: 1
                border.color: strip.sk ? (strip.audible ? Skins.qt(strip.acc, .45) : Skins.qt(strip.sk.hair)) : "transparent"
                readonly property color ink: !strip.sk ? "white" : strip.audible ? Skins.qt(strip.acc) : btnHover.hovered ? Skins.qt(strip.sk.ink) : Skins.qt(strip.sk.ink3)
                Item {
                    anchors.centerIn: parent; width: 16; height: 16; scale: 14 / 16
                    Shape {
                        ShapePath { strokeColor: btn.ink; strokeWidth: 1.4; fillColor: "transparent"; capStyle: ShapePath.RoundCap; joinStyle: ShapePath.RoundJoin
                            PathSvg { path: "M2.5 6h2.2L8 3.2v9.6L4.7 10H2.5z" } }
                        ShapePath { strokeColor: strip.audible ? btn.ink : "transparent"; strokeWidth: 1.4; fillColor: "transparent"; capStyle: ShapePath.RoundCap
                            PathSvg { path: "M10.6 5.6a3.4 3.4 0 0 1 0 4.8M12.4 3.8a6 6 0 0 1 0 8.4" } }
                        ShapePath { strokeColor: strip.audible ? "transparent" : btn.ink; strokeWidth: 1.4; fillColor: "transparent"; capStyle: ShapePath.RoundCap
                            PathSvg { path: "M10.5 6l4 4M14.5 6l-4 4" } }
                    }
                }
                MouseArea {
                    anchors.fill: parent
                    onClicked: {
                        var h = strip.host
                        if (h.muted) {
                            h.muted = false
                            if (h.volume <= 0) h.volume = .6
                            if (!h.sound) h.setSound(true)     // unmuting a card means "let me hear it"
                        } else h.muted = true
                    }
                }
            }
            Item {           // volume slider, shown while the pointer is on the speaker or the slider
                id: pop
                readonly property bool shown: btnHover.hovered || popHover.hovered || dragging
                property bool dragging: false
                width: box.width; height: box.height + 6
                x: snd.width - width + 6; y: -height
                opacity: shown ? 1 : 0
                visible: opacity > 0
                Behavior on opacity { NumberAnimation { duration: 140 } }
                HoverHandler { id: popHover }
                Rectangle {
                    id: box
                    width: 112 + 32 + 8 + 20; height: 34
                    radius: strip.sk ? Math.min(12, strip.sk.radius * .6) : 8
                    color: strip.sk ? Skins.qt(strip.sk.disc) : "black"
                    border.color: strip.sk ? Skins.qt(strip.sk.edge) : "transparent"
                    Item {
                        id: track
                        x: 10; width: 112; height: parent.height
                        Rectangle { y: parent.height / 2 - 2; width: parent.width; height: 4; radius: 2
                            color: strip.sk ? Skins.qt([strip.sk.fg[0], strip.sk.fg[1], strip.sk.fg[2], .12]) : "gray" }
                        Rectangle { y: parent.height / 2 - 2; width: parent.width * (strip.host ? strip.host.volume : 0); height: 4; radius: 2; color: strip.acc ? Skins.qt(strip.acc) : "white" }
                        Rectangle { width: 12; height: 12; radius: 6; y: parent.height / 2 - 6
                            x: parent.width * (strip.host ? strip.host.volume : 0) - 6; color: strip.acc ? Skins.qt(strip.acc) : "white" }
                        MouseArea {
                            anchors.fill: parent; anchors.margins: -4
                            function set(mx) {
                                var v = Math.round(F.clamp((mx - 4) / track.width) * 100) / 100
                                strip.host.volume = v
                                if (v === 0) strip.host.muted = true
                            }
                            onPressed: { pop.dragging = true; set(mouse.x) }
                            onPositionChanged: if (pressed) set(mouse.x)
                            onReleased: pop.dragging = false
                        }
                    }
                    Text {
                        anchors.right: parent.right; anchors.rightMargin: 10; anchors.verticalCenter: parent.verticalCenter
                        text: Math.round((strip.host ? strip.host.volume : 0) * 100) + "%"
                        font.pixelSize: 11; font.family: strip.sk ? strip.sk.num : ""
                        color: strip.sk ? Skins.qt(strip.sk.ink2) : "white"
                    }
                }
            }
        }
    }
    Loader {
        id: sfx
        active: strip.audible
        source: "Clicks.qml"
        onLoaded: { item.key = strip.key; item.volume = Qt.binding(function () { return strip.host.volume }) }
    }
}
