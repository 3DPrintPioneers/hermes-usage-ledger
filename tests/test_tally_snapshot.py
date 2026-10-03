import unittest


class TallySnapshotTests(unittest.TestCase):
    def test_parses_current_and_running_metrics_from_latest_block(self):
        from tally_snapshot import parse_tally_snapshot

        text = """# Hermes usage tally

## Response — earlier
Input tokens: 999
Words Running tally: 999

## Current response
Updated: 2026-09-09T04:30:34-04:00
Input tokens: 12
Output tokens: 34
Cache-read tokens: unavailable
Cache-write tokens: 56
Reasoning tokens: 78
Canonical total tokens: 102
Expanded activity tokens: 180
Calls: 2
Thinking time: unavailable
Working time: 90.5 seconds (wall-clock proxy)
Word count: 10
Character count: 20
Coding character count: 3
Input Tokens Running tally: 112
Output Tokens Running tally: 334
Cache-read Tokens Running tally: unavailable
Cache-write Tokens Running tally: 556
Reasoning Tokens Running tally: 778
Canonical Total Tokens Running tally: 1002
Expanded Activity Tokens Running tally: 1780
Calls Running tally: 12
Thinking Time Running tally: unavailable
Working Time Running tally: 1090.5 seconds
Words Running tally: 110
Character Running tally: 220
Coding Character Running tally: 33
"""

        self.assertEqual(
            parse_tally_snapshot(text),
            {
                "updated": "2026-09-09T04:30:34-04:00",
                "working_time_missing_responses": None,
                "current": {
                    "input_tokens": 12,
                    "output_tokens": 34,
                    "cache_read_tokens": None,
                    "cache_write_tokens": 56,
                    "reasoning_tokens": 78,
                    "total_tokens": 102,
                    "expanded_activity_tokens": 180,
                    "calls": 2,
                    "thinking_seconds": None,
                    "working_seconds": 90.5,
                    "words": 10,
                    "characters": 20,
                    "coding_characters": 3,
                },
                "running": {
                    "input_tokens": 112,
                    "output_tokens": 334,
                    "cache_read_tokens": None,
                    "cache_write_tokens": 556,
                    "reasoning_tokens": 778,
                    "total_tokens": 1002,
                    "expanded_activity_tokens": 1780,
                    "calls": 12,
                    "thinking_seconds": None,
                    "working_seconds": 1090.5,
                    "words": 110,
                    "characters": 220,
                    "coding_characters": 33,
                },
            },
        )

    def test_unavailable_or_missing_metrics_are_none_not_zero(self):
        from tally_snapshot import parse_tally_snapshot

        snapshot = parse_tally_snapshot(
            """## Current response
Updated: 2026-09-09T04:30:34-04:00
Input tokens: not available
Word count: 0
Words Running tally: 0
"""
        )

        self.assertIsNone(snapshot["current"]["input_tokens"])
        self.assertEqual(snapshot["current"]["words"], 0)
        self.assertEqual(snapshot["running"]["words"], 0)
        self.assertIsNone(snapshot["running"]["calls"])

    def test_prefers_latest_current_response_block_over_later_unmanaged_sections(self):
        from tally_snapshot import parse_tally_snapshot

        text = """# Hermes usage tally

## Current response
Updated: 2026-09-09T06:00:00+00:00
Word count: 4
Words Running tally: 40

## Notes
Word count: 999
Words Running tally: 999
"""
        snapshot = parse_tally_snapshot(text)
        self.assertEqual(snapshot["updated"], "2026-09-09T06:00:00+00:00")
        self.assertEqual(snapshot["current"]["words"], 4)
        self.assertEqual(snapshot["running"]["words"], 40)


if __name__ == "__main__":
    unittest.main()
