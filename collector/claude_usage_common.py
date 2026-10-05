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
        with open(tmp, "w") as f:
            json.dump(cache, f, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, CACHE_PATH)
        return cache


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
    if samples and ts - samples[-1][0] < SAMPLE_THROTTLE_S:
        return
    samples.append([ts, pct])
    if len(samples) > MAX_SAMPLES:
        del samples[: len(samples) - MAX_SAMPLES]


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


def scoped_keys(cache):
    return [k for k in cache["limits"] if k.startswith("seven_day_")]


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


# --- misc -----------------------------------------------------------------------

def find_bin(name, nvm_glob=None):
    p = shutil.which(name)
    if p:
        return p
    pattern = nvm_glob or f"~/.nvm/versions/node/*/bin/{name}"
    matches = sorted(glob.glob(os.path.expanduser(pattern)))
    return matches[-1] if matches else None


def subprocess_env_for(binary_path):
    """nvm's node dir isn't on the systemd user PATH; node scripts need it."""
    env = os.environ.copy()
    if binary_path:
        env["PATH"] = os.path.dirname(binary_path) + os.pathsep + env.get("PATH", "")
    return env
