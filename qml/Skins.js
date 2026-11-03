.pragma library
// The skins: every color, face, radius and glass value is a token, as in web/index.html.
// Colors are [r, g, b, a] (0-255, a 0-1) so the canvases and the items read the same table.

function rgba(r, g, b, a) { return [r, g, b, a === undefined ? 1 : a] }
function hex(h, a) {
    var n = parseInt(h.slice(1), 16)
    return [n >> 16 & 255, n >> 8 & 255, n & 255, a === undefined ? 1 : a]
}
function qt(c, alpha) { return Qt.rgba(c[0] / 255, c[1] / 255, c[2] / 255, c[3] * (alpha === undefined ? 1 : alpha)) }
function css(c, alpha) { return "rgba(" + c[0] + "," + c[1] + "," + c[2] + "," + (c[3] * (alpha === undefined ? 1 : alpha)) + ")" }
function mix(c, alpha) { return [c[0], c[1], c[2], alpha] }

var UI_SYS = ["Onest", "Noto Sans", "DejaVu Sans", "sans-serif"]
var NUM_SYS = ["Martian Mono", "DejaVu Sans Mono", "monospace"]

function make(o) {
    var fg = o.fg || [255, 255, 255]
    var base = {
        fg: fg, fUi: UI_SYS, fNum: NUM_SYS, glow: 1, needle: hex("#ffffff"), pane: 0.85,
        warn: hex("#ffcf5c"), hot: hex("#ff6a6a"), ok: hex("#72e3a6"),
        shadow: { top: null, bottom: null }, cardGlow: 0,
        btn: { w: 24, h: 24, r: 8, bg: 0.06, bgHover: 0.14, closeHover: null, closeHoverFg: [255, 255, 255, 1] },
        light: false
    }
    for (var k in o) base[k] = o[k]
    return base
}

