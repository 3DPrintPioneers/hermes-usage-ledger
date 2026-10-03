import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ReleaseMetadataTests(unittest.TestCase):
    def test_submission_version_matches_both_manifests(self):
        version = re.search(r"^version: (\S+)$", (ROOT / "plugin.yaml").read_text(), re.M).group(1)
        dashboard = json.loads((ROOT / "dashboard/manifest.json").read_text())
        submission = (ROOT / "docs/STORE_SUBMISSION.md").read_text()
        self.assertEqual(dashboard["version"], version)
        self.assertIn(f"Current package version: `{version}`", submission)


if __name__ == "__main__":
    unittest.main()
