# Codex 요청 프롬프트 — 검색 평가용 질문 세트

튜터 가이드(청킹 → 질문·모범답 → Top-k vs 정답 컨텍스트 → RAGAS → 개선)에 맞춘 Codex 요청문이다.
평가는 `scripts/eval_retrieval.py` 로 돌린다. 이 스크립트는 `gold`, `primary_gold` 칸을 읽는다.

- 프롬프트 1: 이미 만든 `tmp/questions_labeled.jsonl` 수정 (보지 않고 만든 세트)
- 프롬프트 2: 청크를 보고 만드는 새 세트 (튜터 가이드 2단계)

두 프롬프트는 서로 다른 대화에서 요청한다.

---

## 프롬프트 1 — 기존 산출물 수정

```text
tmp/questions_labeled.jsonl 을 아래 피드백대로 고쳐서 tmp/questions_blind_v2.jsonl 로 저장해 줘.
원본 파일은 그대로 두고, eval/ · scripts/ · data/ 는 수정하지 마.
청크는 tmp/chunks_dryrun.jsonl 에서 확인할 수 있어.

## 1. primary_gold 칸 추가 (가장 중요)
지금 gold 가 13~16개인 질문이 있어 (q002, q015, q023, q028 등).
검색은 Top-5 만 보기 때문에 gold 가 16개면 Recall@5 가 최대 0.31 이라서 실패처럼 보여.
- gold 는 지금처럼 "그 청크 하나로 답할 수 있는 청크 전부"로 둬 (hit@k 기준).
- primary_gold 를 새로 추가해. gold 중에서 질문에 가장 직접적으로 답하는 청크 1~3개만 넣어.
  우선순위: 질문이 가리키는 작품의 줄거리/도입부 청크 > 해당 캐릭터 문서 > 에피소드 문서
- 고른 이유를 label_note 에 덧붙여.

## 2. "문서에 없는 사실" 과 "답할 수 없는 질문" 구분
q005(Walkman)는 실제로는 맞는 사실인데 이 문서 묶음에 없을 뿐이야. unanswerable 과는 성격이 달라.
- 모든 질문에 answer_in_corpus (true/false) 칸을 추가해.
- q005 처럼 category 는 원래대로 두고 gold=[] , answer_in_corpus=false 로.

## 3. unanswerable 6개 교체
지금 질문(금고 비밀번호, 치과 의사 이름, 양치 시간 등)은 너무 엉뚱해서 검색만으로도 쉽게 걸러져.
"근거 부족" 안내를 제대로 시험하려면 그럴듯하지만 문서에 없는 질문이 필요해. 아래 유형으로 6개를 새로 써 줘.
- 잘못된 전제 2개: 실제 사건을 살짝 비튼 질문 (예: 실제와 다른 인물이 한 일처럼 묻기)
- 미개봉·미공개 작품의 줄거리나 결말 2개
- 실제 작품에 대한 그럴듯한 세부지만 줄거리·도입부에 없을 내용 2개 (예: 특정 장면의 촬영 장소)
조건:
- tmp/chunks_dryrun.jsonl 에서 키워드로 찾아보고, 정말 답이 없는지 확인해. 확인 방법을 label_note 에 적어.
- gold=[] , primary_gold=[] , answer_in_corpus=false , expected_answer=null
- 잘못된 전제 질문은 premise_error 칸에 무엇이 틀렸는지 한 줄로 적어.

## 4. 공통 칸
모든 줄에 "source": "blind" 를 추가해.

## 마지막에 알려줄 것
- primary_gold 개수 분포
- answer_in_corpus=false 인 질문 목록
- 새 unanswerable 6개 목록과 "없음"을 확인한 방법
```

---

## 프롬프트 2 — 청크를 보고 질문·모범답 만들기 (튜터 가이드 2단계)

