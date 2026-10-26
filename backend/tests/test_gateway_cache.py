"""How the gateway decides when to probe an upstream, and the background health checker."""

import asyncio
import time

import pytest

from app.core.config import settings
from app.core.security import cipher
from app.gateway.service import GatewayService, _CacheEntry
from app.services.mcp_server_service import MCPServerRecord, UpstreamTarget


def target(server_id=1, version="v1", slug="shop") -> UpstreamTarget:
    return UpstreamTarget(id=server_id, slug=slug, name=slug.title(), url="http://x.test/mcp", version=version)


class Harness:
    """A gateway whose upstream probe is replaced by a counter."""

    def __init__(self, **overrides):
        self.service = GatewayService(settings.model_copy(update=overrides), cipher)
        self.probes: list[int] = []
        self.delay = 0.0

        async def fake_probe(t, *, force=False):
            self.probes.append(t.id)
            await asyncio.sleep(self.delay)
            entry = _CacheEntry(version=t.version, expires_at=time.monotonic() + 10)
            self.service._cache[t.id] = entry
            return entry

        self.service._probe = fake_probe  # type: ignore[method-assign]

    def seed(self, t: UpstreamTarget, *, age_expired: bool = False, error: str | None = None) -> _CacheEntry:
        entry = _CacheEntry(
            version=t.version,
            expires_at=time.monotonic() + (-1 if age_expired else 10),
            error=error,
        )
        self.service._cache[t.id] = entry
        return entry

    async def run_checker(self):
        self.service._checker = asyncio.create_task(asyncio.sleep(3600))  # "the checker is running"

    async def stop_checker(self):
        checker, self.service._checker = self.service._checker, None
        if checker:
            checker.cancel()


async def test_a_fresh_result_is_reused_without_probing():
    h = Harness()
    t = target()
    entry = h.seed(t)
    assert await h.service._entry(t) is entry and h.probes == []


async def test_an_expired_result_is_re_probed_when_no_checker_runs():
    h = Harness()
    t = target()
    h.seed(t, age_expired=True)
    await h.service._entry(t)
    assert h.probes == [1]


async def test_an_old_result_is_still_used_while_the_checker_runs():
    h = Harness()
    t = target()
    entry = h.seed(t, age_expired=True)
    await h.run_checker()
    try:
        assert await h.service._entry(t) is entry and h.probes == []  # the checker keeps it current
    finally:
        await h.stop_checker()


async def test_a_server_with_no_result_yet_is_probed_even_with_the_checker():
    h = Harness()
    await h.run_checker()
    try:
        await h.service._entry(target())
        assert h.probes == [1]
    finally:
        await h.stop_checker()


async def test_an_edited_server_is_probed_again_even_with_the_checker():
    h = Harness()
    h.seed(target(version="old"))
    await h.run_checker()
    try:
        await h.service._entry(target(version="new"))  # new URL or credential => new version
        assert h.probes == [1]
    finally:
        await h.stop_checker()


async def test_an_explicit_refresh_always_probes():
    h = Harness()
    t = target()
    h.seed(t)
    await h.run_checker()
    try:
        await h.service._entry(t, refresh=True)
        assert h.probes == [1]
    finally:
        await h.stop_checker()


async def test_a_dead_server_does_not_slow_the_status_overview():
    h = Harness()
    dead, alive = target(1, slug="dead"), target(2, slug="alive")
    h.seed(dead, error="Server 'dead' is unreachable")
    h.seed(alive)
    record = lambda t: MCPServerRecord(id=t.id, name=t.name, slug=t.slug, endpoint_url=t.url, is_enabled=True, description=None)  # noqa: E731
    h.service._load_overview = lambda: ([record(dead), record(alive)], [dead, alive])  # type: ignore[method-assign]
    h.delay = 5.0  # any probe would take 5 seconds
    await h.run_checker()
    try:
        started = time.monotonic()
        statuses = await h.service.overview()
        assert time.monotonic() - started < 1.0 and h.probes == []
        assert {s.server.slug: s.status for s in statuses} == {"dead": "unreachable", "alive": "connected"}
    finally:
        await h.stop_checker()


async def test_overview_can_refresh_one_server_without_touching_the_others():
    h = Harness()
    a, b = target(1, slug="a"), target(2, slug="b")
    h.seed(a)
    h.seed(b)
    record = lambda t: MCPServerRecord(id=t.id, name=t.name, slug=t.slug, endpoint_url=t.url, is_enabled=True, description=None)  # noqa: E731
    h.service._load_overview = lambda: ([record(a), record(b)], [a, b])  # type: ignore[method-assign]
    await h.run_checker()
    try:
        await h.service.overview(refresh={2})
        assert h.probes == [2]
        h.probes.clear()
        await h.service.overview(refresh=True)
        assert sorted(h.probes) == [1, 2]
    finally:
        await h.stop_checker()


async def test_the_checker_probes_repeatedly_and_stops_cleanly():
    h = Harness(health_check_interval_seconds=0.05)
    t = target()
    h.service._load_targets = lambda: [t]  # type: ignore[method-assign]
    h.service.start_health_checks()
    await asyncio.sleep(0.3)
    await h.service.stop_health_checks()
    count = len(h.probes)
    assert count >= 3
    await asyncio.sleep(0.15)
    assert len(h.probes) == count  # nothing runs after stop
    assert h.service._checker is None


async def test_the_checker_is_off_when_the_interval_is_zero():
    h = Harness(health_check_interval_seconds=0)
    h.service.start_health_checks()
    assert h.service._checker is None


async def test_starting_twice_runs_only_one_checker():
    h = Harness(health_check_interval_seconds=0.05)
    h.service._load_targets = lambda: []  # type: ignore[method-assign]
    h.service.start_health_checks()
    first = h.service._checker
    h.service.start_health_checks()
    assert h.service._checker is first
    await h.service.stop_health_checks()


async def test_the_checker_survives_a_failing_round():
    h = Harness(health_check_interval_seconds=0.05)
    calls = {"n": 0}
    t = target()

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("database hiccup")
        return [t]

    h.service._load_targets = flaky  # type: ignore[method-assign]
    h.service.start_health_checks()
    await asyncio.sleep(0.3)
    await h.service.stop_health_checks()
    assert calls["n"] >= 3 and h.probes  # kept going after the first round failed


async def test_results_for_removed_servers_are_dropped():
    h = Harness(health_check_interval_seconds=0.05)
    gone, kept = target(1), target(2)
    h.seed(gone)
    h.seed(kept)
    h.service._load_targets = lambda: [kept]  # type: ignore[method-assign]
    h.service.start_health_checks()
    await asyncio.sleep(0.2)
    await h.service.stop_health_checks()
    assert 1 not in h.service._cache and 2 in h.service._cache


@pytest.mark.parametrize("method", ["stop_health_checks"])
async def test_stopping_without_starting_is_harmless(method):
    await getattr(Harness().service, method)()
