# Cineverse

> 영화 속 세계를, 근거와 함께.

팀 **랙랙무비**의 프로젝트입니다. 마블 시네마틱 유니버스(MCU) 문서를 근거로 한국어 질문에 답하는 **RAG 기반 Knowledge Agent**로, 확인 가능한 출처와 함께 답하거나 근거가 부족하면 그렇게 안내합니다.

> 상태 (2026-11-27): **구현 완료 · 개발용 11문항 평가까지 진행.** 색인 · 검색 · 답변 · 평가 · API · 화면이 `dev` 에 있습니다. 최종용 20문항 실행과 `report.md` 가 남았습니다.

## 한눈에

| | |
|---|---|
| 질문 → 답 | 한국어 질문 → 이름 사전 치환 + 영문 리라이트 → 벡터 · BM25 · RRF → (재정렬) → 근거로 영어 초안 → 인용 검증 → 한국어 답 + 출처 |
| 색인 | 영어 Wikipedia 134문서 → 2,493행 (본문 2,262 · 작품 카드 159 · 연결 카드 72), PostgreSQL + pgvector |
| 비교 구성 | A(벡터) · B(+BM25 · RRF) · C(+재정렬), 그리고 영문 리라이트를 더한 A+ · B+ · C+ |
| 평가셋 | 공식 31문항 — 개발 11(`eval/dev.json`) · 최종 20(`eval/test.json`, 잠금) |
| 화면 | Next.js 화면 + FastAPI 서버(챗봇 · 영화 정보 · 게시판), 발표용 Streamlit |

## 목표
- 사용자의 영화 관련 질문에 **출처(문서 · 절 · 원문 위치 · 판 번호)와 함께** 답한다.
- 문서에 없는 내용은 지어내지 않고 **근거 부족**을 안내한다. 검색 · 생성 장애는 근거 부족과 구분한다.
- 검색 구성을 **같은 평가셋 · 같은 색인**으로 비교한다.
- 실행 결과는 원본 기록(`results/`)으로 남기고, 실패도 삭제하지 않는다.

## 지금까지의 결과 (개발 11문항, 답변 가능 10문항 기준)

| 실행 | 설정 (생성 수 · 후보 · 문맥) | 재정렬 장치 | Hit@5 | 답 정답 (자동 채점) | 응답 중앙값 |
|---|---|---|---|---|---|
| 리라이트 없음 | 5 · 20 · 청크 | CPU | A 2 · B 1 · C 2 | 1 · 0 · 1 | 1.9 · 2.5 · 13.8초 |
| 리라이트 기본 | 5 · 20 · 청크 | CPU | A+ 6 · B+ 5 · C+ 5 | 4 · 2 · 3 | 3.7 · 3.7 · 15.9초 |
| 〃 | 5 · 20 · 청크 | Apple M2 (mps) | A+ 6 · B+ 5 · C+ 5 | 4 · 3 · 3 | 3.0 · 3.4 · 6.7초 |
| 설정 조정 뒤 (지금 기본값) | 8 · 40 · 부모 문맥 | NVIDIA (cuda) | A+ 6 · B+ 5 · **C+ 7** | 4 · 3 · **5** | 3.4 · 4.1 · 4.8초 |

- 문서가 영어라 **한국어 질문 그대로는 정답이 후보에도 잘 오르지 않았다.** 그래서 영문 리라이트를 모든 구성의 기본으로 했다.
- 컴퓨터 성능을 타는 단계는 재정렬뿐이다 (후보 20 기준 CPU 12.1초 · mps 2.4초, cuda 는 후보 58~76개에 1.5초).
- 11문항이라 1문항이 10%p 이고, 같은 조건에서도 답 정답이 ±1문항 흔들린다. **결론이 아니라 방향**으로 읽는다. 최종 20문항은 아직 돌리지 않았다.
- 자세한 표 · 문항별 순위 · 단계별 시간: [`docs/test_comparison/`](docs/test_comparison/)

