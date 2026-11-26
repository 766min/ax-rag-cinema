# test/youngmin — 청킹 · 적재 시험 (영민)

브랜치 `test/youngmin` 에서 **공통 데이터셋**을 청크 · 카드로 만들어 DB 에 적재해 보는 개인 시험 폴더다. 여기 적힌 청킹 기준은 팀 공통 전략으로 정해진 것이 아니다.

| 위치 | 내용 | git |
|---|---|---|
| `chunking/` | 청크 · 작품 카드 · 연결 카드 · 정답 키 코드와 테스트 | 올릴 수 있음 |
| `index/` | 적재(`load_index.py`) · 검색 부품(`retrieval.py`) · 구성별 시험 평가(`eval_run.py`), 테스트 | 올릴 수 있음 |
| `run.py` | 처음부터 끝까지 돌리는 실행기 (단계마다 팀 로거로 기록) | 올릴 수 있음 |
| `data/chunks_{이름}/` · `data/cards/` · `data/index/{색인}/` | 만든 청크 · 카드 · 적재 기록 · 실행 요약 · 비교표 (출력) | `.gitignore` 의 `data/` 규칙으로 빠짐 |
| (저장소 루트) `data/cineverse-dataset-schema/` | 공통 데이터셋 (입력). 팀 번역 도구와 같은 위치 | 빠짐 |
| (저장소 루트) `logs/youngmin.log` · `results/*-try*.jsonl` | 실행 로그 · 시험 평가 기록 | 빠짐 |

## 준비

```bash
docker compose up -d                       # pgvector DB (127.0.0.1:5432, 컨테이너 ax-rag-db)
PYTHONPATH=src uv run python data/cineverse-dataset-schema/validate.py   # 데이터셋이 팀 모델과 맞는지
```

- `.env` 에 필요한 값: `OPENAI_API_KEY` · `DATABASE_URL` · `LLM_PROVIDER` · `LLM_MODEL` · `EMBEDDING_MODEL` · `REASONING_EFFORT` · `LLM_TIMEOUT` · `MOVIE_COLLECTION` (`src/shared/core/config.py` 가 모두 필수로 읽는다). 이 시험은 `LLM_MODEL=gpt-6-luna` · `EMBEDDING_MODEL=text-embedding-3-small` · `MOVIE_COLLECTION=mcu_movies` 로 돌렸다.
- `DATABASE_URL` 에 둥근 따옴표(`“…”`)가 들어가면 SQLAlchemy 가 주소를 읽지 못한다. 따옴표 없이 적는다.
- 5432 포트를 다른 프로젝트의 DB 가 쓰고 있으면 `docker compose up` 이 실패한다. 그 컨테이너를 먼저 멈춘다.

## 실행

```bash
# 전부 — 테스트 → 청크 → 카드 → 연결 카드 → 정답 키 → 적재 → 시험 평가(A · B · C · C+)
HF_HUB_OFFLINE=1 uv run --with sentence-transformers==5.2.0 python test/youngmin/run.py
# 적재까지만 (LLM · 재정렬 모델 없음)
uv run python test/youngmin/run.py --until load
```

C · C+ 의 재정렬 모델(`Dongjin-kr/ko-reranker`, 로컬 Hugging Face 캐시)은 `sentence-transformers` 가 필요하다. 팀 `pyproject.toml` 에 넣지 않고 실행할 때만 `--with` 로 붙인다.

순서는 `docs/architecture.md` §4 (그림 3) — 청크 · 작품 카드 · 연결 카드를 모두 만든 뒤 적재한다. 테스트가 하나라도 실패하면 뒤 단계를 돌리지 않는다. 단계를 하나씩 돌릴 때는 각 파일 머리의 실행 줄을 따른다.

**기록** — 팀 공통 도구를 그대로 쓴다.

- `logs/youngmin.log` — `shared.core.logger` (API 키 · URL 비밀번호는 `***` 로 가려진다). 단계별 시간은 `shared.core.timing.measure_time` 이 `step:{단계}` · `embed` · `upsert` · `retrieve` · `generate` 로 남긴다. 테스트 결과 줄과 실패한 테스트 이름도 여기에 남는다.
- `results/{run_id}.jsonl` — `evals.recorder` 의 `Recorder` · `measure()`. 문항마다 `RunRecord` 한 줄 (후보와 점수 · 인용 · 답변 · 단계별 시간 · 토큰 · 호출 기록). run_id 에 `-try` 가 들어가 git 에는 올라가지 않는다.

