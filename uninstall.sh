#!/usr/bin/env bash
# Removes everything install.sh put in place and restores the previous
# Claude Code status line. Keeps nothing behind except settings.json backups.
set -uo pipefail

PLASMOID_ID="org.rodrigom.claudeusage"
SHARE="$HOME/.local/share/claude-usage"
WRAPPER="$HOME/.local/bin/claude-statusline-wrapper.sh"
UNITS="$HOME/.config/systemd/user"
CONF="$HOME/.config/claude-usage"
CACHE="$HOME/.cache/claude-usage"
SETTINGS="$HOME/.claude/settings.json"

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }

say "Restoring the Claude Code statusLine"
if [ -f "$SETTINGS" ]; then
    python3 - "$SETTINGS" "$WRAPPER" "$CONF/passthrough" <<'EOF'
import json, os, sys
settings, wrapper, passthrough = sys.argv[1:4]
with open(settings) as f:
    data = json.load(f)
sl = data.get("statusLine") or {}
if sl.get("command") == wrapper:
    previous = open(passthrough).read().strip() if os.path.exists(passthrough) else ""
    if previous:
        data["statusLine"]["command"] = previous
        print(f"    statusLine restored to: {previous}")
    else:
        data.pop("statusLine", None)
        print("    statusLine entry removed")
    with open(settings, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
else:
    print("    statusLine was not pointing to the wrapper; left untouched")
EOF
fi

say "systemd units"
systemctl --user disable --now claude-usage-collector.timer 2>/dev/null
rm -f "$UNITS"/claude-usage-collector.service "$UNITS"/claude-usage-collector.timer "$UNITS"/claude-usage-refresh.service
systemctl --user daemon-reload

say "Plasmoid"
kpackagetool6 --type Plasma/Applet --remove "$PLASMOID_ID" >/dev/null 2>&1 && echo "    removed" || echo "    not installed"

say "Files"
rm -rf "$SHARE" "$CONF" "$CACHE"
rm -f "$WRAPPER"
echo "    removed $SHARE, $CONF, $CACHE and the wrapper"
echo
echo "If the widget is still on a panel, remove it there; plasmashell may need a restart."