## 대상 데이터
- 대상: MCU 의 정식 · 개봉 작품 (1차 범위). 작품 문서 68 + 인물 문서 63 + 세계관 문서 3 = **134문서**를 색인한다.
- 출처: **영어 Wikipedia** — 문서 850개를 판 번호째 받아 팀 데이터 모델에 맞춘 공통 데이터셋 `cineverse-dataset-schema` (지문 `24ee6397fbca`). 정식 작품 여부와 순서는 Marvel.com · Disney+ 의 공식 순서 문서에서 값만 가져왔다.
- 데이터셋은 **저장소에 없다** (IMDb 에서 온 값이 들어 있어 재배포할 수 없다). 팀에서 받은 폴더를 `data/cineverse-dataset-schema/` 에 풀어 쓴다.

## 폴더 구조
| 폴더 | 내용 |
|---|---|
| `docs/` | 문제 정의 · 설계 · 취합 기록 · 실행 비교 · 배포 (아래 "문서") |
| `data/` | 공통 데이터셋과 만든 색인 파일을 두는 자리 (git 제외, `.gitkeep` 만 추적) |
| `eval/` | 평가셋 (`dev.json` 개발용 11 · `test.json` 최종용 20) |
| `results/` | 실행별 원본 기록 (**수정·삭제 금지**). `-try` 실행은 git 에서 빠진다 |
| `src/build/` | 데이터셋 → 청크 (`chunks.py`), 작품 카드 · 연결 카드 (`cards.py`) |
| `src/store/` | 증분 적재 · 안전 검사 (`db.py`) |
| `src/retrieve/` | 벡터 · BM25 · RRF (`search.py`), 구성별 실행 (`pipeline.py`), 재정렬 (`refine.py`), 부모 문맥 (`parent.py`) |
| `src/service/` | 서비스 경로 (`movie_qa.py`), 질문 준비 (`query_prep.py`), 답변 생성 · 인용 (`answer.py`), 답변 캐시, 실행 조립 (`runtime.py`) |
| `src/evals/` | 정답 키 (`items.py`), 일괄 실행 (`run.py`), 지표 (`score.py`), 채점기 (`judge.py`), 기록기 (`recorder.py`) |
| `src/tools/` · `src/websearch.py` · `src/webstore.py` | 이름 사전 · 리라이트 · 번역 / 웹 검색 보강 (선택) |
| `src/api/` | FastAPI 서버 — 챗봇 · 영화 정보 · 게시판 |
| `src/shared/core/` | 팀 공통 부품 — 설정 · 데이터 모델 · 로깅 · 예외 · 시간 측정 |
| `scripts/` | 색인 · 질문 한 건 · 일괄 평가 · 채점 등 실행 명령 |
| `web/` · `apps/streamlit/` | Next.js 화면 / 발표용 Streamlit 화면 |
| `tests/` | DB · API 없이 도는 테스트 (2026-11-27 기준 336개) |

## 시작하기

모든 명령은 저장소 루트에서 실행한다.

### 1. 받기와 `.env`
```bash
git clone https://github.com/766min/ax-rag-cinema.git
cd ax-rag-cinema
cp .env.example .env   # .env에 본인 키를 입력 (절대 커밋하지 않음)
```
필수 키: `OPENAI_API_KEY` · `EMBEDDING_MODEL` · `LLM_PROVIDER` · `LLM_MODEL` · `REASONING_EFFORT` · `LLM_TIMEOUT` · `DATABASE_URL` · `MOVIE_COLLECTION`. 로컬 DB 주소는 `127.0.0.1` 로 적는다 (`localhost` 는 Windows 에서 연결이 수십 초 멈춘다).

### 2. 의존성 설치
아래 셋 중 **하나만** 고른다. 로컬 재정렬기(C · C+ 구성)가 필요 없으면 기본 설치로 충분하다.

| 환경 | 명령 | torch |
|---|---|---|
| 기본 (CI 와 같음) | `uv sync --locked` | 설치 안 함 |
| 재정렬 · CPU / macOS | `uv sync --locked --group rerank` | Linux · Windows: `+cpu` (PyTorch CPU 저장소), macOS: PyPI (Apple GPU `mps` 사용) |
| 재정렬 · NVIDIA GPU | `uv sync --locked --group rerank-cu128` | Linux · Windows: `+cu128` (CUDA 12.8, RTX 50 계열 포함) |

