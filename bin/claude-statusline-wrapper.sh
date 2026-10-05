#!/usr/bin/env bash
# statusLine command for Claude Code.
#
# 1. Records rate_limits from the stdin JSON into the shared usage cache
#    (~/.cache/claude-usage/usage.json) so the plasmoid never needs the network.
# 2. Hands the untouched JSON to whatever status line you used before
#    (saved by install.sh in ~/.config/claude-usage/passthrough), or to
#    ccstatusline, or prints a minimal line of its own.
input=$(cat)
printf '%s' "$input" | timeout 2 /usr/bin/python3 "$HOME/.local/share/claude-usage/statusline_capture.py" >/dev/null 2>&1

PASSTHROUGH=$(cat "$HOME/.config/claude-usage/passthrough" 2>/dev/null)
if [ -n "$PASSTHROUGH" ]; then
    printf '%s' "$input" | bash -c "$PASSTHROUGH"
    exit $?
fi

CCS=$(command -v ccstatusline 2>/dev/null)
if [ -z "$CCS" ]; then
    CCS=$(ls -d "$HOME"/.nvm/versions/node/*/bin/ccstatusline 2>/dev/null | sort -V | tail -1)
fi
if [ -n "$CCS" ]; then
    printf '%s' "$input" | "$CCS"
    exit $?
fi

# Fallback: model name plus the two main windows.
CLAUDE_STATUSLINE_INPUT="$input" python3 - <<'EOF'
import json, os, sys
try:
    d = json.loads(os.environ.get("CLAUDE_STATUSLINE_INPUT", ""))
except Exception:
    sys.exit(0)
model = (d.get("model") or {}).get("display_name") or ""
rl = d.get("rate_limits") or {}
parts = [model] if model else []
for key, label in (("five_hour", "5h"), ("seven_day", "7d")):
    w = rl.get(key) or {}
    if w.get("used_percentage") is not None:
        parts.append("%s %d%%" % (label, round(w["used_percentage"])))
print(" | ".join(parts))
EOF
