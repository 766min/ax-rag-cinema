# 배포 — 서버 한 대 (DB + API·화면 + HTTPS)

```
브라우저 ──► Caddy(80/443, HTTPS) ──► api(FastAPI 8000: /api/* + Next 정적 화면) ──► db(Postgres + pgvector)
```

## 1. 서버 준비

- Docker 가 깔린 리눅스 VM 하나 (메모리 2GB 이상. 재정렬 C+ 를 쓰면 4GB 이상)
- 도메인을 쓰면 DNS A 레코드를 서버 IP 로. 방화벽은 80 · 443 만 연다
- 저장소를 받고, **데이터셋을 `data/cineverse-dataset-schema/` 에 직접 올린다** (저장소 · 이미지에는 없다)

## 2. `.env`

`.env.example` 을 복사해 채운다. 배포에서 꼭 볼 것:

| 키 | 값 |
|---|---|
| `OPENAI_API_KEY` 외 필수 키 | 로컬과 같게 |
| `POSTGRES_PASSWORD` | 새 비밀번호 (URL 에 들어가므로 영문 · 숫자만) |
| `SITE_ADDRESS` | 도메인 (없으면 `localhost`) |
| `ASK_MODE` | `B+` (기본). `C+` 는 Dockerfile 에 rerank 그룹을 더해야 한다 |
| `BOARD_SECRET` · `BOARD_ADMIN_TOKEN` | 긴 임의 문자열 |
| `LOG_FILE` | 비운다 (컨테이너는 파일을 쓰지 않는다) |

`DATABASE_URL` 은 compose 가 `db` 주소로 바꿔 넣는다.

## 3. 띄우기

```bash
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml logs -f api
```

## 4. 색인 옮기기 (한 번)

서버 DB 는 비어 있다. 로컬에서 만든 색인을 덤프해서 옮기면 임베딩 비용이 다시 들지 않는다.

```bash
# 로컬
docker exec ax-rag-db pg_dump -U postgres -d rag_db -Fc -t 'langchain_pg_*' > rag_index.dump
# 서버 (파일을 올린 뒤)
docker compose -f docker-compose.prod.yml exec -T db pg_restore -U postgres -d rag_db --clean --if-exists < rag_index.dump
docker compose -f docker-compose.prod.yml restart api
```

`.env` 의 `MOVIE_COLLECTION` · `EMBEDDING_MODEL` 이 덤프한 색인과 같아야 한다 (다르면 챗봇이 503).

## 5. 확인

- `https://도메인/api/health` → `{"status": "ok"}`
- 화면에서 로어 AI 에 질문 하나 → 답 · 출처가 나오는지
- 게시판에 글 하나 쓰고 지우기 (게시판 표는 첫 요청 때 `board` 스키마에 자동으로 생긴다)

## 6. 비용 · 남용 막기

- **OpenAI 대시보드에서 월 예산 한도를 꼭 건다** (코드의 횟수 제한은 보조 장치)
- 질문: IP 당 1분 6번 · 서버 전체 하루 500번 (`ASK_PER_MINUTE` · `ASK_PER_DAY`), 질문 500자 제한, 같은 질문은 캐시
- 게시판: IP 당 1분 5건, 글은 텍스트로만 보여 준다. 문제 글은 관리자 토큰으로 숨긴다

```bash
curl -X POST https://도메인/api/posts/12/hide -H "X-Admin-Token: $BOARD_ADMIN_TOKEN"
```

## 한계

- 횟수 제한 · 답 캐시는 메모리에 있어서 api 를 다시 켜면 초기화된다. 워커는 1개로 둔다
- 챗봇은 질문 하나씩 답한다 (앞 대화를 기억하지 않는다)