## 무엇을 색인에 넣나

| 종류 | 고르는 기준 | 수 |
|---|---|---|
| 본문 청크 (`synopsis` · `plot` · `intro` · `biography` · `lore`) | 작품 문서는 색인 규칙(정식 · 개봉 · 1차 수집 · 문서 있음) 68개, 인물 문서는 MCU 범위 63개, 세계관 문서 3개 | 1,966 |
| 작품 카드 (`card`) | 작품 표의 모든 행 | 159 |
| 연결 카드 (`connection`) | 색인 규칙에 드는 작품 행 75개 중 연결할 것이 있는 것 | 72 |
| **합계 (컬렉션 `mcu_movies`)** | | **2,197** |

## 청킹 기준

1. 경계는 글쓴이가 그은 것을 쓴다 — 절, 문단, 에피소드. 글자 수로 자르지 않는다.
2. 문단을 쪼개지 않는다. 겹침도 없다.
3. 150자 미만 문단만 같은 절(같은 에피소드) 안의 이웃 문단과 합친다. 세계관 문서는 항목이 서로 독립이라 짧은 항목끼리만 모은다.
4. 에피소드 표의 요약은 **에피소드 하나가 한 덩어리**다. 시즌 문서는 줄거리가 이 요약에만 있다.
5. 평탄화된 표(`+ …` 로 시작하는 줄)와 30자 미만 조각은 넣지 않는다.
6. 청크마다 머리말을 붙인다 — `[Loki season 1 · 2021 season · Phase 4 · timeline #45 · Episode 1: Glorious Purpose]`. `text` 에는 넣지 않는다.
7. 청크마다 부모(절 또는 에피소드)를 `source_ids.parent_id` 로 가리키고, 부모의 글자는 `parents.jsonl` 에 둔다.
8. 청크 번호는 문서 안에서 종류별로 1부터 센다. `content_hash` 와 색인 버전은 `architecture.md` §4.2 대로 만든다.

본문 청크의 출력(`data/chunks_base/`)은 `chunks.jsonl` · `parents.jsonl`(부모 756) · `report.json`(본문만의 버전 `chunks_version`) · `gold_candidates.jsonl` · `eval_items.jsonl`(팀 `EvalItem`, 정답 키 후보 63개). 변형 색인 `chunks_min200` (1,911) · `chunks_min300` (1,803) 도 있다.

## 작품 카드 (`data/cards/cards.jsonl`)

작품 표 159행 전부에 하나씩 (`{work_id}-card`). 본문이 색인되지 않는 작품 — 정식이 아닌 작품 64 · 미개봉 5 · 문서가 없는 작품 — 을 물었을 때 검색되는 것이 이 카드다.

```
[Iron Man · 2008 film · Phase 1 · timeline #5]
Iron Man is a film released in 2008. It is part of the MCU official timeline (Marvel.com MCU Complete
Timeline, 2026-10-22). It belongs to Phase 1 of The Infinity Saga. It is number 5 of 81 in the official
timeline order. It is film number 1 in release order. Running time: 126 minutes.
```

- 표의 값을 정해진 문장 틀에 넣어 만든다. LLM 을 쓰지 않는다.
- 정보 상자에서는 값이 하나인 칸만 옮긴다 — 상영 시간 · 시즌 수 · 에피소드 수 · 채널. 감독 · 각본 같은 이름 칸은 정제값에서 여러 이름이 붙어 있어(`Anna BodenRyan Fleck`) 쓰지 않는다.
- 표시: 정식이 아니면 `non_canon`, 개봉 전이면 `upcoming`.
- **시즌의 연도** — 데이터셋의 시즌 행은 `year` 가 시리즈 시작 연도다 (로키 시즌 2 도 2021). 시즌 문서의 정보 상자에 있는 방영 시작 연도로 고친다 (`shown_year`, 머리말에도 같이 쓴다).

## 연결 카드 (`data/cards/connections.jsonl`)

"이 작품 앞뒤에 무엇이 있나" 를 한 청크에 모은다 (`{work_id}-connection`). 줄마다 출처를 괄호로 붙인다.

