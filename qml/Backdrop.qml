import QtQuick
import "Skins.js" as Skins

// The card's surface, drawn once per skin and size (never per frame): the skin's wallpaper at its pane
// opacity, the card color over it, the accent glow, the edge highlights and the border, all inside the
// rounded corners. The blur behind it is the compositor's (see wlblur.py), not drawn here.
Canvas {
    id: cv
    property var sk
    property var accent
    renderTarget: Canvas.Image
    onSkChanged: requestPaint()
    onAccentChanged: requestPaint()
    onWidthChanged: requestPaint()
    onHeightChanged: requestPaint()

    function radial(ctx, w, h, rx, ry, px, py, c) {
        ctx.save()
        ctx.translate(w * px / 100, h * py / 100)
        ctx.scale(w * rx / 100, h * ry / 100)
        var g = ctx.createRadialGradient(0, 0, 0, 0, 0, 1)
        g.addColorStop(0, Skins.css(c))
        g.addColorStop(.7, Skins.css([c[0], c[1], c[2], 0]))
        ctx.fillStyle = g
        ctx.fillRect(-w, -h, w * 2, h * 2)    // far larger than the unit circle: it fades to nothing at .7
        ctx.restore()
    }
    function linear(ctx, w, h, angle, stops) {
        var a = angle * Math.PI / 180, dx = Math.sin(a), dy = -Math.cos(a)
        var len = Math.abs(w * dx) + Math.abs(h * dy)
        var g = ctx.createLinearGradient(w / 2 - dx * len / 2, h / 2 - dy * len / 2, w / 2 + dx * len / 2, h / 2 + dy * len / 2)
        for (var i = 0; i < stops.length; i++) g.addColorStop(stops[i][0], Skins.css(stops[i][1]))
        return g
    }

    onPaint: {
        var w = width, h = height
        if (w < 2 || h < 2 || !sk) return
        var ctx = getContext("2d")
        ctx.reset()
        var r = Math.min(sk.radius, w / 2, h / 2)
        ctx.beginPath(); ctx.roundedRect(0, 0, w, h, r, r); ctx.save(); ctx.clip()

        var wp = sk.wallpaper
        ctx.globalAlpha = sk.pane
        ctx.fillStyle = Skins.css(wp.base); ctx.fillRect(0, 0, w, h)
        if (wp.lin) { ctx.fillStyle = linear(ctx, w, h, wp.lin.angle, wp.lin.stops); ctx.fillRect(0, 0, w, h) }
        for (var i = wp.radials.length - 1; i >= 0; i--) {
            var q = wp.radials[i]
            radial(ctx, w, h, q[0], q[1], q[2], q[3], q[4])
        }
        if (wp.scan) {
            ctx.fillStyle = Skins.css(wp.scan)
            for (var y = 2; y < h; y += 3) ctx.fillRect(0, h - y - 1, w, 1)    // 0deg: lines counted from the bottom
        }
        if (wp.hex) {
            ctx.strokeStyle = Skins.css(wp.hex); ctx.lineWidth = 1
            ctx.beginPath()
            for (var ty = 0; ty < h; ty += 100) for (var tx = 0; tx < w; tx += 56) {
                ctx.moveTo(tx + 28, ty + 66); ctx.lineTo(tx, ty + 50); ctx.lineTo(tx, ty + 16)
                ctx.lineTo(tx + 28, ty); ctx.lineTo(tx + 56, ty + 16); ctx.lineTo(tx + 56, ty + 50)
                ctx.lineTo(tx + 28, ty + 66); ctx.lineTo(tx + 28, ty + 100)
            }
            ctx.stroke()
        }
        ctx.globalAlpha = 1

        var bg = sk.cardBg
        ctx.fillStyle = bg.stops ? linear(ctx, w, h, bg.angle, bg.stops) : Skins.css(bg)
        ctx.fillRect(0, 0, w, h)
        if (sk.cardGlow > 0 && accent) {
            ctx.save()
            ctx.translate(w * .22, h * .26); ctx.scale(w * .70, h * .45)
            var g = ctx.createRadialGradient(0, 0, 0, 0, 0, 1)
            g.addColorStop(0, Skins.css([accent[0], accent[1], accent[2], sk.cardGlow]))
            g.addColorStop(.7, Skins.css([accent[0], accent[1], accent[2], 0]))
            ctx.fillStyle = g; ctx.fillRect(-w, -h, w * 2, h * 2)
            ctx.restore()
        }
        if (sk.shadow.top) { ctx.fillStyle = Skins.css(sk.shadow.top); ctx.fillRect(r * .6, 1, w - r * 1.2, 1) }
        if (sk.shadow.bottom) { ctx.fillStyle = Skins.css(sk.shadow.bottom); ctx.fillRect(r * .6, h - 2, w - r * 1.2, 1) }
        ctx.restore()

        ctx.strokeStyle = Skins.css(sk.edge); ctx.lineWidth = 1
        ctx.beginPath(); ctx.roundedRect(.5, .5, w - 1, h - 1, Math.max(0, r - .5), Math.max(0, r - .5)); ctx.stroke()
    }
}