var skins = {
    glass: make({
        wallpaper: { base: hex("#080b12"), radials: [
            [55, 45, 12, 8, hex("#1b2152")], [45, 45, 78, 12, hex("#0b3640")], [50, 50, 92, 92, hex("#3a2015")]] },
        ink: hex("#e8edf5"), ink2: rgba(232, 237, 245, .64), ink3: rgba(232, 237, 245, .40),
        hair: rgba(255, 255, 255, .08), edge: rgba(255, 255, 255, .14), well: rgba(4, 7, 12, .32),
        cpu: hex("#8fd6ff"), gpu: hex("#ffb067"), cool: hex("#5fb4ff"), warn: hex("#ffcf5c"), hot: hex("#ff6a6a"), ok: hex("#72e3a6"),
        cardBg: { angle: 170, stops: [[0, rgba(255, 255, 255, .10)], [.45, rgba(255, 255, 255, .035)], [1, rgba(255, 255, 255, .05)]] },
        shadow: { top: rgba(255, 255, 255, .20), bottom: rgba(0, 0, 0, .30) },
        cardGlow: .13, radius: 24, needle: hex("#ffffff"), disc: rgba(6, 9, 15, .78),
        faceHi: rgba(255, 255, 255, .07), faceLo: rgba(0, 0, 0, .22), glow: 1, pane: .80
    }),
    matrix: make({
        fg: [90, 255, 140],
        wallpaper: { base: hex("#010603"), scan: rgba(0, 255, 90, .035), radials: [[70, 50, 50, 0, hex("#06301a")]] },
        ink: hex("#c9ffd8"), ink2: rgba(110, 255, 150, .72), ink3: rgba(70, 220, 115, .48),
        hair: rgba(57, 255, 106, .14), edge: rgba(57, 255, 106, .38), well: rgba(0, 22, 8, .7),
        cpu: hex("#39ff6a"), gpu: hex("#00ffd0"), cool: hex("#00e5ff"), warn: hex("#e6ff3a"), hot: hex("#ff3a5c"), ok: hex("#39ff6a"),
        fUi: ["Martian Mono", "DejaVu Sans Mono", "monospace"],
        cardBg: rgba(0, 14, 5, .86), cardGlow: .08, radius: 4, needle: hex("#e4ffe9"), disc: rgba(0, 10, 3, .92),
        faceHi: rgba(57, 255, 106, .06), faceLo: rgba(0, 0, 0, .3), glow: 1.4, pane: .90,
        btn: { w: 22, h: 20, r: 2, bg: 0, bgFixed: rgba(0, 20, 6, .8), bgHover: .14, border: true, closeHover: "hot", closeHoverFg: [0, 0, 0, 1] }
    }),
    hive: make({
        wallpaper: { base: hex("#121317"), hex: rgba(255, 197, 51, .07), radials: [
            [60, 50, 15, 0, rgba(255, 197, 51, .16)], [50, 50, 100, 100, rgba(255, 120, 30, .12)]] },
        fg: [255, 236, 200],
        ink: hex("#f4efe3"), ink2: rgba(244, 239, 227, .64), ink3: rgba(244, 239, 227, .40),
        hair: rgba(255, 197, 51, .10), edge: rgba(255, 197, 51, .24), well: rgba(0, 0, 0, .35),
        cpu: hex("#ffc533"), gpu: hex("#ff8a2a"), cool: hex("#4fb3ff"), warn: hex("#ffd75e"), hot: hex("#ff5b4a"), ok: hex("#8bd46e"),
        cardBg: { angle: 170, stops: [[0, rgba(44, 39, 29, .86)], [1, rgba(22, 22, 26, .88)]] },
        shadow: { top: rgba(255, 220, 140, .12), bottom: null },
        cardGlow: .10, radius: 14, needle: hex("#fff6dc"), disc: rgba(12, 12, 14, .88),
        faceHi: rgba(255, 255, 255, .07), faceLo: rgba(0, 0, 0, .22), glow: 1, pane: .88,
        btn: { w: 24, h: 24, r: 6, bg: 0.06, bgHover: 0.14, hoverBorder: true, closeHover: null, closeHoverFg: [255, 255, 255, 1] }
    }),
    windows: make({
        wallpaper: { base: hex("#0a1430"), radials: [
            [55, 60, 72, 42, hex("#2a63c9")], [45, 50, 28, 85, hex("#0e3a8a")], [40, 40, 90, 90, hex("#6a3fb5")]] },
        ink: hex("#ffffff"), ink2: rgba(255, 255, 255, .79), ink3: rgba(255, 255, 255, .54),
        hair: rgba(255, 255, 255, .084), edge: rgba(255, 255, 255, .10), well: rgba(0, 0, 0, .26),
        cpu: hex("#60cdff"), gpu: hex("#c4a7ff"), cool: hex("#4fa8ff"),
        fUi: ["Segoe UI Variable Text", "Segoe UI", "Noto Sans", "DejaVu Sans", "sans-serif"],
        fNum: ["Cascadia Mono", "Cascadia Code", "Consolas", "DejaVu Sans Mono", "monospace"],
        cardBg: rgba(32, 32, 32, .72), shadow: { top: rgba(255, 255, 255, .07), bottom: null },
        cardGlow: 0, radius: 8, disc: rgba(28, 28, 28, .94),
        faceHi: rgba(255, 255, 255, .04), faceLo: rgba(0, 0, 0, .18), glow: .6, pane: .78,
        btn: { w: 30, h: 24, r: 4, bg: 0, bgHover: .09, closeHover: hex("#c42b1c"), closeHoverFg: [255, 255, 255, 1] }
    }),
    mac: make({
        fg: [0, 0, 0], light: true,
        wallpaper: { base: hex("#a9a3e6"), radials: [
            [60, 55, 15, 10, hex("#ffd3b0")], [55, 55, 85, 20, hex("#e3a6d8")], [70, 60, 60, 100, hex("#6f86e6")]] },
        ink: hex("#1d1d1f"), ink2: rgba(29, 29, 31, .64), ink3: rgba(29, 29, 31, .44),
        hair: rgba(0, 0, 0, .08), edge: rgba(255, 255, 255, .6), well: rgba(255, 255, 255, .38),
        cpu: hex("#007aff"), gpu: hex("#ff9500"), cool: hex("#0a84ff"), warn: hex("#c77700"), hot: hex("#ff3b30"), ok: hex("#1f9d48"),
        fUi: ["SF Pro Text", "Helvetica Neue", "Noto Sans", "DejaVu Sans", "sans-serif"],
        fNum: ["SF Pro Rounded", "Noto Sans", "DejaVu Sans", "sans-serif"],
        cardBg: rgba(255, 255, 255, .55), shadow: { top: rgba(255, 255, 255, .75), bottom: null },
        cardGlow: 0, radius: 22, needle: hex("#1d1d1f"), disc: rgba(255, 255, 255, .88),
        faceHi: rgba(255, 255, 255, .55), faceLo: rgba(0, 0, 0, .035), glow: .5, pane: .76,
        btn: { w: 12, h: 12, r: 6, mac: true, closeHover: null, closeHoverFg: [0, 0, 0, .6] }
    }),
    linux: make({
        wallpaper: { base: hex("#2c001e"), lin: { angle: 160, stops: [[0, hex("#2c001e")], [.6, hex("#3b0a2f")], [1, hex("#4a1530")]] }, radials: [
            [70, 60, 88, 100, rgba(233, 84, 32, .55)], [70, 70, 0, 0, hex("#5e2750")]] },
        ink: hex("#f7f7f7"), ink2: rgba(247, 247, 247, .66), ink3: rgba(247, 247, 247, .42),
        hair: rgba(255, 255, 255, .08), edge: rgba(255, 255, 255, .10), well: rgba(0, 0, 0, .3),
        cpu: hex("#e95420"), gpu: hex("#d986cf"), cool: hex("#4ea5f2"),
        fUi: ["Ubuntu", "Cantarell", "Noto Sans", "DejaVu Sans", "sans-serif"],
        fNum: ["Ubuntu Mono", "DejaVu Sans Mono", "monospace"],
        cardBg: rgba(44, 44, 44, .94), shadow: { top: rgba(255, 255, 255, .05), bottom: null },
        cardGlow: 0, radius: 12, disc: hex("#1f1f1f"),
        faceHi: rgba(255, 255, 255, .04), faceLo: rgba(0, 0, 0, .2), glow: .7, pane: .88,
        btn: { w: 20, h: 20, r: 10, bg: .12, bgHover: .22, closeBg: hex("#e95420"), closeHoverBg: hex("#f0733e"), closeFg: [255, 255, 255, 1] }
    }),
    dark: make({
        wallpaper: { base: hex("#0d0e10"), radials: [] },
        ink: hex("#ececee"), ink2: rgba(236, 236, 238, .6), ink3: rgba(236, 236, 238, .38),
        hair: rgba(255, 255, 255, .07), edge: rgba(255, 255, 255, .08), well: rgba(255, 255, 255, .025),
        cpu: hex("#9cc4ff"), gpu: hex("#ffbf8f"), cool: hex("#6aa8ff"),
        cardBg: hex("#16171a"), cardGlow: 0, radius: 16, needle: hex("#f2f2f2"), disc: hex("#101113"),
        faceHi: rgba(255, 255, 255, .03), faceLo: rgba(0, 0, 0, .15), glow: .4, pane: .92
    }),
    white: make({
        fg: [17, 19, 24], light: true,
        wallpaper: { base: hex("#f3f4f6"), radials: [] },
        ink: hex("#111318"), ink2: rgba(17, 19, 24, .62), ink3: rgba(17, 19, 24, .42),
        hair: rgba(17, 19, 24, .08), edge: rgba(17, 19, 24, .08), well: rgba(17, 19, 24, .035),
        cpu: hex("#0a66ff"), gpu: hex("#f2541b"), cool: hex("#1a7cff"), warn: hex("#b77b00"), hot: hex("#dc2f2f"), ok: hex("#159a52"),
        cardBg: hex("#ffffff"), cardGlow: 0, radius: 18, needle: hex("#111318"), disc: hex("#ffffff"),
        faceHi: rgba(17, 19, 24, .015), faceLo: rgba(17, 19, 24, .04), glow: .3, pane: .92
    })
}

// The first family of a list that is installed (QML's `font.family` takes one name).
var installed = null
function pick(list) {
    if (installed === null) { installed = {}; Qt.fontFamilies().forEach(function (f) { installed[f] = true }) }
    for (var i = 0; i < list.length; i++) if (installed[list[i]]) return list[i]
    return list[list.length - 1]
}
function get(name) {
    var s = skins[name] || skins.glass
    if (s.ui === undefined) { s.ui = pick(s.fUi); s.num = pick(s.fNum) }
    return s
}
