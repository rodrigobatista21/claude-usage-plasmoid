#!/usr/bin/env python3
"""Shared pieces of the Claude usage pipeline.

Single writer path for ~/.cache/claude-usage/usage.json (flock + atomic
replace), the cache schema, normalization of the different producers into
one shape, burn-rate sample history, and threshold notifications.
"""
import fcntl
import glob
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

SCHEMA = 1
CACHE_DIR = os.path.expanduser("~/.cache/claude-usage")
CACHE_PATH = os.path.join(CACHE_DIR, "usage.json")
LOCK_PATH = os.path.join(CACHE_DIR, "usage.json.lock")

MAX_SAMPLES = 300
SAMPLE_THROTTLE_S = 60
PRUNE_GRACE_S = 15 * 60
HEARTBEAT_S = 60               # how often an unchanged statusLine value is re-confirmed on disk
SESSION_TTL_S = 24 * 3600      # forget a Claude Code session not seen for this long
WINDOW_TOLERANCE_S = 120       # reset times this close are the same window
TMP_MAX_AGE_S = 3600

# Which levels trigger a desktop notification, per bucket. 70% is noise.
THRESHOLDS = {"five_hour": (90, 100), "seven_day": (90,)}

LABELS = {
    "five_hour": "Sessão (5 h)",
    "seven_day": "Semana (todos os modelos)",
}


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def now():
    return int(time.time())


def default_cache():
    return {
        "schema": SCHEMA,
        "generated_at": 0,
        "session_active": False,
        "limits": {},
        "extra_usage_enabled": None,
        "api": {
            "last_attempt_at": 0,
            "last_success_at": 0,
            "last_status": None,
            "backoff_until": 0,
            "last_error": None,
        },
        "block": {"updated_at": 0, "active": False},
        "history": {},
        "notified": {},
        "sessions": {},
    }


def read_cache():
    try:
        with open(CACHE_PATH) as f:
            data = json.load(f)
        if not isinstance(data, dict) or data.get("schema") != SCHEMA:
            raise ValueError("schema mismatch")
        base = default_cache()
        base.update(data)
        return base
    except FileNotFoundError:
        return default_cache()
    except Exception as e:
        log(f"cache unreadable ({e}); starting fresh")
        return default_cache()


def update_cache(mutator):
    """Lock, read the freshest copy, apply mutator(cache), write atomically.
    Two producers (statusline capture + collector) share this path."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(LOCK_PATH, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        cache = read_cache()
        mutator(cache)
        cache["schema"] = SCHEMA
        cache["generated_at"] = now()
        tmp = f"{CACHE_PATH}.tmp-{os.getpid()}"
        try:
            with open(tmp, "w") as f:
                json.dump(cache, f, separators=(",", ":"))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, CACHE_PATH)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return cache


def sweep_tmp(at=None, max_age_s=TMP_MAX_AGE_S):
    """Remove temp files a killed writer left behind (SIGKILL/SIGTERM in the
    middle of update_cache skips the cleanup above). Returns how many."""
    at = at or now()
    removed = 0
    for path in glob.glob(glob.escape(CACHE_PATH) + ".tmp-*"):
        try:
            if at - os.stat(path).st_mtime > max_age_s:
                os.unlink(path)
                removed += 1
        except OSError:
            pass
    return removed


# --- limits -----------------------------------------------------------------

def set_limit(cache, key, pct, resets_at, source, label=None, ts=None):
    """Update one bucket without clobbering fields the producer didn't send."""
    if pct is None:
        return
    ts = ts or now()
    entry = cache["limits"].get(key) or {}
    # Never let an older fetch overwrite a newer one (API result applied after
    # a statusline sample that arrived while the request was in flight).
    if entry.get("updated_at", 0) > ts:
        return
    entry["used_percentage"] = round(float(pct), 1)
    if resets_at:
        entry["resets_at"] = int(resets_at)
    entry["source"] = source
    entry["updated_at"] = ts
    if label:
        entry["label"] = label
    elif key in LABELS:
        entry["label"] = LABELS[key]
    cache["limits"][key] = entry
    add_sample(cache, key, entry["used_percentage"], entry.get("resets_at"), ts)


