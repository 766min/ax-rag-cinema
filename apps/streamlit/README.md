# CineVerse Streamlit 발표 화면

Streamlit은 FastAPI와 독립 실행하며 `src/service`와 `src/retrieve`의 공통 RAG 모듈을 직접 호출한다.

```bash
uv pip install -r apps/streamlit/requirements.txt
PYTHONPATH=src .venv/bin/streamlit run apps/streamlit/app.py
```

실행 전 `.env`의 `DATABASE_URL`과 `MOVIE_COLLECTION`은 팀 스키마와 호환되는 로컬 DB를 가리켜야 한다. `.env`와 DB 데이터는 커밋하지 않는다.

## 화면 구성

- 디자인은 CINEVERSE 브랜드 시안(로고·컬러·웹 히어로)을 따른다. 스타일은 `styles.css`, 이미지는 `assets/`에 있다.
  - `logo-main.png` 메인 로고(사이드바), `logo-nav.png` 히어로 상단 로고, `logo-icon.png` 파비콘, `hero-art.jpg` 히어로 일러스트
- 라이트 테마는 저장소 루트의 `.streamlit/config.toml`에서 지정하므로 **저장소 루트에서 실행**한다.
- 히어로 검색창 = 질문 입력, `탑승하기` = 실행(Enter도 가능). 아래 바로가기 4개는 예시 질문을 채운다.
- 검색 구성(A/B/C…)과 A/B/C 비교는 사이드바에서 고른다. 비교 시 결과 카드가 나란히 표시된다.
