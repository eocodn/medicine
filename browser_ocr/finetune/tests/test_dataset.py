from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from browser_ocr.finetune.dataset import (
    DatasetError,
    build_split,
    dataset_stats,
    export_paddle,
    load_dataset,
)


class DatasetFixture:
    def __init__(self, root: Path, samples: list[dict]):
        self.root = root
        (root / "images").mkdir(parents=True)
        for sample in samples:
            image = root / sample["image"]
            image.parent.mkdir(parents=True, exist_ok=True)
            payload = b"\x89PNG\r\n\x1a\nfixture"
            image.write_bytes(payload)
            sample["image_sha256"] = hashlib.sha256(payload).hexdigest()
        (root / "samples.jsonl").write_text(
            "".join(json.dumps(sample, ensure_ascii=False) + "\n" for sample in samples),
            encoding="utf-8",
        )
        self.manifest = root / "manifest.json"
        self.manifest.write_text(json.dumps({
            "schema_version": 1,
            "dataset_id": "medicine-rec-fixture",
            "task": "text_recognition",
            "patient_data_policy": "forbid",
            "samples_file": "samples.jsonl",
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sample(
    sample_id: str,
    *,
    text: str,
    document: str,
    layout: str,
    source: str,
    drug: str,
    origin: str = "synthetic",
    semantic_tags: list[str] | None = None,
    risk_tags: list[str] | None = None,
) -> dict:
    return {
        "id": sample_id,
        "image": f"images/{sample_id}.png",
        "text": text,
        "origin": origin,
        "document_type": "medication_bag",
        "document_id": document,
        "groups": {
            "layout_family": layout,
            "source_family": source,
            "drug_family": drug,
        },
        "semantic_tags": semantic_tags or [],
        "risk_tags": risk_tags or [],
        "privacy": {
            "contains_patient_data": False,
            "deidentified": True,
        },
        "provenance": {
            "source_id": f"fixture:{document}",
            "license_id": "generated-fixture",
        },
    }


class FineTuneDatasetTest(unittest.TestCase):
    def test_load_and_stats_preserve_mixed_script_and_safety_strata(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            fixture = DatasetFixture(root, [
                sample(
                    "mixed",
                    text="타이레놀 Tylenol 500mg",
                    document="doc-a",
                    layout="bag-a",
                    source="synthetic-pharmacy-a",
                    drug="acetaminophen",
                    semantic_tags=["product", "strength"],
                    risk_tags=["mixed_script", "exact_numeric"],
                ),
                sample(
                    "range",
                    text="1 ~ 2정",
                    document="doc-b",
                    layout="bag-b",
                    source="synthetic-pharmacy-b",
                    drug="ibuprofen",
                    semantic_tags=["dose"],
                    risk_tags=["ambiguous_range", "exact_numeric"],
                ),
            ])

            dataset = load_dataset(fixture.manifest)
            stats = dataset_stats(dataset)

            self.assertEqual(stats["sample_count"], 2)
            self.assertEqual(stats["document_count"], 2)
            self.assertEqual(stats["scripts"]["korean"], 2)
            self.assertEqual(stats["scripts"]["latin"], 1)
            self.assertEqual(stats["scripts"]["digit"], 2)
            self.assertEqual(stats["risk_tags"]["ambiguous_range"], 1)
            self.assertEqual(stats["semantic_tags"]["strength"], 1)

    def test_validation_fails_closed_on_patient_data_bad_labels_and_path_escape(self) -> None:
        base = sample(
            "bad", text="1정", document="doc-a", layout="bag-a",
            source="source-a", drug="drug-a",
        )
        cases = []
        patient = json.loads(json.dumps(base))
        patient["privacy"]["contains_patient_data"] = True
        cases.append(patient)
        tab = json.loads(json.dumps(base))
        tab["text"] = "1정\t3회"
        cases.append(tab)
        escape = json.loads(json.dumps(base))
        escape["image"] = "../outside.png"
        cases.append(escape)

        for bad in cases:
            with self.subTest(bad=bad):
                with tempfile.TemporaryDirectory() as raw:
                    root = Path(raw)
                    fixture = DatasetFixture(root, [bad])
                    with self.assertRaises(DatasetError):
                        load_dataset(fixture.manifest)

    def test_grouped_split_prevents_document_and_selected_axis_leakage(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            fixture = DatasetFixture(root, [
                # doc-1 connects drug-a and drug-b, so both drug families must remain together.
                sample("a1", text="약A", document="doc-1", layout="layout-1", source="src-1", drug="drug-a"),
                sample("b1", text="약B", document="doc-1", layout="layout-1", source="src-1", drug="drug-b"),
                sample("a2", text="약A 1정", document="doc-2", layout="layout-2", source="src-2", drug="drug-a"),
                sample("c1", text="약C", document="doc-3", layout="layout-3", source="src-3", drug="drug-c"),
                sample("d1", text="약D", document="doc-4", layout="layout-4", source="src-4", drug="drug-d"),
                sample("e1", text="약E", document="doc-5", layout="layout-5", source="src-5", drug="drug-e"),
                sample("f1", text="약F", document="doc-6", layout="layout-6", source="src-6", drug="drug-f"),
            ])
            dataset = load_dataset(fixture.manifest)

            first = build_split(dataset, group_by="drug_family", seed=112, ratios=(0.6, 0.2, 0.2))
            second = build_split(dataset, group_by="drug_family", seed=112, ratios=(0.6, 0.2, 0.2))
            self.assertEqual(first, second)

            by_id = {item["id"]: item for item in dataset.samples}
            sample_split = {}
            for split_name, ids in first["splits"].items():
                for sample_id in ids:
                    sample_split[sample_id] = split_name

            document_splits: dict[str, set[str]] = {}
            drug_splits: dict[str, set[str]] = {}
            for sample_id, split_name in sample_split.items():
                item = by_id[sample_id]
                document_splits.setdefault(item["document_id"], set()).add(split_name)
                drug_splits.setdefault(item["groups"]["drug_family"], set()).add(split_name)
            self.assertTrue(all(len(values) == 1 for values in document_splits.values()))
            self.assertTrue(all(len(values) == 1 for values in drug_splits.values()))
            self.assertEqual(sample_split["a1"], sample_split["a2"])
            self.assertEqual(sample_split["a1"], sample_split["b1"])

    def test_scale_stable_split_keeps_family_assignment_when_samples_grow(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            small_samples = []
            large_samples = []
            for family_index in range(17):
                source = f"synthetic-source-{family_index:02d}"
                first = sample(
                    f"small-{family_index:02d}",
                    text="1정",
                    document=f"small-doc-{family_index:02d}",
                    layout=f"layout-{family_index:02d}",
                    source=source,
                    drug=f"drug-{family_index:02d}",
                )
                small_samples.append(first)
                large_samples.append(json.loads(json.dumps(first)))
                large_samples.append(sample(
                    f"large-{family_index:02d}",
                    text="2정",
                    document=f"large-doc-{family_index:02d}",
                    layout=f"layout-extra-{family_index:02d}",
                    source=source,
                    drug=f"drug-extra-{family_index:02d}",
                ))

            small = load_dataset(DatasetFixture(root / "small", small_samples).manifest)
            large = load_dataset(DatasetFixture(root / "large", large_samples).manifest)
            small_split = build_split(
                small, group_by="source_family", seed=112, stable_across_scales=True,
            )
            large_split = build_split(
                large, group_by="source_family", seed=112, stable_across_scales=True,
            )

            def assignments(dataset, split):
                by_id = {item["id"]: item for item in dataset.samples}
                result = {}
                for split_name, ids in split["splits"].items():
                    for sample_id in ids:
                        family = by_id[sample_id]["groups"]["source_family"]
                        result.setdefault(family, set()).add(split_name)
                return {family: next(iter(names)) for family, names in result.items() if len(names) == 1}

            self.assertEqual(assignments(small, small_split), assignments(large, large_split))
            self.assertEqual(small_split["assignment"], "stable_family_hash_v1")
            self.assertEqual(large_split["assignment"], "stable_family_hash_v1")

    def test_union_find_handles_more_than_recursion_limit_in_one_group(self) -> None:
        from browser_ocr.finetune.dataset import _UnionFind

        groups = _UnionFind()
        for index in range(1500):
            groups.union(f"document:{index}", "source:shared")

        expected_root = groups.find("document:1499")
        self.assertEqual(groups.find("source:shared"), expected_root)
        self.assertEqual(groups.find("document:0"), expected_root)

    def test_paddle_export_is_atomic_machine_readable_and_tab_separated(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            fixture = DatasetFixture(root, [
                sample("a", text="타이레놀정 1정", document="doc-a", layout="l-a", source="s-a", drug="d-a"),
                sample("b", text="Tylenol 500mg", document="doc-b", layout="l-b", source="s-b", drug="d-b"),
                sample("c", text="1일 3회", document="doc-c", layout="l-c", source="s-c", drug="d-c"),
                sample("d", text="식후 30분", document="doc-d", layout="l-d", source="s-d", drug="d-d"),
                sample("e", text="오전 8시", document="doc-e", layout="l-e", source="s-e", drug="d-e"),
            ])
            dataset = load_dataset(fixture.manifest)
            split = build_split(dataset, group_by="layout_family", seed=7, ratios=(0.6, 0.2, 0.2))
            output = root / "paddle"

            report = export_paddle(dataset, split, output)

            self.assertEqual(report["schema_version"], 1)
            self.assertEqual(report["dataset_id"], "medicine-rec-fixture")
            self.assertEqual(report["sample_count"], 5)
            self.assertTrue((output / "split.json").is_file())
            for name in ("train", "val", "test"):
                label = output / f"{name}.txt"
                self.assertTrue(label.is_file())
                for line in label.read_text(encoding="utf-8").splitlines():
                    image_path, text = line.split("\t", 1)
                    self.assertTrue((root / image_path).is_file())
                    self.assertTrue(text)


if __name__ == "__main__":
    unittest.main()
