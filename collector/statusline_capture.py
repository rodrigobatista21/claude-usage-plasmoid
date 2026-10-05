#!/usr/bin/env python3
"""Reads the Claude Code statusLine JSON from stdin and records rate_limits
into the shared cache. Exits 0 quietly when there is nothing to record so the
wrapper never breaks the status line."""
import json
import sys

from claude_usage_common import check_thresholds, log, now, set_limit, update_cache

WINDOWS = ("five_hour", "seven_day")


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    limits = payload.get("rate_limits") if isinstance(payload, dict) else None
    if not isinstance(limits, dict):
        return 0
    found = {k: limits.get(k) for k in WINDOWS if isinstance(limits.get(k), dict)}
    if not found:
        return 0
    ts = now()

    def mutate(cache):
        for key, win in found.items():
            set_limit(cache, key, win.get("used_percentage"), win.get("resets_at"),
                      "statusline", ts=ts)
        cache["session_active"] = True
        cache["session_seen_at"] = ts
        check_thresholds(cache)

    try:
        update_cache(mutate)
    except Exception as e:
        log(f"statusline capture failed: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
