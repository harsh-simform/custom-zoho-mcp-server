"""Child-process body for the two-process cache contention test (must be importable by `spawn`)."""
from __future__ import annotations

import sys
from pathlib import Path

from support.factories import API, make_settings
from support.replay import ReplayTransport

from zoho_axi.core.cache import CachedGateway, CacheStore
from zoho_axi.core.gateway import ZohoGateway

TASKS = 40


def run(home: str, fresh: bool, start) -> None:
    home_path = Path(home)
    replay = ReplayTransport()
    for n in range(TASKS):
        replay.get(f"{API}/portal/11/projects/22/tasks/{n}", json={"id": str(n), "key": f"P-T{n}"}, repeat=True)
    replay.get(f"{API}/portal/11/users", json={"users": [{"id": 5, "name": "Asha K"}]}, repeat=True)
    settings = make_settings(home_path)
    gateway = ZohoGateway(settings, transport=replay, token_provider=lambda: "tok", sleep=lambda s: None,
                           rate_limit=10_000)
    store = CacheStore(home_path / "cache" / "cache.sqlite")
    cached = CachedGateway(gateway, store, settings, fresh=fresh)

    start.wait(30)  # both children begin together, on a database that does not exist yet
    for _ in range(3):
        for n in range(TASKS):
            cached.get("task", task=str(n), portal="11", project="22")
        cached.get("users", portal="11")
    sys.exit(3 if cached.warnings else 0)
