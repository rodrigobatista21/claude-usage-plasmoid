#!/usr/bin/env python3
"""Regression tests for buckets that the producers stop reporting.

Run with: python3 -m unittest discover -s tests
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "collector"))

import claude_usage_common as common  # noqa: E402
import collector  # noqa: E402

NOW = 1_794_317_000
WEEK_RESET = NOW + 3 * 86400


def cache_with_dead_five_hour():
    """The state seen on 2026-11-10: weekly buckets alive, five_hour frozen
    since the previous evening with a reset time long gone."""
    c = common.default_cache()
    c["limits"] = {
        "five_hour": {"used_percentage": 0.0, "resets_at": NOW - 56000,
                      "source": "ccstatusline", "updated_at": NOW - 57000},
        "seven_day": {"used_percentage": 31.0, "resets_at": WEEK_RESET,
                      "source": "statusline", "updated_at": NOW - 20},
        "seven_day_fable": {"used_percentage": 41.0, "resets_at": WEEK_RESET,
                            "source": "api", "updated_at": NOW - 120},
    }
    c["history"] = {"five_hour": {"window": NOW - 56000, "samples": [[NOW - 57000, 0.0]]}}
    c["notified"] = {"five_hour": {"window": NOW - 56000, "levels": [90, 100]}}
    c["api"]["last_success_at"] = NOW - 120
    return c


class PruneUnreported(unittest.TestCase):
    def test_drops_bucket_the_api_no_longer_lists(self):
        c = cache_with_dead_five_hour()
        dropped = common.prune_unreported(c, {"seven_day", "seven_day_fable"}, NOW)
        self.assertEqual(dropped, ["five_hour"])
        self.assertNotIn("five_hour", c["limits"])
        self.assertNotIn("five_hour", c["history"])
        self.assertNotIn("five_hour", c["notified"])
        self.assertIn("seven_day", c["limits"])
        self.assertIn("seven_day_fable", c["limits"])

    def test_keeps_bucket_another_producer_still_feeds(self):
        c = cache_with_dead_five_hour()
        c["limits"]["five_hour"]["updated_at"] = NOW - 30  # statusLine just wrote it
        self.assertEqual(common.prune_unreported(c, {"seven_day"}, NOW), [])
        self.assertIn("five_hour", c["limits"])

    def test_empty_report_prunes_nothing(self):
        # An unexpected payload shape must not wipe the cache.
        c = cache_with_dead_five_hour()
        self.assertEqual(common.prune_unreported(c, set(), NOW), [])
        self.assertIn("five_hour", c["limits"])


class LimitsStale(unittest.TestCase):
    def test_missing_five_hour_is_not_stale(self):
        c = cache_with_dead_five_hour()
        del c["limits"]["five_hour"]
        self.assertFalse(collector.limits_stale(c, collector.STALE_ACTIVE_S, NOW))

    def test_present_but_dead_bucket_is_stale(self):
        # Still triggers one API call, which is what prunes it.
        c = cache_with_dead_five_hour()
        self.assertTrue(collector.limits_stale(c, collector.STALE_ACTIVE_S, NOW))

    def test_stale_week_is_stale(self):
        c = cache_with_dead_five_hour()
        del c["limits"]["five_hour"]
        c["limits"]["seven_day"]["updated_at"] = NOW - 3600
        self.assertTrue(collector.limits_stale(c, collector.STALE_ACTIVE_S, NOW))


class CcstatuslineFiles(unittest.TestCase):
    """Isolated ~/.cache/ccstatusline with both the legacy and the
    hashed (>= 2.2.34) file names available."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        orig = collector.CCSTATUSLINE_DIR
        collector.CCSTATUSLINE_DIR = self.dir.name
        self.addCleanup(setattr, collector, "CCSTATUSLINE_DIR", orig)
        orig_env = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = "/home/someone/.claude"
        self.addCleanup(self._restore_env, orig_env)
        self.hashed, self.legacy = collector.ccstatusline_paths("json")

    @staticmethod
    def _restore_env(value):
        if value is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = value

    def write_file(self, path, data, mtime=NOW):
        with open(path, "w") as f:
            json.dump(data, f)
        os.utime(path, (mtime, mtime))


