import pytest

from etl.dataset import PROJECT_ROOT
from evals.provenance import code_fingerprint, results_dir


def test_결과는_버전_폴더에만_쓴다() -> None:
    assert results_dir("v1") == PROJECT_ROOT / "results" / "v1"
    for bad in ("archive", "v", "v1/../archive", "1"):
        with pytest.raises(ValueError):
            results_dir(bad)


def test_코드_지문은_같은_코드면_같다() -> None:
    assert code_fingerprint() == code_fingerprint()
