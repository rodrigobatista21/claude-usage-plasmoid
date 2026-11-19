.pragma library

function fmtTokens(n) {
    if (n === null || n === undefined) return "-";
    if (n >= 1000000) return (n / 1000000).toFixed(2) + "M";
    if (n >= 1000) return (n / 1000).toFixed(1) + "K";
    return String(Math.round(n));
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

// Burn-rate forecast for one bucket, from how much it grew over the last
// lookbackSec (or since the window's first sample, when that is more
// recent). The history is a step function: a flat stretch is stored as its
// first and latest sample, so "the value back then" is the last sample at
// or before that moment. Returns "" when there isn't enough signal.
function forecast(history, pct, resetsAt, nowSec, lookbackSec) {
    if (!history || !history.samples || history.samples.length < 1 || !resetsAt) return "";
    if (pct === null || pct === undefined || resetsAt <= nowSec) return "";
    var samples = history.samples;
    var from = nowSec - lookbackSec;
    var base = samples[0];
    for (var i = 0; i < samples.length && samples[i][0] <= from; i++) base = samples[i];
    var elapsedH = (nowSec - base[0]) / 3600;
    if (elapsedH < 10 / 60) return "";
    var rate = (pct - base[1]) / elapsedH;   // pct points per hour
    if (rate <= 0) return "";
    var secondsToLimit = (100 - pct) / rate * 3600;
    var eta = nowSec + secondsToLimit;
    if (eta >= resetsAt) return "nesse ritmo, não esgota antes do reset";
    var sameDay = new Date(eta * 1000).toDateString() === new Date(nowSec * 1000).toDateString();
    return "nesse ritmo, esgota às " + (sameDay ? clock(eta) : dateClock(eta)) + " (em " + fmtDuration(secondsToLimit) + ")";
}
