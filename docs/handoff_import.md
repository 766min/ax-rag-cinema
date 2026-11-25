# Cineverse handoff 적재

## 원천 배치

전달받은 ZIP은 다음 경로에 두며 Git에 커밋하지 않는다.

```text
data/local/cineverse_handoff/cineverse-integrated-dataset-handoff.zip
```

`data/`는 저장소의 `.gitignore`에 포함된다. ZIP은 최종 DB가 아니라 import 원천이다.

## ID 변환 범위

- `source_ids.imdb_tconst`가 있는 작품만 정식 `work_id=tt...`로 적재한다.
- 기존 `cnt_...`, `doc_...`, source chunk ID는 `source_ids`에 보존한다.
- 작품 Wikipedia 문서는 `{work_id}-wikipedia`, 인물·세계관 문서는 `wp-{pageid}`를 쓴다.
- 청크는 `{doc_id}-{section}-pNN`으로 바꾸고 `chunk_id + header + text`의 SHA-256을 `content_hash`로 쓴다.
- IMDb 매핑이 없는 작품과 청크는 임의 ID를 만들지 않고 dry-run 보고서에 남긴다.

## dry-run

`.env`나 DB 없이 실행할 수 있다.

```powershell
uv run python scripts/import_handoff.py
```

결과는 콘솔과 `data/local/cineverse_handoff/import-report.json`에 기록된다. 현재 handoff에서 기대하는 핵심 수치는 다음과 같다.

- 원천 작품 378 / 정식 작품 112
- 원천 청크 861 / 정식 원문 청크 167
- 정식 LLM 정제 청크 167
- IMDb 미매핑으로 제외되는 원천 청크 694

## 실제 적재

`.env`에 개인 DB와 모델 설정을 채우고 PostgreSQL을 시작한다.

```powershell
docker compose up -d db
uv run python scripts/import_handoff.py --apply
```

일반 표 `works`, `documents`, `aliases`, `characters`, `ingest_runs`는 importer가 없으면 만든다. 원문 청크는 `MOVIE_COLLECTION`, `cleaned_text` 기반 정제 청크는 `ENRICHED_COLLECTION`에 들어간다. 기존 청크의 `content_hash`가 같으면 임베딩을 다시 호출하지 않는다.

- **저장 ID**: PGVector 의 행 ID(`langchain_pg_embedding.id`)는 컬렉션과 상관없이 하나의 공간이라, 원문과 정제본이 같은 `chunk_id` 로 저장되면 서로 덮어쓴다. 그래서 행 ID는 `{컬렉션}:{chunk_id}` 로 저장하고, 인용 · 평가 정답 키는 metadata 의 `chunk_id` 를 쓴다. 검색기는 결과의 `metadata["chunk_id"]` 를 넘겨야 한다. 이전 형식(접두어 없는 ID)으로 들어간 행은 지우지 않고 `stale_remaining` 으로 알려 주므로, 0 이 아니면 테스트 DB 를 비우고 다시 적재한다.
- **적재 상태**: `ingest_runs` 는 벡터 적재 전에 `pending` 으로 한 줄을 만들고, 원문 · 정제본 벡터가 모두 들어간 뒤 `completed`, 도중에 실패하면 `failed` 로 바꾼다. 평가 기록에는 `status = 'completed'` 인 실행의 `base_index_version` 만 쓴다.
- **제외 사유** (2026-11-25 ZIP 기준 dry-run): `chunk_work_without_imdb_id` 694 (IMDb ID 가 없는 TV 시즌 · 에피소드 등의 plot 청크), `wp_document_without_chunks` 114 (인물 · 세계관 `wp-` 문서는 만들어지지만 ZIP 에 청크가 없다 → 공통 청킹 단계에서 만들어야 한다). `wp-` 문서에 청크가 오면 `work_id=None` 으로 그대로 적재한다.

`eval/test.json`은 이 과정에서 읽거나 수정하지 않는다. 본 평가를 시작한 뒤에는 색인을 다시 적재하지 않고 보고서의 `base_index_version`을 평가 기록에 사용한다.
