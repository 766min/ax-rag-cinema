# cineverse-dataset-schema — 팀 데이터 모델에 맞춘 데이터셋 (2026-11-25)

팀 저장소 `dev` 의 데이터 모델(`src/shared/core/models.py` — `Work` · `Document` · `Alias`)로 **그대로 읽히는** 데이터셋이다. 2026-11-24 handoff 묶음과 재료는 같고, 모양을 팀 모델에 맞춰 다시 짰다. 본문은 원문(`raw/`)에서 빠진 글자를 되살린 판이다.

## 왜 다시 짰나

handoff 형식은 팀 모델로 읽히지 않는다.

| | handoff | 팀 모델 (`dev`) | 이 묶음 |
|---|---|---|---|
| 작품 ID | 378행 중 266행이 `cnt_…` | IMDb ID, 시즌은 `tt…-S1` | 전부 `tt…` · `tt…-S1` |
| 문서 ID | `doc_…` | `{work_id}-wikipedia` · `wp-{pageid}` | 팀 규칙대로. 원래 ID 는 `source_ids` 에 |
| 문서의 `work_id` | 850행 모두 비어 있음 | 연결된 작품 | 문서 전체가 한 작품인 103개에 채움 |
| `is_canon` | 문자열 `"unknown"` | `bool` | 공식 문서에 올라 있으면 `true` |
| `phase` | 숫자 | 문자열 | `"3"` |
| `timeline_order` | Disney **개봉** 순번이 들어 있음 | 시간순 순서 | Marvel.com 타임라인 순번 |
| `format` | `live_action` · `animation` | `movie` · `tvSeries` … | IMDb 형식 그대로 |
| `year` · `parent_work_id` | 없음 | 있음 | 채움 |

## 파일

| 파일 | 행 | 모양 |
|---|---|---|
| `works.jsonl` | 159 | 팀 `Work` |
| `documents.jsonl` | 850 | 팀 `Document` |
| `aliases.jsonl` | 112 | 팀 `Alias` |
| `sections.jsonl` | 12,476 | **본문.** 팀 모델에는 본문을 담는 표가 없어서(본문은 `Chunk` 에만 있다) 따로 둔다. `doc_id` 로 `documents` 와 잇는다 |
| `extra/` | — | 팀 모델에 칸이 없는 값 (아래) |
| `validate.py` | — | 이 묶음을 팀 모델로 검사하는 코드 |
| `BUILD_REPORT.json` · `SHA256SUMS.txt` · `scripts/` | — | 건수 · 파일 해시 · 만든 코드 |

앞의 세 파일은 팀 모델이 내보낸 값 그대로다. 칸이 남지도 모자라지도 않는다.

```python
from shared.core.models import Document, Work

works = [Work.model_validate_json(line) for line in open("works.jsonl", encoding="utf-8")]
documents = [Document.model_validate_json(line) for line in open("documents.jsonl", encoding="utf-8")]
```

**청크(`chunks`)는 넣지 않았다.** 청킹은 각자 시험하기로 했기 때문이다. `sections.jsonl` 의 절을 잘라 팀 `Chunk` 를 만들면 된다 (아래 "청크를 만들 때").

## 확인하는 법

저장소 루트(`dev` 를 받은 브랜치)에서:

```bash
PYTHONPATH=src uv run python <풀어 둔 폴더>/validate.py
```

모든 행이 팀 모델로 읽히는지, ID 가 겹치지 않는지, 다른 표를 가리키는 값이 모두 있는지, 절로 팀 `Chunk` 를 만들면 ID 검사를 통과하는지 본다. 2026-11-25 `dev`(`bcb0943`) 기준으로 통과한다. 모델이 바뀌면 다시 돌려 본다.

## works.jsonl — 작품 159행

architecture.md §3 의 작품 표다. IMDb 목록 132편에서 출발한다.

| 행의 종류 | 수 | 예 |
|---|---|---|
| 작품 한 편 | 129 | `tt0371746` 아이언맨 |
| 시즌 (타임라인이 시즌별로 적은 시리즈) | 21 | `tt9140554-S1` 로키 시즌 1, `parent_work_id = tt9140554` |
| 시리즈 전체 (시즌 행이 있는 시리즈) | 9 | `tt9140554` 로키 |

