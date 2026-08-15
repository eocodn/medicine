from __future__ import annotations

import argparse
import fcntl
import json
import sys
from pathlib import Path

from .dataset import DatasetError, load_dataset
from .fixed_eval import (
    audit_fixed_eval_reference_compatibility,
    build_fixed_eval_plan,
    evaluate_fixed_predictions,
    infer_list_text,
    parse_infer_predictions,
)
from .full_document_cli import load_selected_recognizer
from .runner_io import json_file, sha256_file, stream_command, verify_sha, write_json_atomic


def run_fixed_eval(args: argparse.Namespace) -> dict:
    upstream = json_file(Path(args.upstream).resolve())
    if upstream.get("pin_status") != "training-smoke-verified":
        raise DatasetError("training runtime has not passed the pinned smoke gate")
    source_root = Path(args.paddleocr_root).resolve()
    paddle_info = upstream["paddleocr"]
    dictionary_path = source_root / paddle_info["dictionary_path"]
    verify_sha(dictionary_path, paddle_info["dictionary_sha256"], "PaddleOCR dictionary")
    inference_script = source_root / "tools" / "infer_rec.py"
    if not inference_script.is_file():
        raise DatasetError(f"PaddleOCR recognizer inference script is missing: {inference_script}")

    recognizer = load_selected_recognizer(args.baseline_result)
    expected_sha = args.expected_checkpoint_sha256
    if expected_sha and recognizer["checkpoint_sha256"] != expected_sha:
        raise DatasetError("selected recognizer checkpoint SHA-256 does not match --expected-checkpoint-sha256")
    dataset = load_dataset(args.manifest)
    compatibility = audit_fixed_eval_reference_compatibility(
        dataset,
        dictionary_path,
        max_text_length=upstream["model_contract"]["max_text_length"],
        use_space_char=upstream["model_contract"]["use_space_char"],
    )
    if compatibility["critical"]["status"] != "ok":
        raise DatasetError("fixed evaluation critical medication labels are incompatible with selected recognizer contract")
    plan = build_fixed_eval_plan(dataset, minimum_required_count=args.minimum_required_count)

    run_dir = Path(args.run_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    result_path = run_dir / "fixed-eval-result.json"
    state_path = run_dir / "fixed-eval-state.json"
    plan_path = run_dir / "fixed-eval-plan.json"
    infer_list_path = run_dir / "infer-list.txt"
    inference_output_path = run_dir / "recognition.txt"
    predictions_path = run_dir / "predictions.jsonl"
    log_path = run_dir / "recognition.log"
    profile = {
        "schema_version": 1,
        "dataset_id": dataset.manifest["dataset_id"],
        "dataset_fingerprint": dataset.fingerprint,
        "drug_assignment_seed": plan["drug_assignment_seed"],
        "drug_assignment_sha256": plan["drug_assignment_sha256"],
        "recognition_evaluation_policy": plan["recognition_evaluation_policy"],
        "minimum_required_count": args.minimum_required_count,
        "baseline_result_sha256": sha256_file(recognizer["result_path"]),
        "checkpoint_sha256": recognizer["checkpoint_sha256"],
        "config_sha256": recognizer["config_sha256"],
        "paddleocr_commit": paddle_info["commit"],
        "infer_rec_sha256": sha256_file(inference_script),
        "device": args.device,
    }

    lock_path = run_dir / ".fixed-eval.lock"
    with lock_path.open("a+b") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise DatasetError(f"fixed evaluation is already active in {run_dir}") from exc

        if state_path.exists():
            state = json_file(state_path)
            if state.get("profile") != profile:
                raise DatasetError("fixed evaluation state profile does not match requested inputs")
            if state.get("status") == "completed":
                result = json_file(result_path)
                if result.get("profile") != profile or result.get("status") != "ok":
                    raise DatasetError("completed fixed evaluation state/result disagree")
                prediction_sha = result.get("predictions_sha256")
                if not isinstance(prediction_sha, str) or sha256_file(predictions_path) != prediction_sha:
                    raise DatasetError("completed fixed evaluation prediction artifact SHA-256 mismatch")
                return result
        else:
            stale = [path for path in run_dir.iterdir() if path.name != lock_path.name]
            if stale:
                raise DatasetError("fixed evaluation output directory is non-empty without authoritative state")
            state = {"schema_version": 1, "status": "initializing", "profile": profile}
            write_json_atomic(state_path, state)

        write_json_atomic(plan_path, plan)
        infer_list_path.write_text(infer_list_text(dataset), encoding="utf-8", newline="\n")
        state["status"] = "inferencing"
        write_json_atomic(state_path, state)
        command = [
            sys.executable,
            "tools/infer_rec.py",
            "-c",
            str(recognizer["config"]),
            "-o",
            f"Global.use_gpu={'True' if args.device == 'gpu' else 'False'}",
            "Global.distributed=False",
            f"Global.checkpoints={recognizer['checkpoint']}",
            f"Global.infer_img={dataset.root}",
            f"Global.infer_list={infer_list_path}",
            f"Global.save_res_path={inference_output_path}",
        ]
        state["command"] = command
        write_json_atomic(state_path, state)
        stream_command(command, cwd=source_root, log_path=log_path, capture=False, echo=False)
        if not inference_output_path.is_file():
            raise DatasetError("fixed evaluation recognizer did not produce recognition.txt")

        predictions = parse_infer_predictions(dataset, inference_output_path.read_text(encoding="utf-8"))
        by_id = {sample["id"]: sample for sample in dataset.samples}
        rows = []
        for sample_id in sorted(predictions):
            prediction = predictions[sample_id]
            rows.append(json.dumps({
                "id": sample_id,
                "image": by_id[sample_id]["image"],
                "reference": by_id[sample_id]["text"],
                "prediction": prediction["text"],
                "score": prediction["score"],
            }, ensure_ascii=False, sort_keys=True))
        predictions_path.write_text("\n".join(rows) + "\n", encoding="utf-8", newline="\n")
        metrics = evaluate_fixed_predictions(dataset, predictions, plan)
        result = {
            "schema_version": 1,
            "status": "ok",
            "profile": profile,
            "plan": str(plan_path),
            "plan_sha256": sha256_file(plan_path),
            "predictions": str(predictions_path),
            "predictions_sha256": sha256_file(predictions_path),
            "inference_output_sha256": sha256_file(inference_output_path),
            "metrics": metrics,
            "reference_compatibility": compatibility,
        }
        write_json_atomic(result_path, result)
        state["status"] = "completed"
        state["result"] = str(result_path)
        write_json_atomic(state_path, state)
        return result
