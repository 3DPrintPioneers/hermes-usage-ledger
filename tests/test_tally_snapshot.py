import unittest


class TallySnapshotTests(unittest.TestCase):
    def test_finite_scientific_numbers_roundtrip_without_partial_or_zero_measurements(self):
        from tally_snapshot import parse_tally_snapshot

        cases = [('1e-05', 0.00001), ('1.25e-05', 0.0000125), ('1.5E+20', 1.5e20),
                 ('5e-324', 5e-324), ('0e+99', 0), ('1e309', None), ('1e-999', None),
                 ('-1e-05', None), ('NaN', None), ('inf', None), ('1.25e', None)]
        for text, expected in cases:
            with self.subTest(text=text):
                snapshot = parse_tally_snapshot('## Current response\nThinking time: ' + text + ' seconds\n'
                                                'Thinking Time Running tally: ' + text + ' seconds\n')
                self.assertEqual(snapshot['current']['thinking_seconds'], expected)
                self.assertEqual(snapshot['running']['thinking_seconds'], expected)

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


    def test_preserves_legacy_global_tally_before_history_sections(self):
        from tally_snapshot import parse_tally_snapshot

        text = """# Hermes global usage tally
Updated: 2026-01-01T00:00:00+00:00
Word count: 4
Character count: 22
Coding character count: 1
Thinking time: unavailable
Working time: 3.5 seconds (wall-clock proxy)
Tokens Input: 12
Tokens Output: 3
Tokens Cache Read: 2
Tokens Cache Write: 1
Tokens Reasoning: 1
Tokens Total: 18
Words Running tally: 40.
Character Running tally: 220.
Coding Character Running tally: 10.
Thinking Time Running tally: 0 seconds measured / 5 unavailable.
Working Time Running tally: 30.5 seconds measured / 2 unavailable.
Token Running Total Input (last known; one prior interval unmeasured): 120
Token Running Total Output (last known): 30
Token Running Total Cache Read (last known): 20
Token Running Total Cache Write (last known): 10
Token Running Total Reasoning (last known): 10
Token Running Total (last known; excludes reasoning): 180

## Older history
Response: a legacy history annotation, not the current numeric tally
"""
        snapshot = parse_tally_snapshot(text)
        self.assertEqual(snapshot['updated'], '2026-01-01T00:00:00+00:00')
        self.assertEqual(snapshot['current']['input_tokens'], 12)
        self.assertEqual(snapshot['current']['total_tokens'], 18)
        self.assertEqual(snapshot['running']['input_tokens'], 120)
        self.assertEqual(snapshot['running']['total_tokens'], 180)
        self.assertEqual(snapshot['running']['reasoning_tokens'], 10)
        self.assertEqual(snapshot['running']['words'], 40)
        self.assertEqual(snapshot['running']['working_seconds'], 30.5)
        self.assertIsNone(snapshot['current']['thinking_seconds'])
        self.assertIsNone(snapshot['running']['calls'])


    def test_legacy_activity_and_missing_work_are_derived_only_from_explicit_numbers(self):
        from tally_snapshot import parse_tally_snapshot

        snapshot = parse_tally_snapshot('''# Hermes global usage tally
Updated: fixture
Words Running tally: 40.
Tokens Total: 18
Tokens Reasoning: 1
Token Running Total (last known; excludes reasoning): 180
Token Running Total Reasoning (last known): 10
Working Time Running tally: 30.5 seconds measured / 2 unavailable.
''')
        self.assertEqual(snapshot['current']['expanded_activity_tokens'], 19)
        self.assertEqual(snapshot['running']['expanded_activity_tokens'], 190)
        self.assertEqual(snapshot['working_time_missing_responses'], 2)
        unavailable = parse_tally_snapshot('''# Hermes global usage tally
Updated: fixture
Words Running tally: 40.
Tokens Total: 18
Tokens Reasoning: unavailable
''')
        self.assertIsNone(unavailable['current']['expanded_activity_tokens'])


if __name__ == "__main__":
    unittest.main()