```
[Connection card] Iron Man 2 (2010 film · Phase 1)
Release order: previous = The Incredible Hulk; next = Thor. (Disney+ Marvel Movies In Order, 2026-11-04)
Timeline order: previous = Iron Man; next = The Incredible Hulk. (Marvel.com MCU Complete Timeline, 2026-10-22)
Earlier works sharing main characters (candidates): Iron Man (J.A.R.V.I.S., James Rhodes, Nick Fury, …);
The Incredible Hulk (Tony Stark). (Wikipedia cast sections)
```

| 줄 | 근거 | 카드 수 |
|---|---|---|
| 개봉 순서 앞뒤 (영화만) | Disney+ 개봉 순서 | 34 |
| 타임라인 앞뒤 | Marvel.com 타임라인 | 68 |
| 선행작 후보 | 먼저 나온 작품 중 출연진 절이 링크한 MCU 인물이 겹치는 것 (많이 겹칠수록 · 가까울수록 앞, 최대 5편) | 63 |

- "먼저 나온" 은 둘 다 가진 값 중 가장 자세한 것으로 잰다 — 정보 상자의 개봉일 → 개봉 순번 → 연도. 같은 해의 두 작품은 앞뒤를 가리지 않는다.
- 시즌과 그 시리즈 전체 행은 서로 선행작으로 치지 않는다. 같은 시리즈의 앞 시즌은 선행작이다.
- 선행작은 **후보**다 (`prerequisite_candidates` 표시). 사람이 확인한 정답이 아니다.
- 연결할 것이 없어 만들지 않은 행 3개: 〈팀 토르 2〉 · 〈팀 대릴〉 · 〈왓 이프...?〉 시리즈 전체 행.

## 적재 (`index/load_index.py` → `data/index/manifest.json`)

- 팀 부품을 그대로 쓴다 — `get_vector_store` (LangChain `PGVector`, 코사인) · `get_embeddings` · `get_raw_db_connection`.
- 청크 하나 = 행 하나. 행 ID = `chunk_id`, 본문(`document`) = 청크의 `text` 그대로, 나머지 칸 = 메타데이터(JSONB). **임베딩은 "머리말 + 본문"** 으로 만든다 (§3).
- **증분 적재** (§4.2): `content_hash` 가 같으면 임베딩을 건너뛴다. 결과를 `inserted` · `updated` · `unchanged_skipped` · `deleted` 로 남긴다. 적재 뒤 DB 의 행 수와 색인 버전이 파일과 같은지 확인하고, 다르면 멈춘다.
- **같은 ID 가 다른 컬렉션에 있으면 멈춘다.** `langchain_pg_embedding` 의 `id` 는 컬렉션과 상관없는 전역 키이고, 덮어쓸 때 `collection_id` 는 바뀌지 않는다. 그대로 두면 다른 컬렉션의 행을 조용히 덮어쓴다.
- 색인 버전(`index_version`)은 본문 청크 + 작품 카드 + 연결 카드 전체로 잰다. `check_gold.py` 가 정답 키에 같은 값을 적으므로, 평가 기록과 정답 키의 버전이 맞는다.

## 구성별 시험 평가 (`index/eval_run.py`)

평가셋 20문항을 팀 문서 §5 의 구성대로 돌린다. 모든 구성이 같은 것을 쓴다 — 이름 사전 치환(`normalize_query`) · 1단계 후보 20개 · 생성 모델(`LLM_MODEL`) · 답변 프롬프트(`p0-smoke`, 시험용) · 컨텍스트 상위 5청크.

| 구성 | 검색 |
|---|---|
| A | 벡터 (`text-embedding-3-small`, 코사인) |
| B | + BM25(`rank-bm25`, 머리말 + 본문) → RRF (k = 60) |
| C | + 재정렬 — RRF 후보 20개를 다국어 Cross-Encoder `Dongjin-kr/ko-reranker` 가 질문과 한 쌍으로 다시 채점 |
| C+ | + 질문 전체를 영어 검색어로 (팀 번역 도구 `rewrite_movie_query`, `LLM_MODEL`). 실패하면 원질문으로 검색하고 `rewrite_failed` 를 기록 |

```bash
PYTHONPATH=src:test/youngmin/chunking:test/youngmin/index HF_HUB_OFFLINE=1 \
  uv run --with sentence-transformers==5.2.0 python test/youngmin/index/eval_run.py --modes A,B,C,C+
# 손잡이: --index min300 (색인 변형) · --candidates 40 (후보 수) · --tag cand40 (run_id 표시)
# 비교만:  ... eval_run.py --compare <run_id>,<run_id>,...
```

