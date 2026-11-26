"""요청 횟수 제한 (공개 주소에서 OpenAI 비용 · 게시판 도배 · 비밀번호 맞히기를 막는다)."""

import ipaddress
import threading
import time
from collections import deque

from fastapi import Request

from shared.core.exceptions import SharedException

MAX_KEYS = 10_000  # 기억하는 주소 수 상한 (넘으면 오래된 기록부터 정리)


def client_ip(request: Request) -> str:
    # 배포에서는 uvicorn --proxy-headers 가 Caddy 뒤의 실제 주소를 request.client 에 넣는다
    host = request.client.host if request.client else "unknown"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host
    # IPv6 는 한 사람이 /64 대역 안에서 주소를 마음대로 바꿀 수 있어 대역으로 센다
    return str(ipaddress.ip_network(f"{ip}/64", strict=False)) if ip.version == 6 else host


class RateLimit:
    """키(IP)마다 window 초 안에 limit 번까지. 넘으면 429.

    shortcut: 메모리에만 둬서 서버를 다시 켜면 초기화되고 워커 1개 기준이다. 워커를 늘리면 Redis 로
    """

    def __init__(self, limit: int, window: float, message: str) -> None:
        self.limit, self.window, self.message = limit, window, message
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            if len(self._hits) > MAX_KEYS:
                self._hits = {
                    k: h for k, h in self._hits.items() if h and h[-1] > now - self.window
                }
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= now - self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                raise SharedException(self.message, status_code=429, error_code="TOO_MANY_REQUESTS")
            hits.append(now)
