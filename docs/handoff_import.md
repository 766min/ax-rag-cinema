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

`eval/test.json`은 이 과정에서 읽거나 수정하지 않는다. 본 평가를 시작한 뒤에는 색인을 다시 적재하지 않고 보고서의 `base_index_version`을 평가 기록에 사용한다.