구성마다 `results/{run_id}.jsonl` (팀 `RunRecord`, 후보 20개의 순위 · 재정렬 전 순위 · 점수 포함)과 `data/index/{색인}/{run_id}-summary.json` 이 생기고, 한 번에 돌린 구성들의 비교표가 `compare.md` 로 남는다.

**2026-11-25 결과** (색인 `56b946cb17db4d51`, 정답 키가 있는 19문항)

| 구성 | Hit@5 | Hit@20 (후보) | 인용 ∩ 정답 | 답변 / 근거 없음 | 근거 없음 문항 | 중앙 지연 |
|---|---|---|---|---|---|---|
| A | 3/19 | 5/19 | 2/19 | 4 / 16 | 1/1 | 1.5초 |
| B | 2/19 | 4/19 | 0/19 | 3 / 17 | 1/1 | 1.2초 |
| C | 3/19 | 4/19 | 2/19 | 10 / 10 | 1/1 | 4.1초 |
| C+ | **13/19** | **16/19** | **9/19** | 17 / 3 | 1/1 | 6.3초 |

- **한국어 질문으로 영어 문서를 찾는 것이 병목이다.** A 는 한국어 질문을 그대로 임베딩해 엉뚱한 작품이 올라오고, B 의 BM25 는 덧붙인 영어 이름에만 걸려 오히려 1문항 떨어졌다. C 의 재정렬은 후보 안에 정답이 거의 없어(4/19) 올릴 것이 없었다. C+ 는 질문을 영어로 바꾼 뒤 후보에 정답이 16/19 들어오고, 재정렬이 그중 13개를 상위 5에 올렸다.
- C+ 의 지연 6.3초 중 재정렬이 2.8초(로컬 MPS), 리라이트가 1.4초다.

**첫 실험 (에이전트 둘이 나눠 돌림, C+ 기준)** — 모든 실행의 비교표는 `data/index/compare-all.md`.

| 실험 | 바꾼 것 | Hit@5 | Hit@후보 | 중앙 지연 | 읽기 |
|---|---|---|---|---|---|
| 기준 | — | 13/19 | 16/19 | 6.3초 | |
| 반복 | 같은 설정을 다시 | 14/19 | 16/19 | 7.4초 | 실행 간 흔들림 ±1문항 (리라이트가 매번 조금 다르다) |
| 후보 40 | `--candidates 40` | 10/19 | 18/19 | 8.8초 | 후보 안의 정답은 늘었지만 재정렬이 상위 5에 덜 올렸다. 흔들림보다 큰 차이 |
| 300자 병합 | 색인 `min300` (별도 DB `rag_db_min300`, 2,034행) | 12/19 | 16/19 | 7.7초 | 흔들림 안 — 차이 없음 |

놓친 문항을 열어 보면 절반가량은 **맞는 근거를 찾았지만 정답 키에 없는** 경우다 (예: 소코비아 협정을 세계관 문서의 항목에서 찾았는데 정답 키는 영화 줄거리만 받는다). 같은 사실이 여러 문서에 있으면 모두 허용 근거로 적는다는 §8 의 규칙대로 정답 키를 넓혀야 개선이 점수에 잡힌다. 여러 작품에 걸친 질문은 인물 문서가 상위를 채워 영화별 줄거리가 밀리고, 세계관 문서 하나(Features, 청크 353개)가 여러 질문의 상위를 차지한다.

## 답변 구조와 채점 (`index/judge.py`)

**답변 구조** (2026-11-25 결정, 프롬프트 `p1-en-draft-ko`) — 검색에는 번역한 질의(C+)를, 답변 모델에는 **한국어 원 질문**과 영어 근거 5개를 넘겨 **영어 초안**을 받고, 팀 번역 도구 `translate_answer_to_korean` 이 청크 ID · 고유명사를 보호한 채 한국어로 옮긴다. 번역이 실패하면 영어 초안을 한국어 답으로 내보내지 않고 `generation_error` 로 남긴다. `RunRecord.answer` 에는 영어 초안이, 사용자가 보는 한국어 답은 `data/index/{색인}/{run_id}-answers.jsonl` 에 있다. 팀 서비스 경로(`src/service/movie_qa.py`)는 답변 모델에 번역한 질의를 넘긴다 — 원 질문을 넘기는 지금 결정과 다르다.

