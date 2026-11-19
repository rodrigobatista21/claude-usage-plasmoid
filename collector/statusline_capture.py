#!/usr/bin/env python3
"""Reads the Claude Code statusLine JSON from stdin and records rate_limits
into the shared cache. Exits 0 quietly when there is nothing to record so the
wrapper never breaks the status line."""
import json
import sys

from claude_usage_common import check_thresholds, log, now, read_cache, record_statusline, update_cache

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
    session_id = str(payload.get("session_id") or "")
    ts = now()

    # Dry run on an unlocked read: most refreshes repeat what is already on
    # disk, and then there is nothing to write.
    try:
        if not record_statusline(read_cache(), session_id, found, ts)[0]:
            return 0
    except Exception as e:
        log(f"statusline capture failed: {e}")
        return 0

    def mutate(cache):
        if record_statusline(cache, session_id, found, ts)[1]:
            check_thresholds(cache)

    try:
        update_cache(mutate)
    except Exception as e:
        log(f"statusline capture failed: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
