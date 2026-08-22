from __future__ import annotations

import argparse
import json
import sys

from .graph_training_paddle import GraphTrainingConfig, run_graph_training


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ocr-parser-train",
        description="Train the mobile sparse document-graph medication parser",
    )
    parser.add_argument("--train-manifest", action="append", required=True)
    parser.add_argument("--val-manifest", action="append", required=True)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--epochs", type=int, default=12)
    parser.add_argument("--learning-rate", type=float, default=0.003)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--seed", type=int, default=112)
    parser.add_argument("--hidden-dim", type=int, default=96)
    parser.add_argument("--layers", type=int, default=2)
    parser.add_argument("--neighbor-count", type=int, default=12)
    parser.add_argument("--pair-hidden-dim", type=int, default=64)
    parser.add_argument("--relation-loss-weight", type=float, default=1.0)
    parser.add_argument("--max-relation-pos-weight", type=float, default=8.0)
    parser.add_argument("--device", choices=("gpu", "cpu"), default="gpu")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = GraphTrainingConfig(
            epochs=args.epochs,
            learning_rate=args.learning_rate,
            weight_decay=args.weight_decay,
            seed=args.seed,
            hidden_dim=args.hidden_dim,
            layers=args.layers,
            neighbor_count=args.neighbor_count,
            pair_hidden_dim=args.pair_hidden_dim,
            relation_loss_weight=args.relation_loss_weight,
            max_relation_pos_weight=args.max_relation_pos_weight,
            device=args.device,
        )
        result = run_graph_training(
            train_manifests=args.train_manifest,
            val_manifests=args.val_manifest,
            run_dir=args.run_dir,
            config=config,
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=bool(args.json), indent=None if args.json else 2))
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        if args.json:
            print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_parser", "main"]