- **`timeline_order`** — Marvel.com 타임라인(2026-10-22)의 순번. 81행에 있다 (아이언맨 5 · 엔드게임 44). 시즌별로 적힌 시리즈는 시즌 행에만 있고 시리즈 전체 행은 비어 있다.
- **`is_canon`** — Marvel.com 타임라인이나 Disney+ 개봉 순서 문서에 올라 있으면 `true` (95행), 아니면 `false` (64행). 어느 문서가 근거인지는 `extra/works_meta.jsonl` 의 `canon_source` · `canon_asof`.
- **`phase`** — Disney+ 개봉 순서 문서의 값, 없으면 Wikipedia 분류의 값. 70행에 있다. 여러 Phase 에 걸친 시리즈 전체 행은 비웠다.
- **`format` · `year`** — IMDb 의 형식과 연도 그대로.
- **`source_ids`** — `imdb_tconst` · `team_content_id`(`cnt_…`) · `wikipedia_document_id`(`doc_…`).
- 시리즈 전체 9행은 architecture.md 의 150행에는 없던 것이다 (129 + 21 = 150). 시즌 행의 `parent_work_id` 와 시리즈 문서의 `work_id` 가 가리킬 행이 있어야 해서 남겼다. `extra/works_meta.jsonl` 의 `row_kind = series_parent` 로 가려낼 수 있다.
- 넷플릭스 시기 시리즈 6편(데어데블 · 제시카 존스 · 루크 케이지 · 아이언 피스트 · 퍼니셔 · 디펜더스)은 IMDb 목록에 없어 `title_ko` 가 영어 제목이고 `year` 가 비어 있다.

**본문 색인 규칙** (architecture.md §3): `is_canon` 이고 `status = released` 이고 `collect_wave = "1"` 인 작품 — 시리즈 전체 행을 빼면 **71행**으로 문서의 수와 같다. `status` 와 `collect_wave` 는 `extra/works_meta.jsonl` 에 있다.

## documents.jsonl — 문서 850행

| `doc_id` | 수 | 언제 |
|---|---|---|
| `{work_id}-wikipedia` | 103 | 문서 전체가 그 작품(또는 시즌)을 다룬다. `work_id` 가 채워져 있다 |
| `wp-{pageid}` | 747 | 그 밖의 문서 — 인물 · 세계관 · 에피소드 · 작품 표에 없는 작품. `work_id` 는 비어 있다 |

- `source_ids.team_document_id` 가 원래 ID(`doc_…`)다. **평가셋 근거가 `doc_…` 로 적혀 있으므로 이 값으로 새 `doc_id` 를 찾는다.** 근거 55건의 문서와 절이 이 묶음에 모두 있다.
- `source_url` 은 판 번호가 박힌 주소, 읽기 좋은 주소는 `source_ids.article_url`.
- `doc_type`: `film` 105 · `series` 101 · `season` 35 · `episode` 194 · `short` 10 · `special` 4 · `character` 76 · `character_list` 9 · `universe` 29 · `franchise` 6 · `other` 21 · `non_screen` 260.
- **850개가 모두 MCU 문서는 아니다.** 수집 범위 판정은 `extra/documents_meta.jsonl` 의 `scope_status` 에 있다 (`mcu` 404). 색인에 넣을 문서는 이 값과 작품의 `is_canon` 으로 고른다.
- 에피소드 · 시즌 문서처럼 작품 표에 행이 없는 문서는 `work_id` 가 비어 있지만, 그중 84개는 속한 시리즈를 `extra/documents_meta.jsonl` 의 `related_work_id` 로 알 수 있다.

## sections.jsonl — 본문 12,476행

문서의 절 하나가 한 행이다. 글자가 있는 절과 에피소드 요약이 든 절만 넣었다.

| 칸 | 내용 |
|---|---|
| `doc_id` · `section_index` | 문서와 절 번호. 절 번호는 handoff 와 같다 |
| `team_section` | 이 절이 팀 `Section` 의 어느 값인가 — `synopsis`(작품 문서의 첫머리) 449 · `plot`(Plot · Premise 절) 394 · `intro`(인물 문서의 첫머리) 76. 해당하는 값이 없으면 `null` |
| `section_kind` · `section_path` · `level` | 수집 쪽의 절 분류와 제목 경로 |
| `text` | 본문. 각주 · 틀을 걷어낸 글자 (글자를 되살린 판) |
| `start_char` · `end_char` | 문서 전체 글자에서의 위치 |
| `links` · `episode_entries` | 절 안의 링크, 에피소드 표의 줄(요약 포함) |

