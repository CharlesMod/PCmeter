.pragma library
// Number formatting and small helpers shared by the cards (ported from web/index.html).

function clamp(v, a, b) { a = a === undefined ? 0 : a; b = b === undefined ? 1 : b; return Math.max(a, Math.min(b, v)) }
// A temperature with one decimal ("55.5°"); the dial readouts and the chart axes stay whole numbers.
function temp(v) { return v === null || v === undefined || isNaN(v) ? "—" : Number(v).toFixed(1) + "°" }
// A signed change to one decimal: "+1.2°", "−0.4°", "±0.0°".
function dtemp(d) { var r = Math.round(d * 10) / 10; return (r === 0 ? "±" : r > 0 ? "+" : "−") + Math.abs(r).toFixed(1) + "°" }
function thou(v) { return v === null || v === undefined || isNaN(v) ? "—" : String(Math.round(v)).replace(/\B(?=(\d{3})+(?!\d))/g, ",") }
function fmt(v, d) { return v === null || v === undefined || isNaN(v) ? "—" : Number(v).toFixed(d || 0) }

// A byte rate (given in GB/s) in the unit that reads best: MB/s below 1 GB/s, GB/s from there up.
function rate(gbps) {
    if (gbps === null || gbps === undefined || isNaN(gbps)) return { v: "—", u: "" }
    if (gbps < 1) { var mb = gbps * 1000; return { v: mb < 10 ? mb.toFixed(1) : String(Math.round(mb)), u: "MB/s" } }
    return { v: gbps < 10 ? gbps.toFixed(2) : gbps < 100 ? gbps.toFixed(1) : String(Math.round(gbps)), u: "GB/s" }
}
function rateText(gbps) { var r = rate(gbps); return r.v + " " + r.u }
// 1, 2 or 5 times a power of ten, at or above v
function niceCeil(v) {
    var e = Math.pow(10, Math.floor(Math.log10(v))), m = [1, 2, 5, 10]
    for (var i = 0; i < m.length; i++) if (m[i] * e >= v - 1e-12) return m[i] * e
    return v
}
function mem(mb) { return mb >= 1024 ? fmt(mb / 1024, 1) + " GB" : fmt(mb) + " MB" }
function push(arr, v, max) { arr.push(v); if (arr.length > max) arr.shift() }

function poisson(m) {
    if (m <= 0) return 0
    if (m > 30) {
        var g = Math.sqrt(-2 * Math.log(Math.random() || 1e-9)) * Math.cos(2 * Math.PI * Math.random())
        return Math.max(0, Math.round(m + Math.sqrt(m) * g))
    }
    var L = Math.exp(-m), k = 0, p = 1
    do { k++; p *= Math.random() } while (p > L)
    return k - 1
}

// Weights for QML's Font.Weight from CSS numbers
function weight(w) { return w <= 300 ? Font.Light : w <= 400 ? Font.Normal : w <= 500 ? Font.Medium : Font.DemiBold }