- 기본 설치에는 torch · sentence-transformers · NVIDIA 런타임이 들어가지 않는다. 재정렬 그룹 없이 C · C+ 를 돌리면 설치 방법을 알려 주는 오류가 난다.
- `rerank` 와 `rerank-cu128` 은 함께 고를 수 없다 (`tool.uv.conflicts`). macOS 에서 `rerank-cu128` 을 고르면 `rerank` 와 같은 PyPI torch 가 깔린다.
- Intel Mac(x86_64)은 PyTorch 가 torch 2.11 휠을 내지 않아 재정렬 그룹을 설치할 수 없다. 기본 설치는 된다.
- **환경 바꾸기:** 다른 명령으로 `uv sync` 를 다시 돌리면 된다. `uv sync` 는 고른 그룹에 없는 패키지를 지운다. 예를 들어 `rerank` → `rerank-cu128` 은 torch 만 바꾸고, 재정렬 그룹 → 기본은 torch · sentence-transformers 등 13개를 지운다.
- **실행:** `uv run` 은 패키지를 지우지 않지만, 재정렬 그룹이 필요한 명령에는 같은 `--group` 을 붙이는 것이 안전하다.
- GPU 확인: `uv run --locked --group rerank-cu128 python -c "import torch; print(torch.__version__, torch.cuda.is_available())"` 가 `2.11.0+cu128 True` 이면 된다. 재정렬 장치는 실행 기록 `reranker.device` 에 남는다 (`RERANKER_DEVICE` 로 고정 가능).
- 재정렬 모델은 임시값 `Dongjin-kr/ko-reranker` 다 (`RERANKER_MODEL` 로 바꾼다. **이용 조건은 확인 전**). 처음 한 번은 내려받아야 하고, 그 뒤에는 `HF_HUB_OFFLINE=1` 로 접속 없이 돌릴 수 있다.

### 3. DB 와 데이터셋
```bash
docker compose up -d      # pgvector (컨테이너 ax-rag-db, DB rag_db, 127.0.0.1:5432)
# 팀에서 받은 데이터셋을 data/cineverse-dataset-schema/ 에 푼 뒤
PYTHONPATH=src uv run --locked python data/cineverse-dataset-schema/validate.py   # "팀 모델과 맞습니다"
```
- 데이터셋이 다른 곳에 있으면 `CINEVERSE_DATASET_DIR` 로 가리킨다.
- 5432 를 다른 프로젝트가 쓰고 있지 않은지 먼저 확인한다.

### 4. 환경 확인
```bash
uv run --locked ruff check . && \
uv run --locked ruff format --check . && \
uv run --locked mypy src && \
uv run --locked pytest
```
- 테스트는 DB · API 없이 돈다. 1건이 건너뛰어지면 데이터셋이 제자리에 없는 것이다.
- 명령은 `&&` 로 잇는다. `;` 로 이으면 앞 검사가 실패해도 다음이 돌아 실패를 놓친다.

### 5. 색인 만들기
```bash
export PYTHONPATH=src
uv run --locked python scripts/chunk_stats.py                           # API 없음: 레시피별 청크 수 · 길이 · 정답 발췌 보존
uv run --locked python scripts/build_index.py --recipe s800 --no-db     # 청크만 (data/indexes/s800/)
uv run --locked python scripts/build_index.py --recipe s800 --dry-run   # 적재 계획 (DB 읽기만)
uv run --locked python scripts/build_index.py --recipe s800             # 적재 (OpenAI 임베딩: 새 청크 수만큼)
```
- 평가 색인은 `s800`(문장 경계 최대 800자 · 겹침 없음 · 머리말)이다. 적재하면 2,493행 · `index_version 13c955090983a2f9` 가 나와야 다른 팀원의 결과와 견줄 수 있다.
- 같은 적재를 다시 돌리면 전부 `unchanged_skipped` 다. 기존 행을 고치거나 지우는 적재는 `--allow-changes` 가 있어야 한다.
- **레시피가 다른 색인은 DB 를 새로 만들어 넣는다.** 벡터 표의 행 ID 가 컬렉션과 무관한 전역 키라, 같은 DB 에 넣으면 서로 덮어쓴다 (넣으려 하면 멈춘다).
  ```bash
  docker exec ax-rag-db psql -U postgres -c "CREATE DATABASE rag_s800_o1;"
  docker exec ax-rag-db psql -U postgres -d rag_s800_o1 -c "CREATE EXTENSION IF NOT EXISTS vector;"
  # 실행할 때만 DATABASE_URL 의 DB 이름을 바꿔 넘긴다 (.env 는 그대로)
  ```
