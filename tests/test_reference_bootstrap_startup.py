from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ReferenceBootstrapStartupTest(unittest.TestCase):
    def test_app_startup_waits_for_reference_bootstrap_before_loading_data(self) -> None:
        source = (ROOT / "ui" / "src" / "app.ts").read_text(encoding="utf-8")

        bootstrap = source.index("await window.MedicineBootstrapUi?.ensureReady();")
        health = source.index("await loadHealth();")
        people = source.index("await loadPeople();")

        self.assertLess(bootstrap, health)
        self.assertLess(health, people)


if __name__ == "__main__":
    unittest.main()