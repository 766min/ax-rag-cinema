"""영민 시험 파이프라인을 처음부터 끝까지 돌리고, 단계마다 팀 로거로 기록한다.

실행 (저장소 루트에서):
    uv run --with sentence-transformers==5.2.0 python test/youngmin/run.py   # 전부 (C · C+ 의 재정렬 모델 때문에 --with)
    uv run python test/youngmin/run.py --until load    # 적재까지만

순서는 architecture.md §4 (그림 3) — 청크 · 작품 카드 · 연결 카드를 모두 만든 뒤 적재한다.
테스트가 하나라도 실패하면 뒤 단계를 돌리지 않는다.

기록
- logs/youngmin.log — 팀 shared.core.logger (비밀값은 *** 로 가려진다). 단계마다 걸린 시간은
  shared.core.timing.measure_time 이 남긴다.
- results/{run_id}.jsonl — 팀 evals.recorder 의 RunRecord (시험 평가 단계, 구성마다 파일 하나)
- test/youngmin/data/index/compare.md — 구성 비교표
"""

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for path in (ROOT / "src", HERE / "chunking", HERE / "index"):
    sys.path.insert(0, str(path))

from shared.core.logger import get_logger, setup_logging  # noqa: E402
from shared.core.timing import measure_time  # noqa: E402

LOG_FILE = "logs/youngmin.log"
STEPS = ["tests", "chunks", "cards", "connections", "gold", "load", "eval"]
MODES = ["A", "B", "C", "C+"]  # 시험 평가 단계에서 돌릴 구성 (§5)
log = get_logger("youngmin.run")
SUMMARY = re.compile(r"^=*\s*(\d+ (passed|failed|error).*?)\s*=*$")


def run_tests() -> dict[str, Any]:
    """팀 테스트(tests/)와 이 폴더의 테스트. 결과 줄과 실패한 테스트 이름을 남긴다"""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests", "test/youngmin"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    lines = result.stdout.splitlines()
    summary = next((m.group(1) for line in reversed(lines) if (m := SUMMARY.match(line))), "")
    failed = [line for line in lines if line.startswith(("FAILED", "ERROR"))]
    report = {"exit_code": result.returncode, "summary": summary, "failed": failed}
    if result.returncode != 0:
        log.error("테스트 실패: %s\n%s", summary, "\n".join(failed or lines[-30:]))
        raise SystemExit(result.returncode)
    return report


def steps() -> dict[str, Callable[[], dict[str, Any]]]:
    import build_cards
    import build_chunks
    import build_connections
    import check_gold
    import eval_run
    import load_index

    return {
        "tests": run_tests,
        "chunks": lambda: build_chunks.build("base"),
        "cards": build_cards.build,
        "connections": build_connections.build,
        "gold": lambda: check_gold.check("base"),
        "load": lambda: load_index.load("base"),
        "eval": lambda: eval_run.run_modes(MODES),
    }


def brief(report: dict[str, Any]) -> dict[str, Any]:
    """로그 한 줄에 들어갈 만큼만 (긴 목록은 개수로)"""
    return {
        k: (f"{len(v)}건" if isinstance(v, list) and len(v) > 5 else v) for k, v in report.items()
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="영민 시험 파이프라인")
    parser.add_argument("--until", choices=STEPS, default="eval", help="여기까지 돌린다")
    args = parser.parse_args()
    setup_logging(log_file=LOG_FILE)
    table = steps()
    results: dict[str, Any] = {}
    for name in STEPS[: STEPS.index(args.until) + 1]:
        log.info("단계 시작: %s", name)
        report = measure_time(label=f"step:{name}", logger=log)(table[name])()
        results[name] = brief(report)
        log.info("단계 결과: %s %s", name, json.dumps(results[name], ensure_ascii=False))
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
