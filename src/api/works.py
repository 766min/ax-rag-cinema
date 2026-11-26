"""영화 · 시리즈 정보 (보기만, 재생 없음). 팀 데이터셋(works · documents · sections)을 서버에서 읽는다.

데이터셋에는 IMDb 값이 있어 화면 빌드나 이미지에 넣지 않고, 서버가 필요한 칸만 골라 보낸다.
"""

import functools
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from build.chunks import Dataset
from shared.core.exceptions import NotFoundException, ServiceUnavailableException
from shared.core.schemas import APIResponse

router = APIRouter(tags=["works"])
SUMMARY_CHARS = 1200  # 상세 화면 소개 글 길이


class WorkSummary(BaseModel):
    work_id: str
    title_ko: str
    title_original: str
    format: str = Field(description="movie · tvSeries · tvMiniSeries 등")
    year: int | None
    phase: str | None
    timeline_order: int | None
    is_canon: bool | None


class WorkDetail(WorkSummary):
    summary: str = Field(description="영문 위키백과 첫 문단 (없으면 빈 값)")
    source_url: str | None = Field(description="소개 글 원문 주소")
    license: str | None = Field(description="소개 글 라이선스 (예: CC BY-SA 4.0)")


@functools.cache
def _dataset() -> Dataset:
    try:
        return Dataset.load()
    except FileNotFoundError as exc:
        raise ServiceUnavailableException("영화 데이터가 서버에 없습니다.") from exc


def _summary(work_id: str) -> WorkSummary:
    for w in _dataset().works:
        if w.work_id == work_id:
            return WorkSummary.model_validate(w.model_dump())
    raise NotFoundException("작품을 찾을 수 없습니다.")


def all_works() -> list[WorkSummary]:
    return [WorkSummary.model_validate(w.model_dump()) for w in _dataset().works]


def title_of(work_id: str) -> str:
    return _summary(work_id).title_ko


@router.get("/works")
def list_works(
    q: str = "",
    phase: str | None = None,
    canon: bool | None = True,
    sort: Literal["release", "timeline"] = "release",
) -> APIResponse[list[WorkSummary]]:
    """작품 목록. 기본은 MCU 정식 작품만, 공개 연도순."""
    needle = q.strip().casefold()
    works = [
        w
        for w in all_works()
        if (canon is None or w.is_canon == canon)
        and (phase is None or w.phase == phase)
        and (not needle or needle in w.title_ko.casefold() or needle in w.title_original.casefold())
    ]
    if sort == "timeline":
        works.sort(key=lambda w: (w.timeline_order is None, w.timeline_order or 0))
    else:
        works.sort(key=lambda w: (w.year is None, w.year or 0, w.title_original))
    return APIResponse.ok(works)


@router.get("/works/{work_id}")
def get_work(work_id: str) -> APIResponse[WorkDetail]:
    summary = _summary(work_id)
    ds = _dataset()
    doc = ds.documents.get(f"{work_id}-wikipedia")
    lead = next(
        (s["text"] for s in ds.sections.get(doc.doc_id, []) if s["section_path"] == ["(lead)"]),
        "",
    ) if doc else ""  # fmt: skip
    return APIResponse.ok(
        WorkDetail(
            **summary.model_dump(),
            summary=lead[:SUMMARY_CHARS],
            source_url=doc.source_url if doc else None,
            license=doc.license if doc else None,
        )
    )
