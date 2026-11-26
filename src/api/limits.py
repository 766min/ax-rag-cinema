"""요청 횟수 제한 (공개 주소에서 OpenAI 비용 · 게시판 도배를 막는다)."""

import threading
import time
from collections import defaultdict, deque

from fastapi import Request

from shared.core.exceptions import SharedException


def client_ip(request: Request) -> str:
    # 배포에서는 uvicorn --proxy-headers 가 Caddy 뒤의 실제 주소를 request.client 에 넣는다
    return request.client.host if request.client else "unknown"


class RateLimit:
    """키(IP)마다 window 초 안에 limit 번까지. 넘으면 429.

    shortcut: 메모리에만 둬서 서버를 다시 켜면 초기화되고 워커 1개 기준이다. 워커를 늘리면 Redis 로
    """

    def __init__(self, limit: int, window: float, message: str) -> None:
        self.limit, self.window, self.message = limit, window, message
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] <= now - self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                raise SharedException(self.message, status_code=429, error_code="TOO_MANY_REQUESTS")
            hits.append(now)
