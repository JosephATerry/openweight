from __future__ import annotations

import pytest

from openweight_platform.api.errors import PublicDemoBusyError
from openweight_platform.api.public_demo_limits import PublicDemoInferenceGate


def gate(
    *,
    enabled: bool = True,
    concurrency: int = 2,
    requests: int = 5,
    window: float = 60,
    clients: int = 8,
    now: list[float] | None = None,
) -> PublicDemoInferenceGate:
    current = now if now is not None else [0.0]
    return PublicDemoInferenceGate(
        enabled=enabled,
        concurrency_limit=concurrency,
        requests_per_window=requests,
        window_seconds=window,
        max_tracked_clients=clients,
        clock=lambda: current[0],
        salt=b"deterministic-test-salt",
    )


def test_global_concurrency_rejects_without_queueing() -> None:
    limiter = gate(concurrency=2)
    first = limiter.acquire("192.0.2.1")
    second = limiter.acquire("192.0.2.2")

    with pytest.raises(PublicDemoBusyError):
        limiter.acquire("192.0.2.3")

    assert limiter.active_count == 2
    first.release()
    replacement = limiter.acquire("192.0.2.3")
    replacement.release()
    second.release()
    assert limiter.active_count == 0


def test_per_client_window_expires_without_persisting_identity() -> None:
    now = [100.0]
    limiter = gate(requests=2, window=60, now=now)

    limiter.acquire("198.51.100.9").release()
    limiter.acquire("198.51.100.9").release()
    with pytest.raises(PublicDemoBusyError):
        limiter.acquire("198.51.100.9")

    now[0] = 161.0
    limiter.acquire("198.51.100.9").release()
    assert limiter.tracked_client_count == 1
    assert "198.51.100.9" not in repr(limiter._clients)


def test_client_tracking_is_bounded_and_stale_entries_are_evicted() -> None:
    now = [0.0]
    limiter = gate(requests=10, window=10, clients=3, now=now)

    for address in ("192.0.2.1", "192.0.2.2", "192.0.2.3", "192.0.2.4"):
        limiter.acquire(address).release()
    assert limiter.tracked_client_count == 3

    now[0] = 21.0
    limiter.acquire("192.0.2.5").release()
    assert limiter.tracked_client_count == 1


def test_disabled_gate_does_not_track_or_limit_local_requests() -> None:
    limiter = gate(enabled=False, concurrency=1, requests=1, clients=2)
    leases = [limiter.acquire("192.0.2.1") for _ in range(10)]

    assert limiter.active_count == 0
    assert limiter.tracked_client_count == 0
    for lease in leases:
        lease.release()
