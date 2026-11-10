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


class MergeCcstatusline(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        self.addCleanup(os.unlink, self.tmp.name)
        self._orig = collector.CCSTATUSLINE_CACHE
        collector.CCSTATUSLINE_CACHE = self.tmp.name
        self.addCleanup(setattr, collector, "CCSTATUSLINE_CACHE", self._orig)

    def write(self, data):
        json.dump(data, self.tmp)
        self.tmp.close()
        os.utime(self.tmp.name, (NOW, NOW))

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


if __name__ == "__main__":
    unittest.main()
