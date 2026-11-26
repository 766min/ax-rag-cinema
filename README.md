# Cineverse

> 영화 속 세계를, 근거와 함께.

팀 **랙랙무비**의 프로젝트입니다. 영화(세계관) 문서를 근거로 질문에 답하는 **RAG 기반 Knowledge Agent**로, 확인 가능한 출처와 함께 답하거나 근거가 부족하면 그렇게 안내합니다.

> 상태: **기획 중** (도메인·데이터 범위·평가셋 합의 진행)

## 목표
- 사용자의 영화 관련 질문에 **출처(문서·청크 위치)와 함께** 답한다.
- 문서에 없는 내용은 지어내지 않고 **근거 부족**을 안내한다.
- A(Vector) / B(Hybrid: BM25 + Vector) / C(Hybrid + Re-ranking)를 **같은 평가셋**으로 비교한다.
- 실행 결과는 원본 기록(`results/`)으로 남기고, 실패도 삭제하지 않는다.

## 대상 데이터
- 첫 대상: 마블 시네마틱 유니버스(MCU) 영화 (범위는 확정되면 갱신)
- 출처: Wikipedia 영화 문서(리비전 고정), Wikidata 메타데이터
- 문서별 출처·이용 조건·버전은 `data/`의 문서 목록에 기록한다.

## 폴더 구조
| 폴더 | 내용 |
|---|---|
| `docs/` | 문제 정의, 설계, 보고서 |
| `data/` | 수집·정제한 문서와 문서 목록 |
| `eval/` | 평가셋 (`dev.json` 개발용, `test.json` 최종용) |
| `results/` | 실행별 원본 기록 (**수정·삭제 금지**) |
| `src/` | 색인, 검색, 답변, 인터페이스 코드 |
| `scripts/` | 수집·평가 실행 스크립트 |

## 시작하기
```bash
git clone https://github.com/766min/ax-rag-cinema.git
cd ax-rag-cinema
cp .env.example .env   # .env에 본인 키를 입력 (절대 커밋하지 않음)
```

### 의존성 설치
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
  ```bash
  HF_HUB_OFFLINE=1 PYTHONPATH=src uv run --locked --group rerank-cu128 python scripts/ask.py "타임 스톤이 뭐야?" --mode C+
  ```
- GPU 확인: `uv run --locked --group rerank-cu128 python -c "import torch; print(torch.__version__, torch.cuda.is_available())"` 가 `2.11.0+cu128 True` 이면 된다. 재정렬 장치는 실행 기록 `reranker.device` 에 남는다 (`RERANKER_DEVICE` 로 고정 가능).

색인 생성, 앱 실행 방법은 구현이 진행되면 이 문서에 추가한다.

## 팀
| 이름 | GitHub | 역할 |
|---|---|---|
| (작성) | | 문제·평가 설계 |
| (작성) | | 문서·색인 |
| (작성) | | 검색·재정렬 |
| (작성) | | 응답·최적화 |
| (작성) | | 실험·분석·통합 |

## 작업 규칙
1. `main`에는 직접 올리지 않고 **브랜치 → PR → 리뷰 1명 승인 → Squash merge**
2. 커밋 메시지는 `타입: 한 줄 설명` (`feat`, `fix`, `docs`, `data`, `exp`, `chore`)
3. API 키와 `.env`는 올리지 않는다 (저장소는 공개)
4. `results/` 원본과 동결된 `eval/test.json`은 수정하지 않는다

## 데이터 출처와 라이선스
수집한 Wikipedia 텍스트는 CC BY-SA 4.0 조건에 따라 출처를 표기하고, 가공본을 공개할 때는 같은 조건을 따른다. 이미지는 사용하지 않는다. 세부 내용은 문서 목록에 기록한다.

## 안내
이 프로젝트는 수업 과제로 만든 비공식 프로젝트이며, 영화·캐릭터·제작사 등 특정 권리자나 동일한 이름의 기업·서비스와 무관합니다.
