"""Rate limiter, 429 retry and the request counter, observed through the gateway's public behavior."""
import pytest
from support.factories import API, Clock, make_settings
from support.replay import ReplayTransport

from zoho_axi.core.errors import RateLimitedError
from zoho_axi.core.gateway import ZohoGateway

PORTALS = f"{API}/portals"


class Sleeps(list):
    """Records requested sleeps and advances the fake clock by them."""

    def __init__(self, clock):
        super().__init__()
        self.clock = clock

    def __call__(self, seconds):
        self.append(seconds)
        self.clock.now += seconds


def build(tmp_path, replay, **kwargs):
    clock = Clock()
    sleeps = Sleeps(clock)
    gateway = ZohoGateway(
        make_settings(tmp_path), transport=replay, token_provider=lambda: "t", clock=clock, sleep=sleeps, **kwargs
    )
    return gateway, clock, sleeps


def test_the_101st_request_inside_the_window_fails_fast_with_a_rate_limit_error(tmp_path):
    replay = ReplayTransport().get(PORTALS, json={"portals": []}, repeat=True)
    gateway, _, sleeps = build(tmp_path, replay)

    for _ in range(100):
        gateway.get("portals")
    with pytest.raises(RateLimitedError) as exc:
        gateway.get("portals")

    assert exc.value.exit_code == 6
    assert exc.value.code == "rate_limited.client"
    assert "wait" in exc.value.hint.lower()
    assert len(replay.calls) == 100  # the 101st never reached the wire
    assert sleeps == []  # failed fast; did not sleep out a two-minute window


def test_the_window_slides_so_capacity_returns_with_time(tmp_path):
    replay = ReplayTransport().get(PORTALS, json={"portals": []}, repeat=True)
    gateway, clock, _ = build(tmp_path, replay)
    for _ in range(100):
        gateway.get("portals")

    clock.now += 100  # the oldest request is 100s old: 20s to wait, beyond the short cap
    with pytest.raises(RateLimitedError):
        gateway.get("portals")
    clock.now += 21  # the first request is now 121s old
    gateway.get("portals")
    assert len(replay.calls) == 101


def test_limit_and_window_are_configurable(tmp_path):
    replay = ReplayTransport().get(PORTALS, json={"portals": []}, repeat=True)
    gateway, _, _ = build(tmp_path, replay, rate_limit=2, rate_window=10.0)
    gateway.get("portals")
    gateway.get("portals")
    with pytest.raises(RateLimitedError):
        gateway.get("portals")


def test_a_short_wait_for_the_window_is_slept_rather_than_failed(tmp_path):
    replay = ReplayTransport().get(PORTALS, json={"portals": []}, repeat=True)
    gateway, clock, sleeps = build(tmp_path, replay, rate_limit=1, rate_window=10.0, max_wait=2.0)
    gateway.get("portals")
    clock.now += 9  # 1s until the slot frees up, inside the 2s cap
    gateway.get("portals")
    assert sleeps == [pytest.approx(1.0)]
    assert len(replay.calls) == 2


def test_a_429_is_retried_with_backoff_and_then_succeeds(tmp_path):
    replay = ReplayTransport()
    replay.get(PORTALS, status=429)
    replay.get(PORTALS, status=429)
    replay.get(PORTALS, json={"portals": [{"id": "1"}]})
    gateway, _, sleeps = build(tmp_path, replay)

    assert gateway.get("portals") == [{"id": "1"}]
    assert sleeps == [1, 2]
    assert len(replay.calls) == 3


def test_persistent_429_ends_in_a_rate_limit_error_after_three_attempts(tmp_path):
    replay = ReplayTransport().get(PORTALS, status=429, repeat=True)
    gateway, _, _ = build(tmp_path, replay)
    with pytest.raises(RateLimitedError) as exc:
        gateway.get("portals")
    assert (exc.value.exit_code, exc.value.code) == (6, "rate_limited.zoho")
    assert len(replay.calls) == 3


def test_every_attempt_counts_against_the_budget(tmp_path):
    replay = ReplayTransport().get(PORTALS, status=429, repeat=True)
    gateway, _, _ = build(tmp_path, replay, rate_limit=4)
    with pytest.raises(RateLimitedError):
        gateway.get("portals")  # 3 attempts
    with pytest.raises(RateLimitedError) as exc:
        gateway.get("portals")  # the 4th attempt is allowed, the 5th is not
    assert exc.value.code == "rate_limited.client"
    assert len(replay.calls) == 4


def test_request_count_reports_requests_actually_sent_including_fallbacks_and_retries(tmp_path):
    replay = ReplayTransport()
    replay.get(f"{API}/portal/11/projects/22/milestones", status=404)
    replay.get("https://projectsapi.zoho.com/restapi/portal/11/projects/22/milestones/", status=429)
    replay.get("https://projectsapi.zoho.com/restapi/portal/11/projects/22/milestones/", json={"milestones": []})
    gateway, _, _ = build(tmp_path, replay)
    assert gateway.request_count == 0
    gateway.get("milestones", portal="11", project="22")
    assert gateway.request_count == 3