`team_section` 이 `null` 인 절(인물의 서사 · 세계관 문서의 본문 등 11,557행)은 **지금 팀 `Section` 에 넣을 값이 없다.** 평가셋 근거 55건 중 11건(20문항 중 9문항)이 이런 절에 있으므로(예: Q001 타임 스톤 — 세계관 문서), 이 절들을 색인하려면 `Section` 에 값을 더하는 합의가 먼저 필요하다.

## 청크를 만들 때

```python
Chunk(
    chunk_id=f"{doc_id}-{section}-p{position:02d}",  # 예: tt4154796-wikipedia-plot-p07
    doc_id=doc_id, work_id=document.work_id, source_id=document.source_id,
    section=section, position=position, total=total, text=문단,
    source_url=document.source_url, revid=document.revid, ...
)
```

`doc_id` 를 이 묶음의 값 그대로 쓰면 팀원끼리 청크 ID 의 앞부분이 같아진다. 문단을 어떻게 자르고 합칠지는 각자 정한다.

## extra/ — 팀 모델에 칸이 없는 값

| 파일 | 내용 |
|---|---|
| `works_meta.jsonl` | 작품마다: `row_kind` · `doc_id` · `canon_source` · `canon_asof` · `saga` · `release_order`(Disney 개봉 순번) · `status` · `collect_wave` · `scope_status` · `wikipedia_target` · `fandom_page` · `wikipedia_page` · `release_date_raw` · `tmdb_id` · `is_animation` |
| `documents_meta.jsonl` | 문서마다: `title` · `related_work_id` · `scope_status` · `categories` · `infobox` · `text_sha256` · `text_length` · `text_restored` · `revision_timestamp` · `review_status` |
| `characters.jsonl` | handoff 의 인물 633행 (글자를 되살린 판) + `doc_id`. 팀 모델에 인물 표가 아직 없다 |
| `marvel_timeline_2026-10-22.csv` · `disney_release_2026-11-04.csv` · `title_map.csv` | 순서와 정식 여부의 근거인 공식 문서 추출본과 제목 ↔ IMDb ID 대응표 |

## 쓸 때 주의

- **본문 글자를 되살렸다.** 정제 때 틀째 지워졌던 항목 이름 846 · 소유격 1,549 · 띄어쓰기 640 · 줄표 47 · 줄임말 235건을 같은 판의 원문에서 되살렸다 (`is an orangutan` → `Peotor is an orangutan`). 새로 쓰거나 LLM 으로 고친 글자는 없다. 문서 439개가 해당하고 `documents_meta` 의 `text_restored` 로 알 수 있다.
- **평가셋의 발췌문은 되살리기 전 글자다.** 발췌문을 본문에서 그대로 찾으면 아포스트로피가 들어간 곳에서 어긋난다. 영문자와 숫자만 남겨 비교하면 맞는다.
- **IMDb 대응 33편은 문서가 따로 없다.** 시리즈 문서의 에피소드 줄에만 있는 것 22편(예: 〈마블 스튜디오 어셈블드〉 각 화), 다른 문서로 넘겨지는 것 8편, 문서의 한 절인 것 3편이다. `works_meta` 의 `wikipedia_target` 이 `episode_entry` · `redirect_target` · `article_section` 이고 `doc_id` 가 비어 있다. 대응된 문서는 `Work.source_ids.wikipedia_document_id` 에 적혀 있다.
- 정식 작품인데 수집된 Wikipedia 문서가 없는 것이 9행 있다 — 원샷 3편 · 〈팀 토르 2〉 · 〈팀 대릴〉 · 〈아이 엠 그루트〉 시즌 1 · 2 · 〈디펜더스〉 · 〈어벤져스: 시크릿 워즈〉.
- 단위 변환 틀(`{{convert}}`)은 되살리지 않았다. 정보 상자의 값도 손대지 않았다.
- 전 레코드가 검토 전(`pending_review`)이다. 사람이 확인한 레코드는 없다.
- 본문은 영어 Wikipedia, CC BY-SA 4.0. 문서마다 주소와 판 번호가 있다. **IMDb 목록에서 온 값이 들어 있으므로 공개 저장소에 올리지 않는다.**
