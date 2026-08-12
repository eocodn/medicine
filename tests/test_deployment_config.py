from pathlib import Path
import unittest


class DeploymentConfigTest(unittest.TestCase):
    def test_android_build_refreshes_validated_aliases_before_mobile_database(self) -> None:
        compose = Path("compose.yaml").read_text()
        alias_refresh = "python3.12 -m medicine_catalog.cli ingredient-aliases --write --json"
        mobile_build = "from medicine_dur.mobile import build_mobile_database"

        self.assertIn(alias_refresh, compose)
        self.assertLess(compose.index(alias_refresh), compose.index(mobile_build))


if __name__ == "__main__":
    unittest.main()
