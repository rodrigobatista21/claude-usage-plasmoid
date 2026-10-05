#!/usr/bin/env python3
"""Oneshot collector, run by a systemd timer (every 5 min) or with --force
from the plasmoid's refresh button.

Policy:
  * The statusLine capture is the primary source. The oauth/usage API is only
    a fallback when the cache is stale, and never more often than every
    10 min, with jitter, honoring our own backoff and ccstatusline's lock.
  * Never refreshes the OAuth token: refresh tokens are single-use and racing
    Claude Code for them can revoke the login. Only reads the file.
  * ccusage (local JSONL scan, ~300 MB peak) runs only while a session is
    active and at most every 5 min.
"""
import json
import os
import random
import subprocess
import sys
import time
import urllib.error
import urllib.request

from claude_usage_common import (
    check_thresholds, find_bin, is_stale, iso_to_epoch, log, normalize_api_limits,
    now, read_cache, scoped_keys, set_limit, subprocess_env_for, update_cache,
)

OAUTH_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CREDENTIALS = os.path.expanduser("~/.claude/.credentials.json")
CCSTATUSLINE_CACHE = os.path.expanduser("~/.cache/ccstatusline/usage.json")
CCSTATUSLINE_LOCK = os.path.expanduser("~/.cache/ccstatusline/usage.lock")
PROJECTS_DIR = os.path.expanduser("~/.claude/projects")

STALE_ACTIVE_S = 10 * 60
STALE_IDLE_S = 30 * 60
API_MIN_SPACING_S = 10 * 60
API_JITTER_S = 60
BACKOFF_MIN_S = 5 * 60
BACKOFF_DEFAULT_S = 10 * 60
BACKOFF_MAX_S = 60 * 60
AUTH_BACKOFF_S = 30 * 60
SESSION_RECENT_S = 3 * 60
JSONL_RECENT_S = 10 * 60
BLOCK_MAX_AGE_S = 5 * 60
SCOPED_RECHECK_S = 60 * 60


def session_active(cache, at):
    for key in ("five_hour", "seven_day"):
        e = cache["limits"].get(key) or {}
        if e.get("source") == "statusline" and at - e.get("updated_at", 0) < SESSION_RECENT_S:
            return True
    if at - cache.get("session_seen_at", 0) < SESSION_RECENT_S:
        return True
    try:
        with os.scandir(PROJECTS_DIR) as projects:
            for proj in projects:
                if not proj.is_dir():
                    continue
                with os.scandir(proj.path) as files:
                    for f in files:
                        if f.name.endswith(".jsonl") and at - f.stat().st_mtime < JSONL_RECENT_S:
                            return True
    except FileNotFoundError:
        pass
    return False


def read_token(at):
    try:
        with open(CREDENTIALS) as f:
            oauth = json.load(f).get("claudeAiOauth") or {}
    except Exception as e:
        return None, f"credentials unreadable: {e}"
    token = oauth.get("accessToken")
    if not token:
        return None, "no accessToken in credentials"
    expires_ms = oauth.get("expiresAt") or 0
    if expires_ms and expires_ms / 1000 <= at:
        return None, "token expired; waiting for Claude Code to refresh it"
    return token, None


def ccstatusline_blocked_until():
    try:
        with open(CCSTATUSLINE_LOCK) as f:
            return int(json.load(f).get("blockedUntil") or 0)
    except Exception:
        return 0


