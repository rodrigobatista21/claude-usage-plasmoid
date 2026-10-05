.pragma library

function fmtTokens(n) {
    if (n === null || n === undefined) return "-";
    if (n >= 1000000) return (n / 1000000).toFixed(2) + "M";
    if (n >= 1000) return (n / 1000).toFixed(1) + "K";
    return String(Math.round(n));
}

function fmtMoney(v) {
    if (v === null || v === undefined) return "-";
    return "US$ " + Number(v).toFixed(2);
}

function fmtPct(p) {
    if (p === null || p === undefined) return "-";
    return Math.round(p) + "%";
}

function fmtDuration(totalSeconds) {
    if (totalSeconds === null || totalSeconds === undefined) return "-";
    var s = Math.max(0, Math.floor(totalSeconds));
    var d = Math.floor(s / 86400);
    var h = Math.floor((s % 86400) / 3600);
    var m = Math.floor((s % 3600) / 60);
    if (d > 0) return d + "d " + h + "h";
    if (h > 0) return h + "h " + (m < 10 ? "0" : "") + m + "min";
    return m + "min";
}

function pad2(n) { return (n < 10 ? "0" : "") + n; }

function clock(epoch) {
    if (!epoch) return "-";
    var dt = new Date(epoch * 1000);
    return pad2(dt.getHours()) + ":" + pad2(dt.getMinutes());
}

function dateClock(epoch) {
    if (!epoch) return "-";
    var dt = new Date(epoch * 1000);
    return pad2(dt.getDate()) + "/" + pad2(dt.getMonth() + 1) + " " + clock(epoch);
}

// "há 2 min", "há 1h 05min", "agora"
function relTime(epoch, nowSec) {
    if (!epoch) return "-";
    var diff = nowSec - epoch;
    if (diff < 60) return "agora";
    return "há " + fmtDuration(diff);
}

// Burn-rate forecast from the sample history of one bucket.
// Returns "" when there isn't enough signal to say anything useful.
function forecast(history, pct, resetsAt, nowSec) {
    if (!history || !history.samples || history.samples.length < 2 || !resetsAt) return "";
    var first = history.samples[0];
    var last = history.samples[history.samples.length - 1];
    var elapsedH = (last[0] - first[0]) / 3600;
    if (elapsedH < 10 / 60) return "";
    var rate = (last[1] - first[1]) / elapsedH;   // pct points per hour
    if (rate <= 0.5) return "";
    var hoursToLimit = (100 - last[1]) / rate;
    var eta = nowSec + hoursToLimit * 3600;
    if (eta < resetsAt) return "nesse ritmo, esgota em " + fmtDuration(hoursToLimit * 3600);
    return "nesse ritmo, não esgota antes do reset";
}