def add_sample(cache, key, pct, resets_at, ts):
    hist = cache["history"].setdefault(key, {"window": 0, "samples": []})
    window = int(resets_at or 0)
    if hist.get("window") != window:
        hist["window"] = window
        hist["samples"] = []
    samples = hist["samples"]
    if samples and samples[-1][1] == pct:
        # A flat stretch is two samples, its first and its latest, so the
        # history spans the whole window instead of the last MAX_SAMPLES
        # minutes. A change is always recorded.
        if ts - samples[-1][0] < SAMPLE_THROTTLE_S:
            return
        if len(samples) >= 2 and samples[-2][1] == pct:
            samples[-1][0] = ts
            return
    samples.append([ts, pct])
    if len(samples) > MAX_SAMPLES:
        del samples[: len(samples) - MAX_SAMPLES]


def statusline_verdict(cache, session_id, key, pct, resets_at, ts):
    """What to do with one window a Claude Code session just reported:
    "apply", "heartbeat" (same value we already have: only confirm it) or
    "ignore".

    Every open session re-sends the rate limits of its own last API
    response on each statusLine refresh. An idle session therefore keeps
    repeating an old number, and with two sessions open the cache used to
    flip between the fresh value and the old one every few seconds. So a
    repeated snapshot is not news: a session's value counts when it changes
    (a new observation), or the first time the session is seen if it isn't
    below what is already known. Usage only grows inside a window, so a
    lower first value is an old one."""
    if pct is None:
        return "ignore"
    pct = round(float(pct), 1)
    window = int(resets_at or 0)
    if window and window <= ts:
        return "ignore"          # a window that already ended
    cur = cache["limits"].get(key)
    if not cur:
        return "apply"
    cur_window = int(cur.get("resets_at") or 0)
    if window and cur_window:
        if window < cur_window - WINDOW_TOLERANCE_S:
            return "ignore"      # an older window than the one we know
        if window > cur_window + WINDOW_TOLERANCE_S:
            return "apply"       # the window rolled over
    session = (cache.get("sessions") or {}).get(session_id) or {}
    prev = (session.get("limits") or {}).get(key)
    if prev is not None and prev != [pct, window]:
        return "apply"           # this session observed something new
    cur_pct = cur.get("used_percentage")
    if cur_pct == pct:
        return "heartbeat"
    if prev is None and cur_pct is not None and pct > cur_pct:
        return "apply"
    return "ignore"


def record_statusline(cache, session_id, windows, ts):
    """Apply what one session reported (windows: {key: {used_percentage,
    resets_at}}). Returns (changed, applied): whether the cache needs to be
    written at all, and whether any limit value moved. Unchanged state is
    only re-confirmed every HEARTBEAT_S, so an idle session costs one small
    write a minute instead of one per statusLine refresh."""
    session_id = session_id or ""
    changed = applied = False
    sessions = cache.setdefault("sessions", {})
    session = sessions.get(session_id)
    if session is None:
        session = sessions[session_id] = {"seen_at": 0, "limits": {}}
        changed = True
    for key, win in windows.items():
        pct, resets_at = win.get("used_percentage"), win.get("resets_at")
        verdict = statusline_verdict(cache, session_id, key, pct, resets_at, ts)
        if pct is not None:
            snapshot = [round(float(pct), 1), int(resets_at or 0)]
            if session["limits"].get(key) != snapshot:
                session["limits"][key] = snapshot
                changed = True
        if verdict == "apply":
            set_limit(cache, key, pct, resets_at, "statusline", ts=ts)
            changed = applied = True
        elif verdict == "heartbeat":
            entry = cache["limits"][key]
            if ts - entry.get("updated_at", 0) >= HEARTBEAT_S:
                entry["updated_at"] = ts
                entry["source"] = "statusline"
                changed = True
    if ts - session.get("seen_at", 0) >= HEARTBEAT_S:
        session["seen_at"] = ts
        changed = True
    if ts - cache.get("session_seen_at", 0) >= HEARTBEAT_S or not cache.get("session_active"):
        cache["session_active"] = True
        cache["session_seen_at"] = ts
        changed = True
    for sid in [s for s, v in sessions.items() if ts - v.get("seen_at", 0) > SESSION_TTL_S and s != session_id]:
        del sessions[sid]
        changed = True
    return changed, applied


def is_stale(entry, max_age_s, at=None):
    """Old if nobody refreshed it within max_age, or if its reset time has
    passed since the last refresh (the number on screen is from a dead
    window)."""
    at = at or now()
    if not entry:
        return True
    updated = entry.get("updated_at", 0)
    if at - updated > max_age_s:
        return True
    resets_at = entry.get("resets_at") or 0
    if resets_at and updated < resets_at <= at:
        return True
    return False


def iso_to_epoch(text):
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except ValueError:
        return None