class CcstatuslinePaths(CcstatuslineFiles):
    def test_hash_matches_ccstatusline(self):
        # sha256("/home/someone/.claude")[:16], as ccstatusline computes it.
        import hashlib
        digest = hashlib.sha256(b"/home/someone/.claude").hexdigest()[:16]
        self.assertEqual(os.path.basename(self.hashed), f"usage-{digest}.json")
        self.assertEqual(os.path.basename(self.legacy), "usage.json")

    def test_no_cache(self):
        self.assertIsNone(collector.ccstatusline_cache_path())

    def test_newest_name_wins(self):
        self.write_file(self.legacy, {}, mtime=NOW - 600)
        self.write_file(self.hashed, {}, mtime=NOW)
        self.assertEqual(collector.ccstatusline_cache_path(), self.hashed)
        os.utime(self.legacy, (NOW + 1, NOW + 1))
        self.assertEqual(collector.ccstatusline_cache_path(), self.legacy)

    def test_lock_takes_latest_sane_deadline(self):
        lock_hashed, lock_legacy = collector.ccstatusline_paths("lock")
        self.write_file(lock_legacy, {"blockedUntil": collector.now() + 60})
        self.write_file(lock_hashed, {"blockedUntil": collector.now() + 300})
        self.assertGreaterEqual(collector.ccstatusline_blocked_until(), collector.now() + 290)

    def test_bogus_lock_deadline_is_ignored(self):
        lock_hashed, _ = collector.ccstatusline_paths("lock")
        self.write_file(lock_hashed, {"blockedUntil": collector.now() + 10 * 86400})
        self.assertEqual(collector.ccstatusline_blocked_until(), 0)


class MergeCcstatusline(CcstatuslineFiles):
    def write(self, data):
        self.write_file(self.hashed, data)

    def test_session_zero_without_reset_is_ignored(self):
        # ccstatusline writes sessionUsage 0 when the API has no session window.
        self.write({"sessionUsage": 0, "weeklyUsage": 32,
                    "weeklyResetAt": "2026-11-13T19:59:59+00:00"})
        c = common.default_cache()
        self.assertTrue(collector.merge_ccstatusline(c, NOW))
        self.assertNotIn("five_hour", c["limits"])
        self.assertEqual(c["limits"]["seven_day"]["used_percentage"], 32.0)

    def test_session_with_reset_is_still_merged(self):
        self.write({"sessionUsage": 12, "sessionResetAt": "2026-11-13T19:59:59+00:00"})
        c = common.default_cache()
        self.assertTrue(collector.merge_ccstatusline(c, NOW))
        self.assertEqual(c["limits"]["five_hour"]["used_percentage"], 12.0)


class PruneKeepsLiveWindows(unittest.TestCase):
    def test_running_window_survives_an_api_that_does_not_list_it(self):
        # 2026-11-18: the statusLine reports the 5-hour window again, the API
        # doesn't. Its last value stays true until the reset.
        c = cache_with_dead_five_hour()
        c["limits"]["five_hour"] = {"used_percentage": 25.0, "resets_at": NOW + 3600,
                                    "source": "statusline", "updated_at": NOW - 3000}
        self.assertEqual(common.prune_unreported(c, {"seven_day", "seven_day_fable"}, NOW), [])
        self.assertIn("five_hour", c["limits"])

    def test_running_window_the_api_cannot_refresh_is_not_stale(self):
        c = cache_with_dead_five_hour()
        c["limits"]["five_hour"] = {"used_percentage": 25.0, "resets_at": NOW + 3600,
                                    "source": "statusline", "updated_at": NOW - 3000}
        self.assertTrue(collector.limits_stale(c, collector.STALE_ACTIVE_S, NOW))
        c["api"]["reported"] = ["seven_day", "seven_day_fable"]
        self.assertFalse(collector.limits_stale(c, collector.STALE_ACTIVE_S, NOW))
        # ...but once it has ended it is stale again, which gets it pruned.
        c["limits"]["five_hour"]["resets_at"] = NOW - 60
        self.assertTrue(collector.limits_stale(c, collector.STALE_ACTIVE_S, NOW))


