"""Bounded, process-local metered-inference controls for the public demo."""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from openweight_platform.api.errors import PublicDemoBusyError


_OVERFLOW_CLIENT = "overflow"


@dataclass
class _ClientWindow:
    requests: deque[float]
    last_seen: float


class PublicDemoInferenceLease:
    """Release one non-queued inference slot exactly once."""

    def __init__(self, gate: PublicDemoInferenceGate | None) -> None:
        self._gate = gate

    def release(self) -> None:
        gate, self._gate = self._gate, None
        if gate is not None:
            gate._release()

    def __enter__(self) -> PublicDemoInferenceLease:
        return self

    def __exit__(self, *_: object) -> None:
        self.release()


class PublicDemoInferenceGate:
    """Reject excess metered requests without queues or unbounded state.

    The client key is a process-salted digest of the ASGI peer address. The
    application deliberately does not parse forwarding headers; the serving
    stack is responsible for accepting them only from a trusted proxy.
    """

    def __init__(
        self,
        *,
        enabled: bool,
        concurrency_limit: int,
        requests_per_window: int,
        window_seconds: float,
        max_tracked_clients: int,
        clock: Callable[[], float] = time.monotonic,
        salt: bytes | None = None,
    ) -> None:
        self._enabled = enabled
        self._concurrency_limit = concurrency_limit
        self._requests_per_window = requests_per_window
        self._window_seconds = window_seconds
        self._max_tracked_clients = max_tracked_clients
        self._clock = clock
        self._salt = salt or secrets.token_bytes(16)
        self._clients: dict[str, _ClientWindow] = {}
        self._active = 0
        self._lock = threading.Lock()

    def acquire(self, peer_host: str | None) -> PublicDemoInferenceLease:
        if not self._enabled:
            return PublicDemoInferenceLease(None)
        now = self._clock()
        key = self._client_key(peer_host)
        with self._lock:
            self._prune(now)
            key = self._bounded_key(key)
            window = self._clients.get(key)
            cutoff = now - self._window_seconds
            if window is None:
                window = _ClientWindow(requests=deque(), last_seen=now)
                self._clients[key] = window
            while window.requests and window.requests[0] <= cutoff:
                window.requests.popleft()
            window.last_seen = now
            if (
                len(window.requests) >= self._requests_per_window
                or self._active >= self._concurrency_limit
            ):
                raise PublicDemoBusyError
            window.requests.append(now)
            self._active += 1
        return PublicDemoInferenceLease(self)

    @property
    def tracked_client_count(self) -> int:
        with self._lock:
            return len(self._clients)

    @property
    def active_count(self) -> int:
        with self._lock:
            return self._active

    def _client_key(self, peer_host: str | None) -> str:
        value = (peer_host or "unknown").strip().lower() or "unknown"
        return hashlib.blake2b(
            value.encode("utf-8"),
            key=self._salt,
            digest_size=16,
        ).hexdigest()

    def _bounded_key(self, key: str) -> str:
        if key in self._clients:
            return key
        # Reserve one entry for all excess identities. This prevents a stream
        # of source addresses from evicting prior rate-limit history.
        distinct_capacity = self._max_tracked_clients - 1
        distinct_count = len(self._clients) - int(_OVERFLOW_CLIENT in self._clients)
        return key if distinct_count < distinct_capacity else _OVERFLOW_CLIENT

    def _prune(self, now: float) -> None:
        stale_before = now - (self._window_seconds * 2)
        stale = [
            key
            for key, window in self._clients.items()
            if window.last_seen <= stale_before
        ]
        for key in stale:
            del self._clients[key]

    def _release(self) -> None:
        with self._lock:
            if self._active <= 0:
                raise RuntimeError("public demo inference lease released twice")
            self._active -= 1


__all__ = ["PublicDemoInferenceGate", "PublicDemoInferenceLease"]