def merge_ccstatusline(cache, at):
    """ccstatusline keeps its own 180 s API cache; if it's newer than ours,
    use it instead of spending another request on the same endpoint."""
    try:
        mtime = int(os.stat(CCSTATUSLINE_CACHE).st_mtime)
        with open(CCSTATUSLINE_CACHE) as f:
            data = json.load(f)
    except Exception:
        return False
    ours = max((e.get("updated_at", 0) for e in cache["limits"].values()), default=0)
    if mtime <= ours:
        return False
    merged = False
    if data.get("sessionUsage") is not None:
        set_limit(cache, "five_hour", data["sessionUsage"], iso_to_epoch(data.get("sessionResetAt")),
                  "ccstatusline", ts=mtime)
        merged = True
    if data.get("weeklyUsage") is not None:
        set_limit(cache, "seven_day", data["weeklyUsage"], iso_to_epoch(data.get("weeklyResetAt")),
                  "ccstatusline", ts=mtime)
        merged = True
    # Per-model windows: only trust the ones that come with a reset time
    # (ccstatusline writes 0 for models the API didn't mention).
    for usage_field, reset_field, key, label in (
        ("fableUsage", "fableResetAt", "seven_day_fable", "Fable (7d)"),
        ("weeklyOpusUsage", "weeklyOpusResetAt", "seven_day_opus", "Opus (7d)"),
        ("weeklySonnetUsage", "weeklySonnetResetAt", "seven_day_sonnet", "Sonnet (7d)"),
    ):
        reset = iso_to_epoch(data.get(reset_field))
        if data.get(usage_field) is not None and reset:
            set_limit(cache, key, data[usage_field], reset, "ccstatusline", label=label, ts=mtime)
            merged = True
    if data.get("extraUsageEnabled") is not None:
        cache["extra_usage_enabled"] = bool(data["extraUsageEnabled"])
    return merged


