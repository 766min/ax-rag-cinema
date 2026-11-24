"""Validate or import the local Cineverse handoff ZIP."""

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

DEFAULT_ZIP = Path("data/local/cineverse_handoff/cineverse-integrated-dataset-handoff.zip")
DEFAULT_REPORT = Path("data/local/cineverse_handoff/import-report.json")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", type=Path, default=DEFAULT_ZIP, help="handoff ZIP path")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT, help="dry-run report path")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write metadata and embeddings; omission performs a safe dry-run",
    )
    parser.add_argument("--batch-size", type=int, default=64)
    return parser.parse_args()


def main() -> None:
    from ingest.handoff import adapt_handoff

    args = parse_args()
    if not args.zip.is_file():
        raise SystemExit(f"handoff ZIP을 찾을 수 없습니다: {args.zip}")
    if args.batch_size < 1:
        raise SystemExit("--batch-size는 1 이상이어야 합니다.")

    bundle = adapt_handoff(args.zip)
    report = bundle.report.as_dict()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if not args.apply:
        print(f"dry-run complete: {args.report}")
        return

    from ingest.store import import_bundle

    result = import_bundle(bundle, str(args.zip.resolve()), batch_size=args.batch_size)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