- 서버로 옮길 때는 색인을 덤프해 옮기면 임베딩 비용이 다시 들지 않는다 ([`docs/deploy.md`](docs/deploy.md) 4절).

### 6. 질문 한 건
```bash
PYTHONPATH=src uv run --locked python scripts/ask.py "아이언맨은 어떻게 탈출해?" --mode A
HF_HUB_OFFLINE=1 PYTHONPATH=src uv run --locked --group rerank \
    python scripts/ask.py "타임 스톤이 뭐야?" --mode C+
```
화면에 원 질문 · 치환 · 실제 검색어 · 상태 · 한국어 답변과 **검색 후보 → 모델이 읽은 범위(원문 위치) → 인용**, 인용마다 문서 제목 · 절 · 원문 위치 · 판 번호 · 수집 시점 · 주소 · 발췌가 찍힌다. `--json` 은 결과 전체다.

| 구성 | 흐름 | 기록 값 |
|---|---|---|
| A | 벡터 검색 | `vector` |
| B | + BM25 + RRF 결합 | `hybrid` |
| C | + Cross-Encoder 재정렬 (재정렬 그룹 필요) | `hybrid_rerank` |
| A+ · B+ · C+ | 영문 리라이트 → A · B · C | `vector_rewrite` · `hybrid_rewrite` · `hybrid_rerank_rewrite` |

- **영문 리라이트는 모든 구성에서 기본으로 켠다.** `--mode C` 는 C+ 로 돌고 기록에는 실제로 돈 구성이 남는다. 리라이트 없는 A · B · C 를 보려면 `--no-rewrite`.
- 기본값: 검색기마다 후보 40개(`--pool`) · 합친 목록은 자르지 않음 · 생성 모델에 상위 8개(`--generate-k`) · 부모 문맥(`--context parent`, 부모당 최대 3,000자) · RRF 상수 60. 예전 설정은 `--pool 20 --generate-k 5 --context chunk`.
- `--web` 은 근거 없음일 때만 웹 검색 보강을 부른다 (`BRAVE_API_KEY` 필요, 평가에서는 쓰지 않는다).
- OpenAI 호출: 리라이트 1(`--query-en` · `--no-rewrite` 면 0) + 질문 임베딩 1 + 생성 1 + (답했으면) 번역 1.

### 7. 일괄 평가와 채점
```bash
export PYTHONPATH=src
# 비교마다 같은 영문 검색어 파일을 쓴다 (리라이트가 실행마다 달라지면 결과가 ±1문항 흔들린다)
uv run --locked python scripts/make_queries.py --questions eval/dev.json --out tmp/queries_dev.jsonl
# 구성별 실행 (C 계열은 --group rerank 또는 rerank-cu128)
HF_HUB_OFFLINE=1 uv run --locked --group rerank python scripts/run_eval.py \
    --questions eval/dev.json --modes A,B,C --queries tmp/queries_dev.jsonl --name dev --try
# 채점기 검사 먼저, 그다음 채점
uv run --locked python scripts/judge_eval.py --questions eval/dev.json --calibrate
uv run --locked python scripts/judge_eval.py --questions eval/dev.json --runs <run_id>,<run_id>
```
- 결과: `results/{날짜}-{이름}-{구성}[-try{n}].jsonl` (문항마다 팀 `RunRecord`). `--try` 실행은 git 에서 빠진다.
- 정답은 평가셋에 **원문 발췌**로 적혀 있고, 실행할 때 지금 색인의 청크 ID 로 이어진다. 정답 키의 색인 버전이 지금 색인과 다르면 시작하지 않는다.
- 요약에는 답변 가능 / 근거 없음 문항의 분모를 따로, Hit@k · 후보 안 적중 · 인용 · 모델 입력에 정답 문장 · 실패 단계 · 단계별 시간이 나온다.
- 평가에서는 웹 검색 보강과 답변 캐시를 쓰지 않는다.
- 리라이트 없는 A · B · C 는 `--no-rewrite`. **`eval/test.json` 은 최종 결과표용이라 설정을 고칠 때 쓰지 않는다.**

