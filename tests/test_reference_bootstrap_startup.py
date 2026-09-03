from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ReferenceBootstrapStartupTest(unittest.TestCase):
    def test_app_startup_waits_for_reference_bootstrap_before_loading_data(self) -> None:
        source = (ROOT / "ui" / "src" / "app.ts").read_text(encoding="utf-8")

        bootstrap = source.index("await window.MedicineBootstrapUi?.ensureReady();")
        people = source.index("await loadPeople();")

        self.assertLess(bootstrap, people)
        self.assertNotIn("await loadHealth();", source)


if __name__ == "__main__":
    unittest.main()