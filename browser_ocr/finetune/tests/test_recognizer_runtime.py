from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from browser_ocr.finetune.dataset import DatasetError
from browser_ocr.finetune.recognizer_runtime import _resolve_character_dictionary


class RecognizerRuntimeTest(unittest.TestCase):
    def test_relative_character_dictionary_is_resolved_from_paddleocr_root(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            dictionary = root / "ppocr" / "utils" / "dict" / "ppocrv5_korean_dict.txt"
            dictionary.parent.mkdir(parents=True)
            dictionary.write_text("가\n나\n", encoding="utf-8")
            config = {"character_dict_path": "./ppocr/utils/dict/ppocrv5_korean_dict.txt"}

            resolved = _resolve_character_dictionary(config, root)

            self.assertEqual(resolved, dictionary.resolve())
            self.assertEqual(config["character_dict_path"], str(dictionary.resolve()))

    def test_missing_character_dictionary_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config = {"character_dict_path": "./ppocr/utils/dict/missing.txt"}
            with self.assertRaisesRegex(DatasetError, "dictionary is missing"):
                _resolve_character_dictionary(config, Path(raw))


if __name__ == "__main__":
    unittest.main()