class StatuslineSessions(unittest.TestCase):
    """Two Claude Code sessions open: the busy one reports fresh numbers,
    the idle one keeps repeating what it saw an hour ago."""

    RESET = NOW + 4 * 3600

    def report(self, cache, session, pct, ts, resets_at=None, key="five_hour"):
        win = {key: {"used_percentage": pct, "resets_at": resets_at or self.RESET}}
        return common.record_statusline(cache, session, win, ts)

    def pct(self, cache, key="five_hour"):
        return cache["limits"][key]["used_percentage"]

    def test_idle_session_does_not_overwrite_the_busy_one(self):
        c = common.default_cache()
        self.report(c, "idle", 4, NOW)
        self.report(c, "busy", 4, NOW + 1)
        self.report(c, "busy", 25, NOW + 10)
        self.assertEqual(self.pct(c), 25.0)
        changed, applied = self.report(c, "idle", 4, NOW + 20)
        self.assertEqual((changed, applied), (False, False))
        self.assertEqual(self.pct(c), 25.0)
        self.assertEqual([p for _t, p in c["history"]["five_hour"]["samples"]], [4.0, 25.0])

    def test_first_report_of_an_unknown_session_below_current_is_old(self):
        c = common.default_cache()
        self.report(c, "busy", 25, NOW)
        self.report(c, "resumed", 4, NOW + 5)
        self.assertEqual(self.pct(c), 25.0)

    def test_first_report_of_an_unknown_session_above_current_counts(self):
        c = common.default_cache()
        self.report(c, "a", 25, NOW)
        self.report(c, "b", 30, NOW + 5)
        self.assertEqual(self.pct(c), 30.0)

    def test_a_new_observation_counts_even_when_lower(self):
        c = common.default_cache()
        self.report(c, "a", 25, NOW)
        self.report(c, "a", 20, NOW + 5)
        self.assertEqual(self.pct(c), 20.0)

    def test_repeated_higher_value_does_not_undo_an_api_correction(self):
        c = common.default_cache()
        self.report(c, "a", 25, NOW)
        common.set_limit(c, "five_hour", 20, self.RESET, "api", ts=NOW + 30)
        self.report(c, "a", 25, NOW + 40)
        self.assertEqual(self.pct(c), 20.0)

    def test_older_window_is_ignored_and_newer_window_wins(self):
        c = common.default_cache()
        self.report(c, "busy", 3, NOW, resets_at=self.RESET + 5 * 3600)
        self.report(c, "idle", 80, NOW + 5, resets_at=self.RESET)
        self.assertEqual(self.pct(c), 3.0)
        self.report(c, "idle", 1, NOW + 9, resets_at=self.RESET + 10 * 3600)
        self.assertEqual(self.pct(c), 1.0)

    def test_window_that_already_ended_is_ignored(self):
        c = common.default_cache()
        changed, applied = self.report(c, "idle", 80, NOW, resets_at=NOW - 60)
        self.assertFalse(applied)
        self.assertNotIn("five_hour", c["limits"])

    def test_same_value_is_confirmed_at_most_once_a_minute(self):
        c = common.default_cache()
        self.report(c, "a", 25, NOW)
        self.assertEqual(self.report(c, "a", 25, NOW + 10), (False, False))
        self.assertEqual(c["limits"]["five_hour"]["updated_at"], NOW)
        self.assertEqual(self.report(c, "a", 25, NOW + common.HEARTBEAT_S), (True, False))
        self.assertEqual(c["limits"]["five_hour"]["updated_at"], NOW + common.HEARTBEAT_S)
        self.assertEqual(c["session_seen_at"], NOW + common.HEARTBEAT_S)

    def test_idle_session_matching_an_api_value_keeps_it_fresh(self):
        c = common.default_cache()
        common.set_limit(c, "seven_day", 8, WEEK_RESET, "api", ts=NOW)
        self.report(c, "a", 8, NOW + 90, resets_at=WEEK_RESET, key="seven_day")
        self.assertEqual(c["limits"]["seven_day"]["updated_at"], NOW + 90)

    def test_sessions_not_seen_for_a_day_are_forgotten(self):
        c = common.default_cache()
        self.report(c, "old", 4, NOW)
        self.report(c, "new", 5, NOW + common.SESSION_TTL_S + 1, resets_at=NOW + 2 * 86400)
        self.assertEqual(list(c["sessions"]), ["new"])


class SweepTmp(unittest.TestCase):
    def test_removes_only_old_temp_files(self):
        with tempfile.TemporaryDirectory() as d:
            orig = common.CACHE_PATH
            common.CACHE_PATH = os.path.join(d, "usage.json")
            self.addCleanup(setattr, common, "CACHE_PATH", orig)
            old, fresh, cache = (common.CACHE_PATH + ".tmp-1", common.CACHE_PATH + ".tmp-2",
                                 common.CACHE_PATH)
            for path in (old, fresh, cache):
                open(path, "w").close()
            os.utime(old, (NOW - 7200, NOW - 7200))
            os.utime(fresh, (NOW - 60, NOW - 60))
            os.utime(cache, (NOW - 7200, NOW - 7200))
            self.assertEqual(common.sweep_tmp(NOW), 1)
            self.assertEqual(sorted(os.listdir(d)), ["usage.json", "usage.json.tmp-2"])


class History(unittest.TestCase):
    def test_flat_stretch_keeps_two_samples_and_changes_are_always_kept(self):
        c = common.default_cache()
        for i in range(10):
            common.add_sample(c, "seven_day", 4.0, WEEK_RESET, NOW + i * 600)
        common.add_sample(c, "seven_day", 5.0, WEEK_RESET, NOW + 5401)
        common.add_sample(c, "seven_day", 6.0, WEEK_RESET, NOW + 5402)
        self.assertEqual(c["history"]["seven_day"]["samples"],
                         [[NOW, 4.0], [NOW + 5400, 4.0], [NOW + 5401, 5.0], [NOW + 5402, 6.0]])


if __name__ == "__main__":
    unittest.main()