def normalize_api_limits(data):
    """oauth/usage response -> list of (key, pct, resets_at_epoch, label).
    kinds: session -> five_hour, weekly_all -> seven_day, weekly_<x> ->
    seven_day_<x>; anything else keeps its kind under other_<kind>."""
    out = []
    for entry in data.get("limits") or []:
        kind = entry.get("kind") or "limit"
        pct = entry.get("percent")
        if pct is None:
            continue
        resets = iso_to_epoch(entry.get("resets_at"))
        if kind == "session":
            key, label = "five_hour", None
        elif kind == "weekly_all":
            key, label = "seven_day", None
        elif kind == "weekly_scoped":
            # Per-model weekly window (Fable today); the model name lives in
            # scope.model.display_name, same place ccstatusline reads it.
            scope = entry.get("scope") or {}
            name = ((scope.get("model") or {}).get("display_name") or "modelo").strip()
            # Key on the model family only ("Fable 5.1" -> fable) so the API
            # and the ccstatusline merge land on the same bucket.
            slug = "".join(ch if ch.isalnum() else " " for ch in name.lower()).split()[0]
            key, label = f"seven_day_{slug}", f"{name} (7d)"
        elif kind.startswith("seven_day_") or kind.startswith("weekly_"):
            model = kind.split("_", 2)[-1] if kind.startswith("seven_day_") else kind[len("weekly_"):]
            key, label = f"seven_day_{model}", f"{model.replace('_', ' ').title()} (7d)"
        else:
            key, label = f"other_{kind}", kind.replace("_", " ").title()
        out.append((key, pct, resets, label))
    return out


def prune_unreported(cache, reported, at, grace_s=PRUNE_GRACE_S):
    """Drop buckets a successful API response no longer lists, with their
    history and notification state, and return their keys.

    The API is the authoritative list of windows an account has, and that
    list depends on the plan. When one goes away (a Max 20x / Ultra account
    stopped reporting the 5-hour session window on 2026-11-09) its last
    value would otherwise sit in the cache forever,
    shown as a dead 0% and counted as stale on every run. A bucket another
    producer refreshed within the grace period is kept, and an empty report
    prunes nothing, so an unexpected payload shape can't wipe the cache. A
    window that hasn't reset yet is kept too: the statusLine can know a
    window the API doesn't list (the 5-hour one, again, since 2026-11-18),
    and its last value stays true until the reset even if nobody repeats
    it. A window that comes back is simply recreated by set_limit()."""
    if not reported:
        return []
    dropped = []
    for key in list(cache["limits"]):
        if key in reported:
            continue
        if at - cache["limits"][key].get("updated_at", 0) < grace_s:
            continue
        if (cache["limits"][key].get("resets_at") or 0) > at:
            continue
        del cache["limits"][key]
        cache["history"].pop(key, None)
        cache["notified"].pop(key, None)
        dropped.append(key)
    return dropped


# --- notifications ------------------------------------------------------------

def notify(title, body, urgency="normal", icon="dialog-warning"):
    exe = shutil.which("notify-send")
    if not exe:
        log(f"notify-send missing; {title}: {body}")
        return
    try:
        subprocess.run(
            [exe, "-a", "Claude Usage", "-i", icon, "-u", urgency, title, body],
            timeout=5, check=False,
        )
    except Exception as e:
        log(f"notify failed: {e}")


def check_thresholds(cache):
    """One notification per level per reset window. A 5h window that reached
    100% also announces its reset, since that's when you can work again."""
    for key, levels in THRESHOLDS.items():
        entry = cache["limits"].get(key)
        if not entry:
            continue
        pct = entry.get("used_percentage", 0)
        window = int(entry.get("resets_at") or 0)
        label = entry.get("label") or LABELS.get(key, key)
        st = cache["notified"].get(key) or {"window": 0, "levels": []}
        if st.get("window") != window:
            if 100 in st.get("levels", []) and key == "five_hour":
                notify("Claude Code liberado", "A janela de 5 h resetou.",
                       icon="dialog-positive")
            st = {"window": window, "levels": []}
        for lvl in levels:
            if pct >= lvl and lvl not in st["levels"]:
                st["levels"].append(lvl)
                when = ""
                if window:
                    when = " · reseta " + datetime.fromtimestamp(window).strftime("%d/%m %H:%M")
                if lvl >= 100:
                    notify("Claude Code: limite esgotado", f"{label}{when}", urgency="critical")
                else:
                    notify(f"Claude Code: {lvl}% usado", f"{label}{when}")
        cache["notified"][key] = st
