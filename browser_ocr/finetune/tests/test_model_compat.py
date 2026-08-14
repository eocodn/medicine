from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from browser_ocr.finetune.dataset import load_dataset
from browser_ocr.finetune.model_compat import audit_model_compatibility

PNG = b"\x89PNG\r\n\x1a\n" + b"fixture"


class ModelCompatibilityAuditTest(unittest.TestCase):
    def make_dataset(self, root: Path, texts: list[str]) -> Path:
        samples=[]
        for i,text in enumerate(texts):
            image=f"img-{i}.png"
            (root/image).write_bytes(PNG)
            samples.append({
                "id": f"s{i}", "image": image, "image_sha256": hashlib.sha256(PNG).hexdigest(),
                "text": text, "origin": "synthetic", "document_type": "prescription", "document_id": f"d{i}",
                "groups": {"layout_family": f"l{i}", "source_family": f"src{i}", "drug_family": f"drug{i}"},
                "semantic_tags": ["product"], "risk_tags": [],
                "privacy": {"contains_patient_data": False, "deidentified": True},
                "provenance": {"source_id": "fixture", "license_id": "fixture"},
            })
        (root/'samples.jsonl').write_text('\n'.join(json.dumps(x,ensure_ascii=False) for x in samples)+'\n',encoding='utf-8')
        (root/'manifest.json').write_text(json.dumps({
            "schema_version":1,"dataset_id":"compat-fixture","task":"text_recognition","patient_data_policy":"forbid","samples_file":"samples.jsonl"
        }),encoding='utf-8')
        return root/'manifest.json'

    def test_length_and_dictionary_violations_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw)
            dataset=load_dataset(self.make_dataset(root,["가나다","가나다라마바","가X"]))
            dictionary=root/'dict.txt'; dictionary.write_text('가\n나\n다\n라\n마\n바\n',encoding='utf-8')
            report=audit_model_compatibility(dataset,dictionary,max_text_length=5,use_space_char=True)
            self.assertEqual(report['status'],'incompatible')
            self.assertEqual(report['overlength_sample_count'],1)
            self.assertEqual(report['unknown_characters'],{'X':1})

    def test_compatible_dataset_passes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw)
            dataset=load_dataset(self.make_dataset(root,["가나다","가 나"]))
            dictionary=root/'dict.txt'; dictionary.write_text('가\n나\n다\n',encoding='utf-8')
            report=audit_model_compatibility(dataset,dictionary,max_text_length=5,use_space_char=True)
            self.assertEqual(report['status'],'ok')
            self.assertEqual(report['overlength_sample_count'],0)
            self.assertEqual(report['unknown_characters'],{})


if __name__ == '__main__':
    unittest.main()
