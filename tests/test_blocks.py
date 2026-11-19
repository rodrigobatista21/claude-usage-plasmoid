#!/usr/bin/env python3
"""The active block read from session logs must follow ccusage's rules.

Run with: python3 -m unittest discover -s tests
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "collector"))

import claude_usage_blocks as blocks  # noqa: E402

H = 3600
T0 = 1_795_050_000 - 1_795_050_000 % H    # on the hour


def line(ts, n=1, out=10, model="claude-opus-5-5", mid=None, rid=None):
    stamp = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return json.dumps({
        "timestamp": stamp, "requestId": rid or f"req-{ts}-{n}",
        "message": {"id": mid or f"msg-{ts}-{n}", "model": model,
                    "usage": {"input_tokens": 1, "output_tokens": out,
                              "cache_creation_input_tokens": 100,
                              "cache_read_input_tokens": 1000}},
    })


class Logs(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def write(self, name, lines, mtime):
        path = os.path.join(self.dir.name, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")
        os.utime(path, (mtime, mtime))
        return path


class ReadEntries(Logs):
    def test_skips_noise_and_duplicates(self):
        a = self.write("p/a.jsonl", [
            line(T0 + 60),
            '{"type":"user","timestamp":"2026-11-19T01:00:00.000Z","message":{"content":"hi"}}',
            "not json with \"usage\" and \"timestamp\"",
            line(T0 + 120, model="<synthetic>"),
        ], T0 + 120)
        # A resumed session repeats its parent's lines.
        b = self.write("p/sub/b.jsonl", [line(T0 + 60), line(T0 + 180)], T0 + 180)
        got = blocks.read_entries([a, b])
        self.assertEqual([e[0] for e in got], [T0 + 60, T0 + 180])
        self.assertEqual(got[0][1:], (1, 10, 100, 1000, "claude-opus-5-5"))

    def test_only_recent_files_are_opened(self):
        self.write("p/old.jsonl", [line(T0 - 200 * H)], T0 - 200 * H)
        new = self.write("p/new.jsonl", [line(T0)], T0)
        self.assertEqual(blocks.recent_files(self.dir.name, T0 - 96 * H), [new])
        self.assertEqual(len(blocks.recent_files(self.dir.name, None)), 2)


def entries(*stamps):
    return [(ts, 1, 10, 100, 1000, "m") for ts in stamps]


class LastBlock(unittest.TestCase):
    def test_block_starts_on_the_hour_of_its_first_request(self):
        b = blocks.last_block(entries(T0 + 600, T0 + 1200))
        self.assertEqual((b["start"], b["first"], b["last"], b["entries"]), (T0, T0 + 600, T0 + 1200, 2))
        self.assertEqual(b["tokens"], [2, 20, 200, 2000])

    def test_request_after_the_block_end_starts_the_next_block(self):
        # Requests every 2 h: no silence of 5 h, yet the blocks roll over.
        b = blocks.last_block(entries(*[T0 + 600 + i * 2 * H for i in range(4)]))
        self.assertEqual((b["start"], b["entries"]), (T0 + 6 * H, 1))

    def test_five_hours_of_silence_start_a_new_block(self):
        b = blocks.last_block(entries(T0 + 600, T0 + 600 + 5 * H + 1800))
        self.assertEqual((b["start"], b["entries"]), (T0 + 5 * H, 1))

    def test_unknown_past_without_a_silence_gives_no_answer(self):
        # The same requests as the rollover test, but cut so that the first
        # block is out of sight: the chain would be anchored on the wrong hour.
        stamps = [T0 + 600 + i * 2 * H for i in range(4)]
        self.assertIsNone(blocks.last_block(entries(*stamps[1:]), since=T0 + H))

    def test_silence_inside_the_known_span_anchors_the_chain(self):
        stamps = [T0 + 600, T0 + 8 * H + 600, T0 + 9 * H]
        b = blocks.last_block(entries(*stamps), since=T0)
        self.assertEqual((b["start"], b["entries"]), (T0 + 8 * H, 2))

    def test_silence_between_the_cutoff_and_the_first_request_counts(self):
        b = blocks.last_block(entries(T0 + 6 * H + 60), since=T0)
        self.assertEqual(b["start"], T0 + 6 * H)


class ActiveBlock(Logs):
    def test_reads_further_back_until_the_chain_is_anchored(self):
        # One request every 2 h for 10 days, with a single 6 h pause on day 2.
        stamps, ts = [], T0 - 240 * H + 600
        while ts <= T0:
            stamps.append(ts)
            ts += 6 * H if len(stamps) == 12 else 2 * H
        self.write("p/s.jsonl", [line(t) for t in stamps], stamps[-1])
        at = stamps[-1] + 300
        got, msg = blocks.active_block(self.dir.name, at)
        self.assertIn("read 720h", msg)
        self.assertTrue(got["active"])
        # Same answer as reading everything.
        full = blocks.last_block(blocks.read_entries([os.path.join(self.dir.name, "p/s.jsonl")]))
        self.assertEqual(got["start_time"], blocks._iso(full["start"]))
        self.assertEqual(got["entries"], full["entries"])

    def test_describes_the_running_block(self):
        self.write("p/s.jsonl", [line(T0 + 600), line(T0 + 1200, out=590)], T0 + 1200)
        got, _msg = blocks.active_block(self.dir.name, T0 + 1800)
        self.assertEqual(got["start_time"], blocks._iso(T0))
        self.assertEqual(got["end_time"], blocks._iso(T0 + 5 * H))
        self.assertEqual(got["token_counts"], {"input": 2, "output": 600, "cache_create": 200, "cache_read": 2000})
        self.assertEqual(got["total_tokens"], 2802)
        self.assertAlmostEqual(got["tokens_per_minute"], 60.2)       # (2 + 600) / 10 min
        self.assertEqual(got["projection"], {"remaining_minutes": 270, "total_tokens": 2802 + 75654})

    def test_no_block_when_the_last_request_is_old_or_the_block_ended(self):
        self.write("p/s.jsonl", [line(T0 + 600)], T0 + 600)
        self.assertEqual(blocks.active_block(self.dir.name, T0 + 600 + 5 * H)[0], {"active": False})
        self.assertEqual(blocks.active_block(self.dir.name, T0 + 5 * H + 1)[0], {"active": False})
        self.assertTrue(blocks.active_block(self.dir.name, T0 + 5 * H - 1)[0]["active"])

    def test_no_logs(self):
        self.assertEqual(blocks.active_block(self.dir.name, T0)[0], {"active": False})


if __name__ == "__main__":
    unittest.main()
