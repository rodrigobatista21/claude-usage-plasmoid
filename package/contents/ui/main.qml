import QtQuick
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.plasma5support as P5Support
import org.kde.kirigami as Kirigami
import "js/format.js" as Fmt

PlasmoidItem {
    id: root

    // ---- raw cache -------------------------------------------------------------
    property var usage: ({})
    property bool cacheMissing: false
    property bool parseError: false
    property bool everLoaded: false
    property int nowSec: Math.floor(Date.now() / 1000)

    readonly property string cachePath: Plasmoid.configuration.cachePath
    // The engine keys sources by command string, so two instances (panel +
    // desktop) running the same `cat` would share one source and one poll
    // timer. The trailing shell comment makes each instance's source unique.
    readonly property string readerSource: "cat \"" + cachePath + "\" # claudeusage-" + Plasmoid.id
    property double lastReadAt: Date.now()
    readonly property int pollMs: Math.max(5, Plasmoid.configuration.pollIntervalSec) * 1000
    readonly property int staleAfterSec: Math.max(1, Plasmoid.configuration.staleAfterMin) * 60

    // ---- derived state -------------------------------------------------------
    readonly property var limits: (usage && usage.limits) ? usage.limits : ({})
    readonly property var fiveHour: limits.five_hour || null
    readonly property var sevenDay: limits.seven_day || null
    readonly property var extraLimits: {
        var out = [];
        for (var k in limits) {
            if (k === "five_hour" || k === "seven_day") continue;
            var e = limits[k];
            if (e && e.used_percentage !== undefined) out.push(Object.assign({ key: k }, e));
        }
        out.sort(function (a, b) { return (a.label || a.key) < (b.label || b.key) ? -1 : 1; });
        return out;
    }
    readonly property bool hasData: fiveHour !== null || sevenDay !== null
    readonly property bool sessionActive: !!(usage && usage.session_active)
    readonly property var apiInfo: (usage && usage.api) ? usage.api : ({})
    readonly property var block: (usage && usage.block) ? usage.block : ({})
    readonly property var history: (usage && usage.history) ? usage.history : ({})
    readonly property int lastUpdated: {
        var t = 0;
        for (var k in limits) if (limits[k] && limits[k].updated_at > t) t = limits[k].updated_at;
        return t;
    }
    readonly property int dataAge: lastUpdated ? nowSec - lastUpdated : -1
    // Old data only matters while a session is running; idle is just idle.
    readonly property bool isStale: hasData && sessionActive && dataAge > staleAfterSec
    readonly property var panelEntry: Plasmoid.configuration.panelMetric === "seven_day" ? sevenDay : fiveHour
    readonly property real panelPct: panelEntry ? panelEntry.used_percentage : 0
    readonly property bool isCritical: (fiveHour && fiveHour.used_percentage >= 90) || (sevenDay && sevenDay.used_percentage >= 90)
    readonly property bool apiBlocked: apiInfo.backoff_until > nowSec
    readonly property string problemText: {
        if (cacheMissing) return "Coletor ainda não rodou (sem arquivo de cache).";
        if (parseError) return "Cache ilegível; aguardando próxima gravação.";
        if (isStale) {
            var s = "Sem dados novos " + Fmt.relTime(lastUpdated, nowSec).replace("há ", "há ") + ".";
            if (apiBlocked) s += " API em espera até " + Fmt.clock(apiInfo.backoff_until) + ".";
            else if (apiInfo.last_error) s += " Último erro: " + apiInfo.last_error + ".";
            return s;
        }
        return "";
    }

    function levelColor(pct) {
        if (pct >= 90) return Kirigami.Theme.negativeTextColor;
        if (pct >= 70) return Kirigami.Theme.neutralTextColor;
        return Kirigami.Theme.positiveTextColor;
    }

    function forecastFor(key) {
        var e = limits[key];
        if (!e) return "";
        return Fmt.forecast(history[key], e.used_percentage, e.resets_at, nowSec);
    }

    function resetText(e) {
        if (!e || !e.resets_at) return "reset desconhecido";
        var remaining = e.resets_at - nowSec;
        if (remaining <= 0) return "janela resetou às " + Fmt.clock(e.resets_at) + " · aguardando dados";
        return "reseta em " + Fmt.fmtDuration(remaining) + " · " + Fmt.dateClock(e.resets_at);
    }

    // ---- cache reader (persistent source + interval; reconnecting per tick leaks in Plasma 6) ----
    P5Support.DataSource {
        id: reader
        engine: "executable"
        interval: root.pollMs
        Component.onCompleted: connectedSources = [root.readerSource]
        onNewData: function (sourceName, data) {
            root.lastReadAt = Date.now();
            var code = data["exit code"];
            if (code !== 0) {
                var err = String(data["stderr"] || "");
                root.cacheMissing = err.indexOf("No such file") >= 0;
                root.parseError = !root.cacheMissing;
                return;
            }
            try {
                root.usage = JSON.parse(data["stdout"]);
                root.parseError = false;
                root.cacheMissing = false;
                root.everLoaded = true;
            } catch (e) {
                root.parseError = true;
            }
        }
    }

    P5Support.DataSource {
        id: runner
        engine: "executable"
        onNewData: function (sourceName, data) {
            disconnectSource(sourceName);
            rereadTimer.start();
        }
    }

    function refreshNow() {
        runner.connectSource("systemctl --user start claude-usage-refresh.service");
    }

    function reread() {
        // Drop whatever is connected (including a source from an old
        // cachePath) and reconnect this instance's own source.
        reader.connectedSources = [];
        reader.connectedSources = [root.readerSource];
    }
    onCachePathChanged: reread()

    Timer { id: rereadTimer; interval: 4000; onTriggered: root.reread() }

    // Watchdog: the executable engine sometimes stops re-running an
    // interval source (seen with two instances sharing one source name
    // after a disconnect/reconnect). If no read landed in 3 intervals,
    // reconnect.
    Timer {
        interval: root.pollMs
        running: true
        repeat: true
        onTriggered: {
            if (Date.now() - root.lastReadAt > root.pollMs * 3) {
                console.warn("claudeusage: no cache read for " + Math.round((Date.now() - root.lastReadAt) / 1000) + "s, reconnecting");
                root.reread();
            }
        }
    }
    Timer { interval: 1000; running: true; repeat: true; onTriggered: root.nowSec = Math.floor(Date.now() / 1000) }

    // ---- plasmoid glue ---------------------------------------------------------
    Plasmoid.status: (root.isCritical && !root.isStale) ? PlasmaCore.Types.NeedsAttentionStatus : PlasmaCore.Types.ActiveStatus
    Plasmoid.contextualActions: [
        PlasmaCore.Action {
            text: "Atualizar agora"
            icon.name: "view-refresh"
            onTriggered: root.refreshNow()
        }
    ]

    toolTipMainText: "Claude Code"
    toolTipTextFormat: Text.PlainText
    toolTipSubText: {
        if (!root.hasData) return root.problemText || "sem dados";
        var lines = [];
        if (root.fiveHour) lines.push("Sessão 5 h: " + Fmt.fmtPct(root.fiveHour.used_percentage) + " · " + root.resetText(root.fiveHour));
        if (root.sevenDay) lines.push("Semana: " + Fmt.fmtPct(root.sevenDay.used_percentage) + " · " + root.resetText(root.sevenDay));
        if (root.problemText) lines.push(root.problemText);
        else lines.push(root.sessionActive ? "atualizado " + Fmt.relTime(root.lastUpdated, root.nowSec) : "ocioso · dados de " + Fmt.dateClock(root.lastUpdated));
        return lines.join("\n");
    }

    // No switchWidth/switchHeight on purpose: they take precedence over
    // preferredRepresentation, and a desktop widget smaller than the
    // threshold would collapse into the ring. Panel: ring + popup on click.
    // Desktop: the full view.
    preferredRepresentation: Plasmoid.formFactor === PlasmaCore.Types.Planar ? fullRepresentation : compactRepresentation
    compactRepresentation: CompactRepresentation {}
    fullRepresentation: FullRepresentation {}
}
