# CINEVERSE 화면 (Next.js + Tailwind)

홈 · 탐색 · 세계관 · 영화 정보 · 커뮤니티(Cosmic Feed) · 로어 AI 챗봇 패널. 1920×1080 무대를 창 크기에 맞춰 16:9 그대로 줄여 보여 준다.

## 개발

```bash
# 1) API (저장소 루트). 챗봇 기본 구성 C+ 는 재정렬 그룹이 필요하다 (NVIDIA GPU: rerank-cu128 · 그 외: rerank)
uv run --locked --group rerank-cu128 uvicorn api.main:app --app-dir src --port 8000
#    DB · 색인 없이 화면만 볼 때는 FAKE_ASK=true (재정렬 그룹 없이도 된다)

# 2) 화면 (web/)
npm install
npm run dev        # http://localhost:3000 → API 는 http://localhost:8000 을 부른다
```

## 빌드

```bash
npm run lint
npm run build      # out/ 에 정적 파일. FastAPI 가 web/out 을 같이 보내서 서버 하나로 뜬다
```

- 정적 내보내기(`output: "export"`)라 화면 서버(Node)가 따로 없다. 데이터는 모두 브라우저에서 `/api/*` 로 받는다.
- 영화 상세 · 게시글은 `/movie/?id=…` · `/community/post/?id=…` 처럼 주소 뒤 값으로 연다 (데이터셋을 빌드 결과에 넣지 않기 위해).
- 다른 주소의 API 를 부르려면 빌드 전에 `NEXT_PUBLIC_API_BASE` 를 넣는다.
