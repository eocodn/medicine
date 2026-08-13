from pathlib import Path
import unittest


class DeploymentConfigTest(unittest.TestCase):
    def test_android_build_packages_canonical_snapshot_without_alias_refresh(self) -> None:
        compose = Path("compose.yaml").read_text()
        self.assertIn("from medicine_canonical.mobile import build_mobile_database", compose)
        self.assertIn('build_mobile_database("data/db/canonical.sqlite", "data/db/mobile.sqlite")', compose)
        self.assertNotIn("ingredient-aliases --write", compose)
        self.assertNotIn("data/db/dur.sqlite", compose.split("android:", 1)[1])


if __name__ == "__main__":
    unittest.main()
