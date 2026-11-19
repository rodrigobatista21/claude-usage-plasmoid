#!/usr/bin/env python3
"""The active 5-hour usage block, read straight from Claude Code's session
logs (~/.claude/projects/**/*.jsonl).

This replaces `ccusage blocks --active`, with the same block rules and the
same token totals, but it only opens the files touched recently instead of
the whole history on every run (0.3 s instead of 8 s for 18 GB of logs).
There is no cost figure: it needs a price table per model, which goes stale
with every new model (ccusage priced two of three models at $0 here).

Block rules (ccusage's): a block starts at the hour of its first request and
lasts 5 hours; the next request after the block's end, or after 5 hours of
silence, starts a new one. Where a block starts therefore depends on the
block before it, back to the last 5-hour silence. That is how far back the
logs have to be read, and it can be days.
"""
import json
import os
import time
from datetime import datetime, timezone

BLOCK_S = 5 * 3600
# How far back to read, in hours. Each step is tried until one contains a
# 5-hour silence before the current block; None reads everything.
LOOKBACKS_H = (96, 30 * 24, None)


def _epoch(text):
    try:
        dt = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def recent_files(projects_dir, since):
    """Session logs modified at or after `since` (epoch; None = all). A
    request made after `since` is always in a file modified after it."""
    out = []
    for dirpath, _dirs, files in os.walk(projects_dir):
        for name in files:
            if not name.endswith(".jsonl"):
                continue
            path = os.path.join(dirpath, name)
            if since is not None:
                try:
                    if os.stat(path).st_mtime < since:
                        continue
                except OSError:
                    continue
            out.append(path)
    return out


def read_entries(paths, since=None):
    """Requests with token usage, as sorted (ts, input, output, cache_create,
    cache_read, model) tuples, deduplicated like ccusage does (message id +
    request id: a resumed or forked session repeats lines of its parent)."""
    seen = set()
    entries = []
    for path in paths:
        try:
            f = open(path, "rb")
        except OSError:
            continue
        with f:
            for line in f:
                # Cheap filter first: most lines are tool output with no usage.
                if b'"usage"' not in line or b'"timestamp"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(d, dict):
                    continue
                msg = d.get("message")
                if not isinstance(msg, dict):
                    continue
                usage = msg.get("usage")
                if not isinstance(usage, dict) or msg.get("model") == "<synthetic>":
                    continue
                ts = _epoch(d.get("timestamp"))
                if ts is None or (since is not None and ts < since):
                    continue
                mid, rid = msg.get("id"), d.get("requestId")
                if mid and rid:
                    if (mid, rid) in seen:
                        continue
                    seen.add((mid, rid))
                entries.append((
                    ts,
                    int(usage.get("input_tokens") or 0),
                    int(usage.get("output_tokens") or 0),
                    int(usage.get("cache_creation_input_tokens") or 0),
                    int(usage.get("cache_read_input_tokens") or 0),
                    msg.get("model") or "",
                ))
    entries.sort()
    return entries


def last_block(entries, since=None):
    """The most recent block among `entries`, or None when where it starts
    can't be told from them. With `since` set, entries before it are unknown,
    so the chain of blocks is only certain after a silence of 5 hours inside
    the known span (counting from `since` to the first entry)."""
    if not entries:
        return None
    anchor = 0 if since is None else None
    if since is not None and entries[0][0] - since > BLOCK_S:
        anchor = 0
    for i in range(1, len(entries)):
        if entries[i][0] - entries[i - 1][0] > BLOCK_S:
            anchor = i
    if anchor is None:
        return None
    block = None
    for e in entries[anchor:]:
        ts = e[0]
        if block is None or ts - block["start"] > BLOCK_S or ts - block["last"] > BLOCK_S:
            block = {"start": ts - ts % 3600, "first": ts, "last": ts, "entries": 0,
                     "tokens": [0, 0, 0, 0], "models": []}
        block["last"] = ts
        block["entries"] += 1
        for j in range(4):
            block["tokens"][j] += e[j + 1]
        if e[5] and e[5] not in block["models"]:
            block["models"].append(e[5])
    return block


def describe(block, at):
    """The cache's "block" entry for a block that is still running at `at`."""
    end = block["start"] + BLOCK_S
    inp, out, cache_create, cache_read = block["tokens"]
    total = inp + out + cache_create + cache_read
    minutes = (block["last"] - block["first"]) / 60
    remaining = max(0, int((end - at) // 60))
    result = {
        "active": True,
        "start_time": _iso(block["start"]),
        "end_time": _iso(end),
        "entries": block["entries"],
        "total_tokens": total,
        # Input + output only: cache reads would swamp the rate (ccusage's
        # tokensPerMinuteForIndicator).
        "tokens_per_minute": (inp + out) / minutes if minutes > 0 else None,
        "token_counts": {"input": inp, "output": out,
                         "cache_create": cache_create, "cache_read": cache_read},
        "projection": {"remaining_minutes": remaining, "total_tokens": None},
        "models": block["models"],
    }
    if minutes > 0:
        result["projection"]["total_tokens"] = round(total + total / minutes * remaining)
    return result


def active_block(projects_dir, at=None):
    """Returns (block entry, log message). The entry is {"active": False}
    when no block is running."""
    at = at or time.time()
    t0 = time.monotonic()
    # No file touched in the last 5 hours means no request in them either.
    if not recent_files(projects_dir, at - BLOCK_S):
        return {"active": False}, f"ok {time.monotonic() - t0:.2f}s (no active block)"
    for hours in LOOKBACKS_H:
        since = None if hours is None else at - hours * 3600
        entries = read_entries(recent_files(projects_dir, since), since)
        if not entries or at - entries[-1][0] >= BLOCK_S:
            return {"active": False}, f"ok {time.monotonic() - t0:.2f}s (no active block)"
        block = last_block(entries, since)
        if block is None:
            continue    # no 5-hour silence in this span: look further back
        elapsed = time.monotonic() - t0
        if at >= block["start"] + BLOCK_S:
            return {"active": False}, f"ok {elapsed:.2f}s (no active block)"
        span = "all" if hours is None else f"{hours}h"
        return describe(block, at), f"ok {elapsed:.2f}s ({block['entries']} requests, read {span})"
    return {"active": False}, "ok (no entries)"