**채점** — 문항의 핵심 사실(`채점 핵심 사실`)마다 한국어 최종 답이 충족(met) · 부분(partial) · 누락(missing)인지, 기준 답안과 모순이 있는지를 `LLM_MODEL` 이 판정한다. 정답 충족(엄격) = 모든 사실 met + 모순 없음. 답하지 않은 문항은 LLM 없이 모두 누락으로 둔다.

```bash
PYTHONPATH=src:test/youngmin/chunking:test/youngmin/index uv run python test/youngmin/index/judge.py --calibrate
PYTHONPATH=src:test/youngmin/chunking:test/youngmin/index uv run python test/youngmin/index/judge.py --runs <run_id>,<run_id>
```

**채점기 검사** — 기준 답안 17/19 통과(사실 충족률 0.98) · 다른 문항의 기준 답안 0/19 통과(0.08). 기준 답안이 떨어진 두 문항은 평가셋 문제다: Q049 의 핵심 사실 "콜렉터에게 맡긴 시점은 다크 월드 쿠키영상" 과 Q045 의 "타노스의 학대" 가 각자의 기준 답안에 없다.

**결과** (정답 키가 있는 19문항)

| 실행 | 답변 구조 | 정답 충족 (엄격) | 사실 충족률 | 모순 | 중앙 지연 |
|---|---|---|---|---|---|
| C | 모델이 한국어로 바로 | 1/19 | 0.23 | 1 | 4.1초 |
| C+ | 모델이 한국어로 바로 | 5/19 | 0.63 | 3 | 6.3초 |
| C | 영어 초안 → 번역 | 0/19 | 0.15 | 2 | 4.6초 |
| C+ | 영어 초안 → 번역 | 5/19 | 0.63 | 3 | 7.5초 |

- 질의를 먼저 번역하면 답변도 좋아진다 — 사실 충족률 0.15 ~ 0.23 → 0.63.
- 영어 초안 → 번역 구조는 바로 한국어로 답하는 것과 품질이 같고(5/19 · 0.63), 번역 단계만큼 약 1 ~ 2초가 더 든다.
- 이제 병목은 답변이다. 정답 근거를 상위 5에 찾은 13문항 중 9문항이 사실을 빠뜨렸다. 영어 초안이 평균 2.5문장이라 여러 부분을 묻는 질문에서 모자란다.
- 채점은 기준 답안만 본다. 영화 내용으로는 맞지만 기준 답안에 없는 서술을 모순으로 잡은 경우가 있다 (Q042). 채점 모델이 답변 모델과 같다는 한계도 있다.

## 답변 개선 — 부분마다 답하기 · 부모 절 넘기기 (2026-11-25 영민 결정)

- **프롬프트 `p2-parts`** — 문장 수 제한을 없애고, 질문의 부분을 먼저 나눈 뒤 부분마다 답하게 한다. 근거가 일부만 다루면 다룬 부분만 답하고 빠진 부분을 밝힌다.
- **부모 절 넘기기** (원칙 5 "작게 찾고 크게 넘긴다", `--parent-chars 3000` 이 기본) — 상위 5 청크를 부모(절 · 에피소드) 단위로 묶어 넘긴다. 부모가 3,000자를 넘으면 검색된 청크를 가운데 두고 앞뒤 문단을 번갈아 붙인다. 한 부모에 상위 청크가 여럿이면 블록 하나로 합친다. 작품 카드 · 연결 카드는 부모가 없어 그대로. **인용은 검색된 청크의 ID** 로 한다 — 팀 규칙상 인용은 후보 안의 청크여야 하고, Hit@5 · 정답 키도 그대로 견줄 수 있다. 근거 글자는 문항당 중앙값 4,610 → 9,084자.

| 실행 (C+) | 정답 충족 (엄격) | 사실 충족률 | 모순 | 근거 없음 문항 (내용 · 상태) | 중앙 지연 | 토큰 입력 / 출력 |
|---|---|---|---|---|---|---|
| p1 · 청크 (4문장 이내) | 5/19 | 0.63 | 3 | 1/1 · 1/1 | 7.5초 | 30,040 / 5,875 |
| p2 · 청크 | 6/19 | 0.74 | 2 | 1/1 · 0/1 | 9.4초 | 32,834 / 8,723 |
| p2 · 부모 ≤ 3,000자 | 8/19 (재채점 7) | 0.73 (재채점 0.74) | 2 | 1/1 · 0/1 | 8.6초 | 52,075 / 9,411 |

