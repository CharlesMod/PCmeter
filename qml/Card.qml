import QtQuick
import "Skins.js" as Skins
import "Fmt.js" as F

// One gadget card (CPU or GPU). `host` is the window's Bridge (skin, view, mute, move/resize/menu) and
// `feed` delivers a telemetry snapshot per sample. Nothing here runs on a timer except `tick`, and that
// one runs only while the card is visible and something is still moving: the needles settling, a
// Geiger count on the tape or the LED fading. At rest the scene graph has nothing to redraw.
Item {
    id: root
    required property string cardKey
    required property var host
    required property var feed

    readonly property bool isCpu: cardKey === "cpu"
    readonly property var sk: Skins.get(host.skin)
    readonly property var acc: isCpu ? sk.cpu : sk.gpu
    readonly property string view: host.view            // full | compact | gauge
    readonly property bool gauge: view === "gauge"
    readonly property bool compact: view === "compact"
    readonly property real pad: gauge ? 16 : 18
    readonly property real inner: width - 2 * pad
    readonly property bool narrow: inner <= 380

    // ---- data (set by ingest)
    property string nameText: "—"
    property string subText: "—"
    property bool present: true
    property var tempC: null
    property var hist: []
    property var bwHist: []
    property int rev: 0
    property real bwMax: 100
    property string peakText: "peak —"
    property var rows: ({})                  // label -> [value, unit]
    property real vramFrac: 0
    property var cores: []
    property var peaks: []
    property string threadsCap: "—"
    property var procs: []
    property string procCap: ""
    property string bwValue: "—"
    property string bwUnit: ""
    property real bwFrac: 0
    property string bwNote: ""
    property string bwPeak: ""
    property int sampleMs: 500
    readonly property var zones: isCpu ? [70, 85] : [70, 83]

    // ---- motion: two springs (58 / 11.5, as in the web cards); `animating` is true while any part moves
    property var ld: ({ x: 0, v: 0, t: 0 })
    property var tp: ({ x: 0, v: 0, t: 0 })
    property bool animating: false
    property real lastT: 0
    property int ticks: 0
    function nowS() { return Date.now() / 1000 }
    function spring(s, dt) {
        s.v += (58 * (s.t - s.x) - 11.5 * s.v) * dt
        s.x += s.v * dt
        if (Math.abs(s.t - s.x) < 1e-4 && Math.abs(s.v) < 1e-3) { s.x = s.t; s.v = 0; return false }
        return true
    }
    function step() {
        var now = nowS(), dt = Math.min(.1, now - lastT), shut = dt * .5
        lastT = now
        var moving = spring(ld, dt)
        moving = spring(tp, dt) || moving
        dial.load = ld.x; dial.loadFrom = ld.x - ld.v * shut
        dial.temp = tp.x; dial.tempFrom = tp.x - tp.v * shut
        var was = geiger.busy
        var busy = geiger.step(now, dt)
        geiger.shut = shut
        if (busy || was) { ticks++; if (ticks % 2 === 0 || !busy) geiger.paintTape(now) }
        animating = moving || busy
    }
    Timer {
        id: tick
        interval: 42                       // 24 frames a second, like the web cards
        repeat: true
        running: root.host.active && root.animating && root.present
        onRunningChanged: if (running) root.lastT = root.nowS()
        onTriggered: root.step()
    }
    Connections {
        target: root.host
        function onActiveChanged() {      // shown again: counts that never happened are forgotten
            if (root.host.active) { geiger.reset(root.nowS()); root.animating = true }
        }
    }

    function tnorm(t) { return t === null || t === undefined ? 0 : F.clamp((t - 20) / 80) }
    function ingest(d) {
        if (!d) return
        var c = d.cpu, g = null, i
        sampleMs = (d.interval || .5) * 1000
        var iv = sampleMs / 1000, r = {}
        if (isCpu) {
            if (!c) return
            var u = (c.util || 0) / 100
            ld.t = u; tempC = c.temp; tp.t = tnorm(c.temp); geiger.u = u
            nameText = c.name || "—"
            subText = c.cores ? c.cores + " cores · " + c.threads + " threads" : c.threads + " threads"
            r.a = [c.freq_mhz ? F.fmt(c.freq_mhz / 1000, 2) : "—", c.freq_mhz ? "GHz" : ""]
            r.b = [F.fmt(c.power_w, 0), c.power_w === null || c.power_w === undefined ? "" : "W"]
            r.c = [c.load ? c.load.map(function (x) { return x.toFixed(1) }).join(" ") : "—", ""]
            var pc = c.per_core || []
            if (pc.length) {
                var bi = 0, hi = 0, k
                for (k = 0; k < pc.length; k++) { if (pc[k] > pc[bi]) bi = k; if (pc[k] > 50) hi++ }
                r.d = ["#" + bi + " · " + F.fmt(pc[bi]), "%"]
                threadsCap = hi + " of " + pc.length + " above 50%"
                var v = [], pk = peaks.length === pc.length ? peaks.slice() : []
                for (k = 0; k < pc.length; k++) {
                    var x = pc[k] / 100
                    v.push(x); pk[k] = pk[k] === undefined || x >= pk[k] ? x : Math.max(x, pk[k] - .06)
                }
                cores = v; peaks = pk
            }
            F.push(hist, c.util || 0, 120)
            peakText = "peak " + Math.round(Math.max.apply(null, hist)) + "%"
            procs = (c.procs || []).slice(0, 10)
            procCap = procs.length ? procs[0].name + " · " + F.fmt(procs[0].util) + "%" : ""
        } else {
            var gs = d.gpus || []
            for (i = 0; i < gs.length; i++) if (!gs[i].error) { g = gs[i]; break }
            present = !!g
            if (!g) {
                nameText = gs[0] && gs[0].name || "No GPU detected"; subText = "—"; geiger.u = 0; return
            }
            var gu = (g.util || 0) / 100, mu = (g.mem_util || 0) / 100
            ld.t = gu; tempC = g.temp; tp.t = tnorm(g.temp); geiger.u = Math.max(gu, mu * .8)
            nameText = g.name
            var sub = []
            if (g.vram_total_mb) sub.push(Math.round(g.vram_total_mb / 1024) + " GB")
            if (g.bus_width) sub.push(g.bus_width + "-bit bus")
            sub.push(g.vendor)
            subText = sub.join(" · ")
            r.a = [F.fmt(g.clock_mhz), g.clock_mhz === null || g.clock_mhz === undefined ? "" : "MHz"]
            r.b = [F.fmt(g.power_w), g.power_w === null || g.power_w === undefined ? "" : g.power_limit_w ? "/ " + F.fmt(g.power_limit_w) + " W" : "W"]
            r.c = [F.fmt(g.fan_pct), g.fan_pct === null || g.fan_pct === undefined ? "" : "%"]
            if (g.vram_total_mb) {
                r.d = [F.fmt(g.vram_used_mb / 1024, 1), "/ " + F.fmt(g.vram_total_mb / 1024, 1) + " GB"]
                vramFrac = g.vram_used_mb / g.vram_total_mb
            }
            F.push(hist, g.util || 0, 120)
            peakText = "peak " + Math.round(Math.max.apply(null, hist)) + "%"
            F.push(bwHist, g.bw_gbps !== null && g.bw_gbps !== undefined ? g.bw_gbps : (g.mem_util || 0), 120)
            if (g.bw_gbps !== null && g.bw_gbps !== undefined) {
                // Scales to recent traffic, not the card's peak: grows at once, shrinks slowly, never below 0.5 GB/s.
                var target = F.niceCeil(Math.max(.5, Math.max.apply(null, bwHist) * 1.15))
                bwMax = !bwMax || target > bwMax ? target : Math.max(target, bwMax * .97)
                var rt = F.rate(g.bw_gbps)
                bwValue = rt.v; bwUnit = rt.u; bwFrac = g.bw_gbps / bwMax
                bwPeak = g.bw_peak_gbps ? "peak " + F.rateText(g.bw_peak_gbps) : ""
                bwNote = "graph and bar to " + F.rateText(bwMax) +
                    (g.bw_display_gbps > .01 ? " · monitor refresh (" + F.rateText(g.bw_display_gbps) + ") not counted" : "")
            } else {
                bwMax = 100
                bwValue = g.mem_util === null || g.mem_util === undefined ? "—" : F.fmt(g.mem_util) + "%"; bwUnit = ""
                bwFrac = (g.mem_util || 0) / 100; bwPeak = ""
                bwNote = "Memory-controller busy" + (g.mem_util_display > .05 ? ", monitor refresh (" + F.fmt(g.mem_util_display) + "%) not counted" : "")
            }
            procs = (g.procs || []).slice(0, 10)
            procCap = procs.length ? procs[0].name + " · " + F.fmt(procs[0].util) + "%" : ""
        }
        rows = r
        rev++
        geiger.schedule(iv, nowS())
        animating = true
        if (!tick.running && host.active) lastT = nowS()
    }
    Connections {
        target: root.feed
        function onSnapshotChanged() { root.ingest(root.feed.snapshot) }
    }
    Component.onCompleted: ingest(feed.snapshot)

    // ---- reporting the wanted size to the window
    readonly property real contentH: body.y + body.implicitHeight + pad
    implicitHeight: Math.ceil(contentH)
    function report() { host.setSize(gauge ? 208 : 0, implicitHeight, sk.radius) }
    onImplicitHeightChanged: Qt.callLater(report)
    onGaugeChanged: Qt.callLater(report)
    onSkChanged: Qt.callLater(report)

    Backdrop { anchors.fill: parent; sk: root.sk; accent: root.acc }

    // Drag anywhere on the card to move it; right-click for the menu.
    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.LeftButton | Qt.RightButton
        onPressed: function (m) {
            if (m.button === Qt.RightButton) { var p = mapToGlobal(m.x, m.y); host.showMenu(Math.round(p.x), Math.round(p.y)) }
            else host.startMove()
        }
    }
    HoverHandler { id: cardHover }

    Column {
        id: body
        x: root.pad; y: root.pad
        width: root.inner
        spacing: 12

        // ---- head
        Item {
            width: parent.width; height: headCol.implicitHeight
            Column {
                id: headCol
                width: parent.width - (root.compact ? 100 : 0) - 44
                spacing: 1
                Text {
                    visible: !root.gauge
                    text: root.isCpu ? "PROCESSOR" : "GRAPHICS"
                    font.pixelSize: 10; font.weight: Font.DemiBold; font.letterSpacing: 1.4
                    font.family: root.sk.ui; color: Skins.qt(root.sk.ink3)
                }
                Clip {
                    width: parent.width; height: 24
                    text: root.nameText; pixelSize: 17; weight: Font.DemiBold
                    family: root.sk.ui; color: Skins.qt(root.sk.ink)
                }
                Text {
                    visible: !root.gauge
                    text: root.subText; font.pixelSize: 12
                    font.family: root.sk.ui; color: Skins.qt(root.sk.ink2)
                }
            }
            Spark {
                visible: root.compact
                anchors.right: led.left; anchors.rightMargin: 10; anchors.verticalCenter: parent.verticalCenter
                width: 90; height: 30
                sk: root.sk; acc: root.acc; series: root.hist; rev: root.rev
            }
            Row {
                id: led
                anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                anchors.rightMargin: root.gauge ? 0 : (cardHover.hovered ? 0 : 0)
                spacing: 5
                visible: !(cardHover.hovered && !root.gauge)
                Rectangle {
                    width: 10; height: 10; radius: 5; anchors.verticalCenter: parent.verticalCenter
                    color: Skins.qt(root.acc); opacity: .18 + .82 * geiger.lit
                    border.width: 1; border.color: Skins.qt(root.sk.hair)
                }
                Text {
                    visible: !root.gauge; anchors.verticalCenter: parent.verticalCenter
                    text: "ACT"; font.pixelSize: 9; font.letterSpacing: 1.2; font.weight: Font.DemiBold
                    font.family: root.sk.ui; color: Skins.qt(root.sk.ink3)
                }
            }
            Row {
                anchors.right: parent.right; anchors.top: parent.top
                spacing: 4
                opacity: cardHover.hovered ? 1 : 0
                visible: opacity > 0
                Behavior on opacity { NumberAnimation { duration: 120 } }
                CtlButton { id: bm; sk: root.sk; groupHover: bm.hov || bc.hov; onClicked: { var p = mapToGlobal(0, height); root.host.showMenu(Math.round(p.x), Math.round(p.y)) } }
                CtlButton { id: bc; sk: root.sk; isClose: true; groupHover: bm.hov || bc.hov; onClicked: root.host.dismiss() }
            }
        }

        // ---- instrument
        Flow {
            id: inst
            visible: root.present
            width: parent.width; spacing: 14
            Dial {
                id: dial
                sk: root.sk; acc: root.acc; zones: root.zones; tempC: root.tempC
                x: root.narrow || root.gauge ? (parent.width - width) / 2 : 0
            }
            Column {
                visible: !root.gauge
                width: root.narrow ? parent.width : parent.width - dial.width - inst.spacing
                spacing: 0
                ReadRow { width: parent.width; sk: root.sk; acc: root.acc; label: root.isCpu ? "Clock" : "Core clock"; value: root.rows.a ? root.rows.a[0] : "—"; unit: root.rows.a ? root.rows.a[1] : "" }
                ReadRow { width: parent.width; sk: root.sk; acc: root.acc; label: root.isCpu ? "Package power" : "Board power"; value: root.rows.b ? root.rows.b[0] : "—"; unit: root.rows.b ? root.rows.b[1] : "" }
                ReadRow { width: parent.width; sk: root.sk; acc: root.acc; label: root.isCpu ? "Load avg" : "Fan"; value: root.rows.c ? root.rows.c[0] : "—"; unit: root.rows.c ? root.rows.c[1] : "" }
                ReadRow {
                    width: parent.width; sk: root.sk; acc: root.acc; label: root.isCpu ? "Busiest thread" : "VRAM"
                    meter: !root.isCpu; frac: root.vramFrac
                    value: root.rows.d ? root.rows.d[0] : "—"; unit: root.rows.d ? root.rows.d[1] : ""
                }
                Item {
                    width: parent.width; height: 8 + 14 + 4 + 38
                    Text { y: 8; text: "LOAD, LAST 60 S"; font.pixelSize: 10; font.letterSpacing: 1.1; font.family: root.sk.ui; color: Skins.qt(root.sk.ink3) }
                    Text { y: 8; anchors.right: parent.right; text: root.peakText; font.pixelSize: 11; font.family: root.sk.num; color: Skins.qt(root.sk.ink2) }
                    Spark { y: 26; width: parent.width; height: 38; sk: root.sk; acc: root.acc; series: root.hist; rev: root.rev }
                }
            }
        }
        Text {
            visible: !root.present
            width: parent.width; wrapMode: Text.WordWrap; lineHeight: 1.3
            text: "No GPU telemetry found. For NVIDIA cards install nvidia-ml-py; AMD cards need the amdgpu driver."
            font.pixelSize: 12; font.family: root.sk.ui; color: Skins.qt(root.sk.ink2)
        }

        GeigerStrip {
            id: geiger
            visible: root.present
            width: parent.width
            sk: root.sk; acc: root.acc; host: root.host; key: root.cardKey
        }

        // ---- per thread (CPU) / VRAM throughput (GPU)
        Column {
            visible: root.isCpu && !root.compact && !root.gauge && root.cores.length > 0
            width: parent.width; spacing: 6
            Item {
                width: parent.width; height: 14
                Text { text: "PER THREAD"; font.pixelSize: 10; font.letterSpacing: 1.1; font.weight: Font.DemiBold; font.family: root.sk.ui; color: Skins.qt(root.sk.ink2) }
                Text { anchors.right: parent.right; text: root.threadsCap; font.pixelSize: 11; font.family: root.sk.num; color: Skins.qt(root.sk.ink3) }
            }
            Cores { width: parent.width; height: 70; sk: root.sk; acc: root.acc; values: root.cores; peaks: root.peaks; interval: root.sampleMs }
        }
        Column {
            visible: !root.isCpu && root.present && !root.compact && !root.gauge
            width: parent.width; spacing: 6
            Item {
                width: parent.width; height: 18
                Text { text: "VRAM THROUGHPUT"; font.pixelSize: 10; font.letterSpacing: 1.1; font.weight: Font.DemiBold; font.family: root.sk.ui; color: Skins.qt(root.sk.ink2) }
                Row {
                    anchors.right: parent.right; spacing: 3
                    Text { text: root.bwValue; font.pixelSize: 14; font.family: root.sk.num; color: Skins.qt(root.sk.ink) }
                    Text { text: root.bwUnit; y: 3; font.pixelSize: 11; font.family: root.sk.ui; color: Skins.qt(root.sk.ink3) }
                }
            }
            Rectangle {
                width: parent.width; height: 5; radius: 2.5
                color: Skins.qt([root.sk.fg[0], root.sk.fg[1], root.sk.fg[2], .08])
                Rectangle {
                    height: parent.height; radius: 2.5; width: parent.width * F.clamp(root.bwFrac); color: Skins.qt(root.acc)
                    Behavior on width { NumberAnimation { duration: 450; easing.type: Easing.OutCubic } }
                }
            }
            Spark { width: parent.width; height: 30; sk: root.sk; acc: root.acc; series: root.bwHist; maxValue: root.bwMax; rev: root.rev }
            Item {
                width: parent.width; height: 14
                Text { width: parent.width - 80; elide: Text.ElideRight; text: root.bwNote; font.pixelSize: 10; font.family: root.sk.ui; color: Skins.qt(root.sk.ink3) }
                Text { anchors.right: parent.right; text: root.bwPeak; font.pixelSize: 10; font.family: root.sk.num; color: Skins.qt(root.sk.ink3) }
            }
        }

        // ---- top processes: folded by default; the sampler scans /proc only while this is open
        Column {
            visible: root.present && !root.compact && !root.gauge
            width: parent.width; spacing: 6
            Item {
                width: parent.width; height: 20
                Text {
                    id: caret; text: "›"; font.pixelSize: 16; color: Skins.qt(root.sk.ink2)
                    rotation: root.host.fold ? 90 : 0; anchors.verticalCenter: parent.verticalCenter
                    Behavior on rotation { NumberAnimation { duration: 140 } }
                }
                Text {
                    x: 16; anchors.verticalCenter: parent.verticalCenter
                    text: "TOP PROCESSES"; font.pixelSize: 10; font.letterSpacing: 1.1; font.weight: Font.DemiBold
                    font.family: root.sk.ui; color: Skins.qt(root.sk.ink2)
                }
                Text {
                    anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                    width: parent.width * .5; horizontalAlignment: Text.AlignRight; elide: Text.ElideRight
                    text: root.procCap; font.pixelSize: 11; font.family: root.sk.num; color: Skins.qt(root.sk.ink3)
                }
                MouseArea { anchors.fill: parent; onClicked: root.host.fold = !root.host.fold }
            }
            ProcTable {
                visible: root.host.fold
                width: parent.width; sk: root.sk; acc: root.acc; rows: root.procs
                colUtil: root.isCpu ? "CPU" : "GPU"; colMem: root.isCpu ? "RAM" : "VRAM"; memKey: root.isCpu ? "mem_mb" : "vram_mb"
                empty: root.isCpu ? "No process data." : "No processes are using the GPU."
                opacity: visible ? 1 : 0
                Behavior on opacity { NumberAnimation { duration: 160 } }
            }
        }
    }

    // Resize handles on the left and right edges.
    MouseArea {
        width: 6; height: parent.height; cursorShape: Qt.SizeHorCursor; visible: !root.gauge
        onPressed: root.host.startResize(1)
    }
    MouseArea {
        x: parent.width - 6; width: 6; height: parent.height; cursorShape: Qt.SizeHorCursor; visible: !root.gauge
        onPressed: root.host.startResize(2)
    }
}
