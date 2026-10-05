#!/usr/bin/env bash
# Installs the Claude Usage plasmoid, the collector and the statusLine wrapper
# for the current user. Safe to run again to update.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLASMOID_ID="org.rodrigom.claudeusage"
SHARE="$HOME/.local/share/claude-usage"
BIN="$HOME/.local/bin"
UNITS="$HOME/.config/systemd/user"
CONF="$HOME/.config/claude-usage"
CACHE="$HOME/.cache/claude-usage"
SETTINGS="$HOME/.claude/settings.json"
WRAPPER="$BIN/claude-statusline-wrapper.sh"

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33mwarning:\033[0m %s\n' "$*" >&2; }
need() { command -v "$1" >/dev/null 2>&1 || { echo "missing required command: $1" >&2; exit 1; }; }

need python3
need kpackagetool6
need systemctl
command -v notify-send >/dev/null 2>&1 || warn "notify-send not found: threshold notifications will only be logged"
command -v ccusage >/dev/null 2>&1 || ls "$HOME"/.nvm/versions/node/*/bin/ccusage >/dev/null 2>&1 \
    || warn "ccusage not found: the 'current block' section will stay empty (npm i -g ccusage)"

say "Collector and wrapper"
mkdir -p "$SHARE" "$BIN" "$UNITS" "$CONF" "$CACHE"
install -m 0644 "$HERE"/collector/*.py "$SHARE/"
install -m 0755 "$HERE/bin/claude-statusline-wrapper.sh" "$WRAPPER"

say "systemd user units"
install -m 0644 "$HERE"/systemd/claude-usage-collector.service "$UNITS/"
install -m 0644 "$HERE"/systemd/claude-usage-collector.timer "$UNITS/"
install -m 0644 "$HERE"/systemd/claude-usage-refresh.service "$UNITS/"
systemctl --user daemon-reload
systemctl --user enable --now claude-usage-collector.timer >/dev/null

say "Claude Code statusLine"
if [ -f "$SETTINGS" ]; then
    python3 - "$SETTINGS" "$WRAPPER" "$CONF/passthrough" <<'EOF'
import json, shutil, sys, time
settings, wrapper, passthrough = sys.argv[1:4]
with open(settings) as f:
    data = json.load(f)
sl = data.get("statusLine") or {}
current = sl.get("command") if sl.get("type") == "command" else None
if current and current != wrapper:
    # Remember the previous status line so the wrapper keeps running it.
    with open(passthrough, "w") as f:
        f.write(current)
    print(f"    previous status line kept: {current}")
if current != wrapper:
    shutil.copy(settings, f"{settings}.bak-claude-usage-{time.strftime('%Y%m%d%H%M%S')}")
    data["statusLine"] = {**sl, "type": "command", "command": wrapper}
    data["statusLine"].setdefault("padding", 0)
    data["statusLine"].setdefault("refreshInterval", 10)
    with open(settings, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print("    statusLine now points to the wrapper (backup written next to settings.json)")
else:
    print("    already configured")
EOF
else
    warn "$SETTINGS not found; add this to it manually:"
    echo "    \"statusLine\": {\"type\": \"command\", \"command\": \"$WRAPPER\", \"padding\": 0, \"refreshInterval\": 10}"
fi

say "Plasmoid"
if [ -d "$HOME/.local/share/plasma/plasmoids/$PLASMOID_ID" ]; then
    kpackagetool6 --type Plasma/Applet --upgrade "$HERE/package" >/dev/null
    echo "    upgraded (restart plasmashell to reload the QML: systemctl --user restart plasma-plasmashell.service)"
else
    kpackagetool6 --type Plasma/Applet --install "$HERE/package" >/dev/null
    echo "    installed"
fi

say "First collection"
systemctl --user start claude-usage-refresh.service || true

cat <<EOF

Done. Next:
  * Right-click the panel or desktop > "Add Widgets..." > search "Claude Usage".
  * Open a Claude Code session: the status line feeds the widget every few seconds.
  * Logs: journalctl --user -t claude-usage-collector -f
EOF
