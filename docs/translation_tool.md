# 영화 질문·답변 번역 도구

## 팀 설계와 연결

- 모든 검색 구성(A/B/C)은 `normalize_query()`로 한국어 원문을 유지하고, 일치한 작품·인물의 영어 정식 이름을 덧붙인다.
- C+리라이트와 서비스의 웹 검색 경로는 `rewrite_movie_query()`의 `query_en`을 사용한다. 실패하면 `query_normalized`로 검색하고 `rewrite_failed=true`를 호출 기록에 남긴다.
- 사용자용 서비스 경로 `service.movie_qa.answer_movie_question()`은 영어 질의 → 영문 근거 검색 → 영문 답변 초안 → `translate_answer_to_korean()` → 한국어 답변 순서로 호출한다. 인용한 청크가 검색 근거에 없으면 답변을 거부한다. 번역 실패 시 영문 초안을 한국어 답변으로 잘못 표시하지 않는다.
- A/B/C/C+ 평가 코드는 서비스 경로를 사용하지 않는다. 팀의 `architecture.md` 초안에 따라 C+에서만 전체 질의 리라이트를 적용해야 C와 비교할 수 있다. 서비스 경로와 평가 경로가 이 점에서 다르다는 사실을 API·평가 기록에 명시한다.
- LangChain 에이전트에는 `tools.ALL_TOOLS`의 `translate_movie_query`, `translate_movie_answer`를 바인딩할 수 있다. 검색 파이프라인에서는 도구 래퍼 대신 위 함수를 직접 호출한다.

## 서비스 연결 계약

`src/service/movie_qa.py`는 호출 순서를 구현했지만 실제 DB 검색기와 영문 답변 생성기는 아직 없다. 두 팀 모듈이 준비되면 다음 형태의 비동기 함수를 넘긴다.

```python
async def retrieve(search_query: str) -> list[Evidence]: ...
async def generate_en(search_query: str, evidence: list[Evidence]) -> EnglishDraft: ...


result = await answer_movie_question(
    "아이언맨은 누구야?", retrieve=retrieve, generate_en=generate_en
)
```

`Evidence`에는 `chunk_id`, 영문 원문 `text`, `source_url`을 넣는다. `EnglishDraft`에는 영문 `answer_en`과 실제로 인용한 `cited_chunk_ids`를 넣는다. 영문 답변 본문에도 해당 청크 ID를 표기하면 한국어 번역 단계에서 ID를 그대로 보호한다. 결과에는 실제 검색에 사용한 `search_query`, 영문 초안, 한국어 답변, 출처, 실패 상태가 담긴다. API는 한국어 답변과 출처를 화면에 표시하고 영문 초안은 내부 기록용으로 다룬다.

## 별칭 데이터

팀이 정리한 `data/official/aliases_ko.csv`가 있으면 이를 우선 사용한다. 아직 없으면 기존 `data/local/aliases.jsonl`, 그마저 없으면 표준 경로 `data/local/cineverse_handoff/cineverse-integrated-dataset-handoff.zip`의 `handoff/aliases.jsonl`을 읽는다. `data/`는 Git에서 제외된다. 환경 변수 `CINEVERSE_ALIASES_PATH`를 지정하면 이 우선순위보다 먼저 적용하며 CSV·JSONL·ZIP 경로를 받을 수 있다. ZIP을 직접 지정해도 다른 대용량 구성원은 읽지 않으며, 읽은 별칭은 프로세스 안에서 캐시한다.

```powershell
$env:CINEVERSE_ALIASES_PATH = 'C:\Users\boomd\Downloads\cineverse-integrated-dataset-handoff.zip'
```

handoff 별칭 112개는 모두 작품명이다. 인물명 `아이언맨`, `캡틴 아메리카`(`캡틴아메리카` 포함), `토니 스타크`, `스티브 로저스`는 코드의 작은 명시적 보충 사전에 있다. 팀이 검증한 인물 별칭 파일을 마련하면 이 부분을 데이터로 옮긴다.

## 호출 예

```python
from tools.translation import load_aliases, normalize_query, rewrite_movie_query

aliases = load_aliases()
query_normalized, matched_aliases = normalize_query("아이언맨 2와 아이언맨의 차이는?", aliases)
# 아이언맨 2와 아이언맨의 차이는? Iron Man 2 Iron Man

result = await rewrite_movie_query("캡틴 아메리카가 아이언맨을 만났어?", aliases)
# query_ko, query_normalized, query_en, rewrite_failed, matched_aliases
```

LLM 호출에는 팀의 `.env`에 있는 `LLM_MODEL`, `LLM_PROVIDER`, 해당 제공자의 API 키가 필요하다. 키 없이도 별칭 기반 `normalize_query()`는 동작한다. 번역 도중 고유명사 토큰이 빠지거나 중복되면 리라이트는 실패 처리하고 사전 보강 질의로 돌아간다. 답변 번역은 URL·청크 ID를 함께 보호한다.