### 8. API 서버와 화면
```bash
# API (챗봇 기본 구성 C+ 는 재정렬 그룹이 필요하다)
uv run --locked --group rerank uvicorn api.main:app --app-dir src --port 8000
# 화면 (Node.js 20.9 이상)
cd web && npm install && npm run dev      # http://localhost:3000 → API 는 8000
```
- `POST /api/ask` (챗봇) · `GET /api/works` (영화 정보) · `/api/posts` (게시판) · `GET /api/health`.
- 챗봇 구성은 `ASK_MODE`(기본 `C+`), 후보 수는 `ASK_POOL`(기본 40), 웹 보강은 `ASK_WEB`(기본 꺼짐). 재정렬 모델이 없거나 CPU 가 느리면 `ASK_MODE=B+`.
- `FAKE_ASK=true` 면 DB · OpenAI 없이 가짜 답을 돌려준다 (화면만 볼 때).
- 발표용 Streamlit: [`apps/streamlit/README.md`](apps/streamlit/README.md). 배포: [`docs/deploy.md`](docs/deploy.md).

## 문서
| 문서 | 내용 |
|---|---|
| [`docs/problem.md`](docs/problem.md) | 무엇을 왜 만드는가 — 사용자 · 범위 · 성공 기준 · 한계. §14 에 처음 계획에서 바뀐 것 |
| [`docs/architecture.md`](docs/architecture.md) | 어떻게 만들었는가 — 데이터 구조 · 색인 · 질의 · 웹 검색 · 답변 · 평가 · API. §13 에 초안에서 바뀐 것과 이유 |
| [`docs/shared-insignts.md`](docs/shared-insignts.md) | 다섯 사람의 1차 실험에서 무엇을 가져왔고 무엇을 버렸는가, 어떤 PR 로 올렸는가, 실패 사례 |
| [`docs/test_comparison/`](docs/test_comparison/) | 세 컴퓨터(CPU · CUDA · Apple M2)의 개발 11문항 실행 비교 |
| [`eval/README.md`](eval/README.md) | 평가셋을 만든 방법 · 분포 · 쓰면 안 되는 것 |
| [`docs/deploy.md`](docs/deploy.md) · [`web/README.md`](web/README.md) · [`apps/streamlit/README.md`](apps/streamlit/README.md) | 배포 · 화면 |
| [`docs/translation_tool.md`](docs/translation_tool.md) | 이름 사전 치환 · 영문 리라이트 · 답변 번역 — 이름을 지키는 방법과 부르는 곳 |
| `report.md` | 최종 실험 보고서 — 최종 20문항 실행 뒤에 쓴다 |

