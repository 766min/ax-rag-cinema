# 배포 이미지 하나: Next 정적 화면(web/out) + FastAPI. 실행은 docker-compose.prod.yml (docs/deploy.md)
# 데이터셋(data/)과 .env 는 이미지에 넣지 않는다 (IMDb 값 · 키). 서버에서 볼륨 · env_file 로 붙인다

# 1) 화면 빌드
FROM node:24-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# 2) API
FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.12.2 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_PROJECT_ENVIRONMENT=/app/.venv
# 챗봇 기본 구성 C+ 는 재정렬 모델(sentence-transformers + torch)이 필요하다.
# 기본은 CPU torch(rerank). NVIDIA GPU 서버면 --build-arg RERANK_GROUP=rerank-cu128
ARG RERANK_GROUP=rerank
RUN --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=.python-version,target=.python-version \
    uv sync --locked --no-dev --no-install-project --group "$RERANK_GROUP"
COPY src ./src
COPY --from=web /web/out ./web/out

# 재정렬 모델(Dongjin-kr/ko-reranker, 약 2.2GB)은 이미지에 넣지 않고 첫 실행 때 받아 볼륨에 둔다
ENV HF_HOME=/app/hf-cache
RUN useradd --system --no-create-home app && mkdir -p /app/hf-cache && chown app /app/hf-cache
USER app
EXPOSE 8000
# Caddy 뒤에서 돌므로 실제 접속 주소(요청 횟수 제한용)를 X-Forwarded-For 에서 읽는다. 8000 은 밖에 열지 않는다
CMD ["/app/.venv/bin/uvicorn", "api.main:app", "--app-dir", "src", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
