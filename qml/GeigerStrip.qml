import QtQuick
import QtQuick.Shapes
import "Skins.js" as Skins
import "Fmt.js" as F

// The activity strip: random counts whose rate climbs steeply with load, drawn as ticks on a tape that
// slides left over 6 s, with a counts-per-minute readout and a speaker with its own mute and volume.
// Each sample draws the counts of the next interval once, into one small canvas (a segment of the tape).
// The segments sit on a belt that an XAnimator slides at constant speed on the render thread, so ticks
// appear at their time and travel left with no JS per frame. The LED pulses once per sample; the click
// sound (only when unmuted) has its own timer that wakes at each count.
Rectangle {
    id: strip
    property var sk
    property var acc
    property var host
    property string key: "cpu"
    property real u: 0                  // load 0..1 that sets the rate
    property string cpm: "0"
    signal pulse(real peak, int ms)
    readonly property bool audible: host && host.sound && !host.muted && host.volume > 0
    property var evT: []                // counts of the last 10 s: times (seconds) and amplitudes
    property var evA: []
    property int burstMs: 160           // the belt moves, and the LED fades, within this window after a sample
    property real iv: .5                // sample interval, seconds
    property real tref: 0               // belt time origin, seconds
    property int slot: 0
    readonly property real speed: tapeView.width / 6
    readonly property int pool: Math.min(80, Math.ceil(6.6 / Math.max(.1, iv)) + 2)
    property var clickT: []             // counts of this interval not yet clicked
    property var clickA: []
    property int clickAt: 0
    property real pan: key === "cpu" ? -.6 : .6

    implicitHeight: 70
    radius: 12
    color: sk ? Skins.qt(sk.well) : "transparent"
    border.color: sk ? Skins.qt(sk.hair) : "transparent"

    function beltX(t) { return tapeView.width - speed * (t - tref) }
    // After a pause (or at the start): forget the counts that never happened.
    function reset(now) {
        evT = []; evA = []; cpm = "0"; tref = now; clickT = []; clickAt = 0; clickTimer.stop()
        beltAnim.stop(); belt.x = beltX(now)
        for (var i = 0; i < segs.count; i++) segs.itemAt(i).clear()
    }
    // One call per sample, at `now`: draw the counts of [now, now + interval) and start the belt moving.
    function schedule(interval, now) {
        iv = interval
        if (tref === 0) tref = now
        var rate = .4 + 80 * Math.pow(F.clamp(u), 1.7)
        var n = F.poisson(rate * interval), list = [], i, amax = 0
        for (i = 0; i < n; i++) list.push([now + Math.random() * interval, F.clamp(.25 + u * .55 + (Math.random() - .5) * .5, .15, 1)])
        list.sort(function (a, b) { return a[0] - b[0] })
        var keep = 0
        while (keep < evT.length && evT[keep] < now + interval - 10) keep++
        var t = evT.slice(keep), a = evA.slice(keep)
        for (i = 0; i < list.length; i++) { t.push(list[i][0]); a.push(list[i][1]); amax = Math.max(amax, list[i][1]) }
        evT = t; evA = a
        cpm = (t.length * 6).toLocaleString(Qt.locale(), "f", 0)
        if (now - tref > 3000) rebase(now)
        var seg = segs.itemAt(slot % pool)
        slot++
        if (seg) seg.draw(list, now)
        beltAnim.stop()
        beltAnim.from = beltX(now); beltAnim.to = beltX(now + interval); beltAnim.duration = Math.round(interval * 1040)
        beltAnim.restart()
        if (n > 0) pulse(Math.min(1, .4 + .1 * n + .3 * amax), burstMs)
        clickT = list.map(function (e) { return e[0] }); clickA = list.map(function (e) { return e[1] }); clickAt = 0
        if (audible) armClick()
    }
    function rebase(now) {              // keep belt coordinates small: move the origin to `now`
        tref = now
        belt.x = beltX(now)
    }
    function armClick() {
        var now = Date.now() / 1000
        while (clickAt < clickT.length && clickT[clickAt] <= now) clickAt++
        if (clickAt < clickT.length && sfx.item) { clickTimer.interval = Math.max(1, Math.round((clickT[clickAt] - now) * 1000)); clickTimer.restart() }
    }
    Timer {
        id: clickTimer
        onTriggered: {
            if (strip.audible && sfx.item && strip.clickAt < strip.clickT.length) sfx.item.play(strip.clickA[strip.clickAt++])
            strip.armClick()
        }
    }
    onAudibleChanged: if (!audible) clickTimer.stop()

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
        Item {
            id: tapeBox
            anchors.left: lbl.right; anchors.leftMargin: 10
            anchors.right: cpmBox.left; anchors.rightMargin: 10
            anchors.verticalCenter: parent.verticalCenter
            height: 34
            Rectangle {              // the faint midline
                y: parent.height / 2; width: parent.width; height: 1
                color: strip.sk ? Skins.qt([strip.sk.fg[0], strip.sk.fg[1], strip.sk.fg[2], .07]) : "transparent"
            }
            Item {
                id: tapeView
                anchors.fill: parent
                clip: true
                layer.enabled: true
                layer.effect: ShaderEffect {
                    property real fadeLeft: Math.min(.5, 40 / Math.max(1, tapeView.width))
                    property real freshRight: Math.min(.4, 7 / Math.max(1, tapeView.width))
                    property color hot: strip.sk ? Skins.qt(strip.sk.needle) : "white"
                    fragmentShader: Qt.resolvedUrl("shaders/tape.frag.qsb")
                }
                Item {
                    id: belt
                    Repeater {
                        id: segs
                        model: strip.pool
                        Canvas {
                            id: seg
                            property real t0: 0
                            property var list: []
                            x: strip.speed * (t0 - strip.tref) - 2
                            width: Math.ceil(strip.speed * strip.iv) + 4; height: tapeView.height
                            renderTarget: Canvas.Image
                            renderStrategy: Canvas.Immediate
                            property bool blank: true
                            function draw(l, t) { t0 = t; list = l; blank = false; requestPaint() }
                            function clear() { list = []; if (!blank) { blank = true; requestPaint() } }
                            onWidthChanged: requestPaint()
                            onPaint: {
                                var ctx = getContext("2d"); ctx.reset()
                                if (list.length === 0 || !strip.acc) return
                                var h = height, mid = h / 2
                                for (var i = 0; i < list.length; i++) {
                                    var x = 2 + (list[i][0] - t0) * strip.speed, hh = (h - 4) * list[i][1]
                                    ctx.strokeStyle = Skins.css(strip.acc, .45 + .5 * list[i][1]); ctx.lineWidth = 1.4
                                    ctx.beginPath(); ctx.moveTo(x, mid - hh / 2); ctx.lineTo(x, mid + hh / 2); ctx.stroke()
                                }
                            }
                        }
                    }
                    XAnimator { id: beltAnim; target: belt; easing.type: Easing.Linear }
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
