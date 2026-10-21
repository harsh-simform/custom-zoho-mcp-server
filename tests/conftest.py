"""Suite-wide harness wiring: offline by default, no credentials, opt-in live smoke, golden updates,
token-budget report. See tests/README.md."""
from __future__ import annotations

import ipaddress
import os
import socket

import pytest
from support import golden, tokens

LIVE = "ZOHO_AXI_LIVE"
_LOOPBACK_NAMES = {"localhost"}


def pytest_addoption(parser):
    parser.addoption(
        "--update-goldens",
        action="store_true",
        default=False,
        help="rewrite golden files from the current output (deliberate changes only)",
    )


def pytest_configure(config):
    golden.configure(update=config.getoption("--update-goldens"))
    config.addinivalue_line("markers", "live: talks to the real Zoho API; runs only with ZOHO_AXI_LIVE=1")


def _live_enabled() -> bool:
    return os.environ.get(LIVE) == "1"


def pytest_collection_modifyitems(config, items):
    if _live_enabled():
        return
    skip = pytest.mark.skip(reason="live Zoho smoke test; set ZOHO_AXI_LIVE=1 to run")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


def _is_loopback(host) -> bool:
    if isinstance(host, bytes):
        host = host.decode()
    if host in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@pytest.fixture(autouse=True)
def _offline_and_credential_free(monkeypatch, request):
    """The default suite must never reach the network or depend on real credentials. Outbound
    connections to anything but loopback (the local server in test_http_transport) raise, and
    every ZOHO_* variable is removed from the environment for the test."""
    if "live" in request.keywords and _live_enabled():
        yield
        return
    for name in [n for n in os.environ if n.startswith("ZOHO_")]:
        monkeypatch.delenv(name)

    real_connect = socket.socket.connect

    def guarded_connect(self, address, *args, **kwargs):
        if self.family in (socket.AF_INET, socket.AF_INET6) and not _is_loopback(address[0]):
            raise RuntimeError(
                f"network access is blocked in the default test suite (tried {address[0]!r}); "
                "use the replay transport, or mark the test `live` and run with ZOHO_AXI_LIVE=1"
            )
        return real_connect(self, address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    yield


def pytest_terminal_summary(terminalreporter):
    rows = tokens.MEASUREMENTS
    if not rows:
        return
    terminalreporter.section("token budgets (proxy: ceil(chars/4); measured / allowed)")
    for row in sorted(rows, key=lambda r: r.label):
        pct = f"{100 * row.measured // row.allowed}%" if row.allowed else "n/a"
        terminalreporter.write_line(f"{row.label}: {row.measured} / {row.allowed} ({pct}, {row.chars} chars)")
