"""공유용 데이터셋이 팀 저장소의 데이터 모델과 맞는지 확인한다.

팀 저장소 루트에서 (dev 또는 dev 를 받은 브랜치):
    PYTHONPATH=src uv run python <풀어 둔 폴더>/validate.py

확인하는 것
- works · documents · aliases 의 모든 행이 팀 모델(Work · Document · Alias)로 읽히고,
  모델로 읽었다가 다시 쓴 값이 파일의 행과 똑같다 (칸이 남거나 모자라지 않다)
- ID 가 겹치지 않고, 다른 표를 가리키는 값(work_id · parent_work_id · target_id · doc_id)이 모두 있다
- sections 의 team_section 이 팀 Section 값이고, 그 절로 팀 Chunk 를 만들면 ID 검사를 통과한다
하나라도 어긋나면 어느 행인지 말하고 멈춘다.
"""

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from shared.core.models import Alias, AliasKind, Chunk, Document, Work
from shared.core.schemas import Section


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def read_as[T: BaseModel](path: Path, model: type[T]) -> list[T]:
    """모든 행을 모델로 읽고, 모델이 다시 쓴 값이 파일의 행과 같은지 본다"""
    items: list[T] = []
    for number, row in enumerate(load_jsonl(path), 1):
        item = model.model_validate(row)
        if item.model_dump(mode="json") != row:
            raise ValueError(f"{path.name} {number}행이 {model.__name__} 모양과 다릅니다")
        items.append(item)
    return items


def unique(values: list[str], what: str) -> set[str]:
    repeated = [value for value, count in Counter(values).items() if count > 1]
    if repeated:
        raise ValueError(f"{what} 가 겹칩니다: {repeated[:5]}")
    return set(values)


def validate(root: Path) -> dict[str, int]:
    works = read_as(root / "works.jsonl", Work)
    documents = read_as(root / "documents.jsonl", Document)
    aliases = read_as(root / "aliases.jsonl", Alias)
    work_ids = unique([w.work_id for w in works], "work_id")
    doc_ids = unique([d.doc_id for d in documents], "doc_id")
    by_work = {w.work_id: w for w in works}
    by_doc = {d.doc_id: d for d in documents}

    for work in works:
        if work.parent_work_id is not None and work.parent_work_id not in work_ids:
            raise ValueError(f"parent_work_id 가 가리키는 작품이 없습니다: {work.work_id}")
        if (work.season is None) != (work.parent_work_id is None):
            raise ValueError(
                f"시즌 행에만 season · parent_work_id 가 있어야 합니다: {work.work_id}"
            )
    for document in documents:
        if document.work_id is not None and document.work_id not in work_ids:
            raise ValueError(f"work_id 가 가리키는 작품이 없습니다: {document.doc_id}")
    for alias in aliases:
        if alias.kind == AliasKind.WORK and alias.target_id not in work_ids:
            raise ValueError(f"target_id 가 가리키는 작품이 없습니다: {alias.alias_ko}")

    sections = load_jsonl(root / "sections.jsonl")
    unique([f"{s['doc_id']}#{s['section_index']}" for s in sections], "doc_id + section_index")
    chunks = 0
    for row in sections:
        doc = by_doc.get(row["doc_id"])
        if doc is None:
            raise ValueError(f"doc_id 가 가리키는 문서가 없습니다: {row['doc_id']}")
        if row["team_section"] is None or not row["text"]:
            continue
        owner = by_work.get(doc.work_id or "")
        section = Section(row["team_section"])
        Chunk(  # 이 절을 한 청크로 만들었을 때 팀 ID 규칙을 통과하는가
            chunk_id=f"{doc.doc_id}-{section}-p01",
            doc_id=doc.doc_id,
            work_id=doc.work_id,
            source_id=doc.source_id,
            section=section,
            position=1,
            total=1,
            text=row["text"],
            header="",
            content_hash="",
            title_ko=owner.title_ko if owner else None,
            title_original=owner.title_original if owner else None,
            source_url=doc.source_url,
            revid=doc.revid,
        )
        chunks += 1

    for name, key, known in (
        ("works_meta.jsonl", "work_id", work_ids),
        ("documents_meta.jsonl", "doc_id", doc_ids),
        ("characters.jsonl", "doc_id", doc_ids),
    ):
        rows = load_jsonl(root / "extra" / name)
        missing = [row[key] for row in rows if row[key] not in known]
        if missing:
            raise ValueError(f"extra/{name} 의 {key} 가 표에 없습니다: {missing[:5]}")
    for name, total in (("works_meta.jsonl", len(works)), ("documents_meta.jsonl", len(documents))):
        if len(load_jsonl(root / "extra" / name)) != total:
            raise ValueError(f"extra/{name} 의 행 수가 표와 다릅니다")

    return {
        "works": len(works),
        "documents": len(documents),
        "aliases": len(aliases),
        "sections": len(sections),
        "sections_checked_as_chunk": chunks,
    }


if __name__ == "__main__":
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent
    print(json.dumps(validate(folder), ensure_ascii=False, indent=2))
    print("팀 모델과 맞습니다")