- **부분마다 답하게 한 것이 효과가 있다** — 사실 충족률 0.63 → 0.74.
- **부모 절은 평균으로는 차이가 없다** (0.73 ~ 0.74). 여러 작품에 걸친 질문은 좋아지고(Q044 바키 0.33 → 0.75, Q045 · Q007) 몇은 나빠졌다(Q042 · Q056). 입력 토큰이 약 60% 늘었다. 두 실행은 리라이트가 매번 조금 달라 검색 결과도 같지 않다 — 부모 효과만 떼어 보려면 같은 리라이트로 다시 견줘야 한다.
- **채점기는 안정적이다** — 같은 답변을 두 번 채점하면 엄격 ±1문항 · 사실 충족률 ±0.01.
- **근거 없음 문항의 동작이 바뀌었다.** Q019(타임 스톤이 코믹스에서 어떻게 쓰이나)에 p1 은 "근거 없음" 으로 답했고, p2 는 "근거는 코믹스를 다루지 않는다" 고 밝힌 뒤 MCU 쪽 설명을 인용했다. 단정하지 않고 부족을 안내해 내용으로는 통과지만 상태는 `answered` 다. 채점기(j2)는 근거 없음 문항도 답을 했으면 핵심 사실로 채점하고 상태 기준을 함께 적는다.

## 실험을 나눠 돌리는 법 (에이전트 · 사람 공통)

실험 하나 = 명령 하나 = run_id 하나다. 모든 실행이 같은 `.env`(키 · 모델) · 같은 logger(`logs/youngmin.log`) · 같은 recorder(`results/`)를 쓰므로, 누가 돌려도 결과가 한 표로 모인다 (`--compare`).

- **검색 손잡이**(후보 수 등)는 같은 색인(`rag_db` 의 `mcu_movies`)을 읽기만 하므로 여러 실험을 동시에 돌려도 된다. `--tag` 로 run_id 를 나눈다.
- **청크 손잡이**(합치는 기준 등)는 색인이 달라지므로 **DB 를 따로 만든다.** 행 ID(`chunk_id`)가 컬렉션과 상관없는 전역 키라 같은 DB 에는 넣을 수 없다. `docker exec ax-rag-db psql -U postgres -c "CREATE DATABASE rag_db_min300"` → `DATABASE_URL` 의 DB 이름만 바꾼 환경변수로 `load_index.py --index min300` · `eval_run.py --index min300`. 적재 기록에 DB 이름이 남고, 평가는 연결한 DB 와 기록이 다르면 멈춘다.
- **평가셋은 하나뿐이다** (`eval/dev.json` · `test.json` 이 비어 있다). 같은 20문항으로 손잡이를 고르면 그 점수는 최종 보고용이 될 수 없다. 개발 · 최종 문항을 나누는 것이 먼저다 (`problem.md` §13 팀이 정할 것 8).

## 주의

- **재정렬 모델 `Dongjin-kr/ko-reranker` 의 이용 조건은 아직 확인하지 않았다** (모델 카드의 라이선스). 팀 모델 선정(`problem.md` §13) 전의 시험 선택이다.
- **정답 키 후보는 후보다.** 발췌가 걸친 청크를 모두 넣었고, 확정은 평가셋 담당이 한다.
- **에피소드 요약을 `plot` 으로 넣은 것은 여기서 정한 것이다.** `episode_summary` 표시로 빼고 비교할 수 있다.
- **카드 · 연결 카드의 `source_id` 는 `wikipedia_en` 으로 두었다.** 작품 표 · 공식 문서에 맞는 값이 팀 `SourceId` 에 없다.
- 연결 카드의 "인물" 에는 팀 · 조직 문서(예: Avengers)도 들어간다. 데이터셋이 인물 문서로 분류한 것이다.
- 연도를 알 수 없어 뺀 카드가 2장 있다 — 〈아이 엠 그루트〉 시즌 2 · 〈디펜더스〉. 〈팀 토르〉 는 줄거리 절이 없어 `synopsis` 만 있다.
