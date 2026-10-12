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

    def test_release_documents_mac_and_windows_without_local_dev_metadata(self):
        version = re.search(r"^version: (\S+)$", (ROOT / "plugin.yaml").read_text(), re.M).group(1)
        self.assertRegex(version, r"^\d+\.\d+\.\d+$")
        readme = (ROOT / "README.md").read_text()
        macos = (ROOT / "docs/MACOS.md").read_text()
        submission = (ROOT / "docs/STORE_SUBMISSION.md").read_text()
        self.assertIn(f"Windows and macOS beta {version}", readme)
        self.assertIn(f"macOS setup — Usage Ledger {version}", macos)
        self.assertIn("platforms: [windows, macos]", submission)
        self.assertNotIn("0.4.4.dev1", readme + macos + submission)


if __name__ == "__main__":
    unittest.main()