def fetch_api(token):
    """Returns (status, payload_or_None, retry_after_s_or_None, error)."""
    req = urllib.request.Request(
        OAUTH_USAGE_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": "oauth-2025-04-20",
            "User-Agent": "claude-usage-collector/2.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode()), None, None
    except urllib.error.HTTPError as e:
        retry = e.headers.get("Retry-After")
        try:
            retry = int(retry) if retry is not None else None
        except ValueError:
            retry = None
        return e.code, None, retry, str(e)
    except Exception as e:
        return None, None, None, str(e)


def run_ccusage():
    exe = find_bin("ccusage")
    if not exe:
        return None, "ccusage not found"
    t0 = time.monotonic()
    try:
        r = subprocess.run(
            [exe, "blocks", "--active", "--json", "-O"],
            capture_output=True, text=True, timeout=60, env=subprocess_env_for(exe),
        )
        data = json.loads(r.stdout)
    except Exception as e:
        return None, f"ccusage failed: {e}"
    elapsed = time.monotonic() - t0
    for b in data.get("blocks", []):
        if b.get("isActive"):
            burn = b.get("burnRate") or {}
            proj = b.get("projection") or {}
            tc = b.get("tokenCounts") or {}
            return {
                "active": True,
                "start_time": b.get("startTime"),
                "end_time": b.get("endTime"),
                "total_tokens": b.get("totalTokens"),
                "cost_usd": b.get("costUSD"),
                "tokens_per_minute": burn.get("tokensPerMinuteForIndicator"),
                "cost_per_hour": burn.get("costPerHour"),
                "token_counts": {
                    "input": tc.get("inputTokens"),
                    "output": tc.get("outputTokens"),
                    "cache_create": tc.get("cacheCreationInputTokens"),
                    "cache_read": tc.get("cacheReadInputTokens"),
                },
                "projection": {
                    "remaining_minutes": proj.get("remainingMinutes"),
                    "total_cost": proj.get("totalCost"),
                    "total_tokens": proj.get("totalTokens"),
                },
                "models": b.get("models") or [],
            }, f"ok {elapsed:.1f}s"
    return {"active": False}, f"ok {elapsed:.1f}s (no active block)"


def main(argv):
    force = "--force" in argv
    at = now()
    cache = read_cache()
    active = session_active(cache, at)
    api = cache["api"]
    tag = "force" if force else "timer"
    log(f"{tag}: session_active={active}")

    # ---- limits -------------------------------------------------------------
    max_age = STALE_ACTIVE_S if active else STALE_IDLE_S

    def limits_stale(c):
        """The statusLine keeps five_hour/seven_day fresh forever, but the
        per-model windows (Fable) only come from the API, so they count too.
        An account with no scoped window at all just rechecks hourly."""
        main_keys = ("five_hour", "seven_day")
        if any(is_stale(c["limits"].get(k), max_age, at) for k in main_keys):
            return True
        scoped = scoped_keys(c)
        if scoped:
            return any(is_stale(c["limits"].get(k), max_age, at) for k in scoped)
        return at - c["api"].get("last_success_at", 0) > SCOPED_RECHECK_S

    stale = force or limits_stale(cache)
    api_result = None  # (status, payload, retry_after, error)
    merged_ccs = False
    if not stale:
        age = at - min((cache["limits"][k].get("updated_at", 0) for k in cache["limits"]), default=at)
        log(f"skip api: limits fresh ({age // 60}m)")
    else:
        merged_ccs = merge_ccstatusline(cache, at)
        still_stale = force or limits_stale(cache)
        blocked = max(api.get("backoff_until", 0), ccstatusline_blocked_until())
        spacing_ok = force or (at - api.get("last_attempt_at", 0)) >= API_MIN_SPACING_S + random.randint(0, API_JITTER_S)
        if merged_ccs and not still_stale:
            log("api: merged ccstatusline cache instead")
        elif at < blocked:
            log(f"skip api: backoff until {time.strftime('%H:%M', time.localtime(blocked))}")
        elif not spacing_ok:
            log(f"skip api: last attempt {(at - api.get('last_attempt_at', 0)) // 60}m ago")
        else:
            token, err = read_token(at)
            if not token:
                log(f"skip api: {err}")
            else:
                api_result = fetch_api(token)

    # ---- active block ---------------------------------------------------------
    block_result = None
    block_age = at - cache["block"].get("updated_at", 0)
    if force or (active and block_age > BLOCK_MAX_AGE_S):
        block, msg = run_ccusage()
        log(f"ccusage: {msg}")
        if block is not None:
            block_result = block
    elif not active:
        log("skip ccusage: idle")
    else:
        log(f"skip ccusage: block fresh ({block_age // 60}m)")

    # ---- commit (re-read inside the lock so a concurrent capture isn't lost) --
    def mutate(c):
        c["session_active"] = active
        if merged_ccs:
            merge_ccstatusline(c, at)
        if api_result is not None:
            status, payload, retry_after, err = api_result
            a = c["api"]
            a["last_attempt_at"] = at
            a["last_status"] = status
            if status == 200 and payload is not None:
                a["last_success_at"] = at
                a["backoff_until"] = 0
                a["last_error"] = None
                a["consecutive_429"] = 0
                for key, pct, resets, label in normalize_api_limits(payload):
                    set_limit(c, key, pct, resets, "api", label=label, ts=at)
                extra = payload.get("extra_usage") or {}
                if "is_enabled" in extra:
                    c["extra_usage_enabled"] = bool(extra["is_enabled"])
                log("api: 200")
            elif status == 429:
                n = a.get("consecutive_429", 0) + 1
                a["consecutive_429"] = n
                if retry_after:
                    wait = retry_after
                else:
                    wait = BACKOFF_DEFAULT_S * (2 ** (n - 1))
                wait = max(BACKOFF_MIN_S, min(BACKOFF_MAX_S, wait))
                a["backoff_until"] = at + wait
                a["last_error"] = "rate-limited"
                log(f"api: 429 retry-after={retry_after} -> backoff {wait}s")
            elif status in (401, 403):
                a["backoff_until"] = at + AUTH_BACKOFF_S
                a["last_error"] = f"auth {status}"
                log(f"api: {status} -> backoff {AUTH_BACKOFF_S}s")
            else:
                a["backoff_until"] = at + BACKOFF_MIN_S
                a["last_error"] = err or f"http {status}"
                log(f"api: error {status or ''} {err} -> backoff {BACKOFF_MIN_S}s")
        if block_result is not None:
            block_result["updated_at"] = at
            c["block"] = block_result
        check_thresholds(c)

    update_cache(mutate)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
