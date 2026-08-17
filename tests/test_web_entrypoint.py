from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from medicine_app.web_entrypoint import prepare_personal_db_ownership


class WebEntrypointTest(unittest.TestCase):
    def test_new_personal_database_parent_chain_is_assigned_to_host_user(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            personal_db = root / "data" / "db" / "personal.sqlite"

            with patch("medicine_app.web_entrypoint.os.chown") as chown:
                prepare_personal_db_ownership(personal_db, 1234, 2345)

            self.assertTrue(personal_db.parent.is_dir())
            self.assertTrue((root / "data").is_dir())
            assigned = {call.args[0] for call in chown.call_args_list}
            self.assertIn(root / "data", assigned)
            self.assertIn(root / "data" / "db", assigned)
            for call in chown.call_args_list:
                self.assertEqual(call.args[1:], (1234, 2345))

    def test_existing_external_parent_is_not_reowned(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            personal_db = Path(temp_dir) / "personal.sqlite"

            with patch("medicine_app.web_entrypoint.os.chown") as chown:
                prepare_personal_db_ownership(personal_db, 1234, 2345)

            chown.assert_not_called()


if __name__ == "__main__":
    unittest.main()