## 팀
| 이름 | GitHub | dev 에 올린 것 |
|---|---|---|
| 민지 | [@766min](https://github.com/766min) | 공통 부품(설정 · 예외 · 벡터 저장소) · 데이터 모델 · CI · 청킹과 부모 문맥 · 청킹 통계 · FastAPI 서버와 Next.js 화면 · 배포 설정 |
| 서인 | [@bsis09872-ux](https://github.com/bsis09872-ux) | 로거와 시간 측정 · 평가 실행 기록기 · 폴더 구조 · 구성별 검색 파이프라인 · 서비스 경로 · 실행 조립 · 통합 테스트 · 재정렬 설치 그룹 · GPU 실행 비교 |
| 명석 | [@aiagentohms](https://github.com/aiagentohms) | 평가셋 31문항 · 검색 부품(벡터 · BM25 · RRF) · 평가 문항과 지표 · 일괄 실행 · 리라이트 기본 켜기 · 검색 설정 조정 · 번역 도구 · 웹 검색 |
| 학용 | [@gkrdyd97-sudo](https://github.com/gkrdyd97-sudo) | 재정렬 · LLM 채점기 · 근거 기반 답변 생성과 답변 캐시 · 채점 실행 명령 · Streamlit 발표 화면 |
| 영민 | [@growthandy](https://github.com/growthandy) | 공통 데이터셋 · 작품 카드와 연결 카드 · 증분 적재 · 1차 실험 취합 · 문제 정의와 설계 문서 |

PR 별 기록은 [`docs/shared-insignts.md`](docs/shared-insignts.md) ⓪ 에 있다.

## 작업 규칙
1. `main` · `dev` 에는 직접 올리지 않고 **`dev` 에서 브랜치 → PR → 리뷰 1명 승인 → 병합**. 브랜치 이름은 `feature/{이름}/{주제}` 꼴을 쓴다 (`docs/…` · `fix/…` 도 같다).
2. 개인 실험 브랜치(`exp/*`)에서는 PR 을 열 수 없다 (CI 가 막는다). 올릴 것은 `dev` 에서 새 브랜치를 만들어 옮긴다.
3. PR 하나는 `dev` 위에서 혼자 네 검사(ruff check · ruff format --check · mypy src · pytest)를 통과해야 한다. 그래서 가져다 쓰는 파일이 먼저 `dev` 에 있어야 한다.
4. 커밋 메시지는 `타입: 한 줄 설명` (`feat`, `fix`, `docs`, `data`, `exp`, `chore`)
5. API 키와 `.env`, 데이터셋(`data/`)은 올리지 않는다 (저장소는 공개)
6. `results/` 원본과 잠근 `eval/test.json` 은 수정하지 않는다

## 데이터 출처와 라이선스
색인한 본문은 영어 Wikipedia 의 텍스트이며 CC BY-SA 4.0 조건에 따라 문서마다 출처 주소와 판 번호를 남기고, 답변의 출처에 주소와 라이선스를 표기한다. 가공본을 공개할 때는 같은 조건을 따른다. 수집 데이터의 포스터와 이미지는 사용하지 않는다. 화면에 넣은 로고와 일러스트의 출처 · 이용 조건은 아직 기록하지 않았다 (확인 필요). 재정렬 모델의 이용 조건도 확인 전이다.

## 처음 README 에서 바뀐 것

| 처음 | 지금 | 이유 |
|---|---|---|
| 상태 "기획 중" | 구현 완료, 개발 11문항 평가까지 | 진행 상황 반영 |
| 출처: Wikipedia 영화 문서 · Wikidata 메타데이터 | 영어 Wikipedia 공통 데이터셋(작품 · 인물 · 세계관) + 공식 순서 문서의 순서 값. Wikidata 는 쓰지 않는다 | 본문 출처로 계획한 팬덤 위키가 약관 확인 전이라 보류됐고, 판 번호째 받아 둔 Wikipedia 묶음을 팀 모델에 맞춰 썼다 |
| "문서별 출처 · 버전은 `data/` 의 문서 목록에 기록" · `data/` = 수집 · 정제한 문서 | 데이터셋은 저장소에 없고 `data/` 는 git 에서 빠진다 | IMDb 에서 온 값이 들어 있어 공개 저장소에 올릴 수 없다 |
| A / B / C 비교 | A · B · C 에 영문 리라이트를 더한 A+ · B+ · C+ 가 기본 | 영어 문서 · 한국어 질문이라 리라이트 없이는 정답이 후보에도 잘 오르지 않았다 (Hit@5 1~2 → 5~6 /10) |
| "색인 생성, 앱 실행 방법은 구현이 진행되면 추가" | 시작하기 3~8절 | 구현 완료 |
| `main` 기준 브랜치 → PR | `dev` 기준, `exp/*` 는 PR 불가, PR 마다 네 검사 | 다섯 사람의 실험을 파일 묶음마다 나눠 올리면서 순서가 필요했다 |
| Squash merge | 규칙에서 뺐다 | `dev` 의 PR 은 실제로 병합 커밋(Merge pull request …)으로 들어갔다. 앞으로 어느 쪽으로 할지는 팀이 정한다 |
| 이미지는 사용하지 않는다 | 수집 데이터의 이미지는 쓰지 않는다. 화면에는 로고 · 일러스트가 있다 | 화면을 만들었다. 출처 기록이 남은 일이다 |

## 안내
이 프로젝트는 수업 과제로 만든 비공식 프로젝트이며, 영화·캐릭터·제작사 등 특정 권리자나 동일한 이름의 기업·서비스와 무관합니다.
