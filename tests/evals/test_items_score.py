import json
from pathlib import Path

import pytest
from eval_fakes import CHUNKS, NO_ROW, ROW

from build.chunks import index_version
from evals.items import check_ready, load_queries, to_items


def test_gold_excerpt_maps_to_chunk_and_index_version() -> None:
    items, report = to_items([ROW, NO_ROW], CHUNKS)
    assert items[0].gold_chunk_ids == [CHUNKS[1].chunk_id]
    assert items[0].gold_index_version == index_version(CHUNKS)
    assert items[0].key_facts == ["갑옷 제작", "동굴 탈출"]
    assert items[1].gold_chunk_ids == [] and not items[1].answerable
    assert report[0]["evidence"][0]["whole_excerpt_in_one_chunk"] is True


def test_check_ready_refuses_other_index_version() -> None:
    items, _ = to_items([ROW], CHUNKS)
    check_ready(items, index_version(CHUNKS))
    with pytest.raises(ValueError, match="색인"):
        check_ready(items, "other")


def test_load_queries_reads_minji_format(tmp_path: Path) -> None:
    path = tmp_path / "q.json"
    path.write_text(json.dumps({"version": "t1", "queries": {"Q001": {"query_en": "What is X?"}}}))
    assert load_queries(path) == {"Q001": "What is X?"}
    jl = tmp_path / "q.jsonl"
    jl.write_text('{"question_id": "Q2", "query_en": "Y?"}\n')
    assert load_queries(jl) == {"Q2": "Y?"}