```text
RAG 검색 평가용 질문 세트를 청크에서 만들어 줘. 결과는 tmp/questions_from_chunks.jsonl 하나로 저장해.
eval/ · scripts/ · data/ 는 수정하지 마. 표본 추출용 임시 코드는 tmp/ 안에만 둬.

## 입력
tmp/chunks_dryrun.jsonl — 한 줄에 하나.
주요 칸: chunk_id, doc_id, doc_type(film/series/season/episode/character/short/special),
section(plot/synopsis/intro), header(작품 머리말, 비어 있을 수 있음), text

## 1단계: 청크 뽑기 (random seed 42, 재현 가능하게)
60개를 아래 비율로 층화 추출해.
- film 의 plot 18 · film 의 synopsis 6
- series/season 의 plot·synopsis 10
- episode 의 plot 12
- character 의 intro 8
- short/special 아무거나 6
조건:
- 120자 미만 청크는 제외
- 한 문서(doc_id)에서 2개 넘게 뽑지 마
- 목록이나 제작 크레디트만 나열한 청크처럼 질문을 만들기 어려우면 같은 칸에서 다시 뽑고, 그 사실을 기록해

## 2단계: 청크마다 질문 1개 + 모범 정답
- 그 청크 하나만 읽고 답할 수 있는 질문이어야 해.
- 바꿔 말하기 규칙:
  - 청크 원문에서 연속 3단어 이상을 그대로 가져오지 마.
  - 드문 단어(고유명사 제외)는 다른 말로 바꿔.
    예: 원문이 "attorney" 면 질문은 "what does she do for a living"
  - 고유명사(인물·작품명)는 써도 돼.
- 사용자가 실제로 물을 법하게 써. 대략 절반은 질문에 작품명을 넣어.
  에피소드 청크는 header 가 비어 있으니 시리즈명과 장면 설명으로 어느 작품인지 알 수 있게 해.
- reference_answer: 청크 내용만 근거로 영어 1~2문장. 청크에 없는 지식을 보태지 마.
- 질문 유형을 고르게 섞어: 누가 / 무엇을 / 왜 / 어디서 / 순서·결과

## 3단계: 정답 컨텍스트를 여러 개로 확장
같은 사실이 다른 문서에 중복으로 실려 있는 경우가 많아 (영화 문서·캐릭터 문서·에피소드 문서).
- 질문마다 tmp/chunks_dryrun.jsonl 전체에서, 그 청크 하나만으로 답할 수 있는 청크를 모두 찾아 gold 에 넣어.
  gold 에는 source_chunk 가 반드시 들어가야 해.
- 키워드 검색으로 후보를 찾고, 후보 text 를 실제로 읽고 판단해.
- primary_gold: source_chunk + 그만큼 직접적인 청크 최대 2개 (총 1~3개)
- 일부 힌트만 있는 청크는 partial 에 넣어.

## 출력 형식 (한 줄에 하나)
{"qid": "c001", "source": "chunk", "source_chunk": "...",
 "question_en": "...", "question_ko": "자연스러운 한국어",
 "reference_answer": "...", "category": "film_plot|series_plot|episode_plot|character|production",
 "qtype": "who|what|why|where|sequence", "mentions_title": true/false,
 "gold": [...], "primary_gold": [...], "partial": [...], "answer_in_corpus": true,
 "label_note": "판단 근거나 애매한 점 한 줄"}

## 마지막에 알려줄 것
- 층별로 실제 뽑힌 개수와 다시 뽑은 횟수
- gold 개수 분포, primary_gold 개수 분포
- 바꿔 말하기 규칙을 지키기 어려웠던 질문 목록
```

---

## 받은 뒤 평가 실행

```powershell
$env:PYTHONPATH = "src"
uv run python scripts/eval_retrieval.py --questions tmp/questions_blind_v2.jsonl
uv run python scripts/eval_retrieval.py --questions tmp/questions_from_chunks.jsonl
uv run python scripts/eval_retrieval.py --questions tmp/questions_from_chunks.jsonl --lang ko
```

두 세트의 점수 차이(chunk − blind)가 "청크를 보고 만든 질문이 점수를 얼마나 부풀리는지"를 보여준다.
결과는 `tmp/eval_<파일명>_<lang>_k<k>.json` 에 저장되고, 임계치(`--min-recall`, 기본 0.5) 미달 목록도 함께 나온다.
