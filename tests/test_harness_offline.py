"""The default suite is offline and credential-free (ticket T04 acceptance), enforced by conftest."""
import os
import socket
from pathlib import Path

import pytest

pytest_plugins = ["pytester"]


def test_outbound_connections_to_non_loopback_hosts_are_blocked():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        with pytest.raises(RuntimeError, match="network access is blocked"):
            sock.connect(("203.0.113.7", 443))  # TEST-NET-3, never routable


def test_zoho_environment_variables_are_removed_for_every_test():
    assert [name for name in os.environ if name.startswith("ZOHO_")] == []


def test_live_marked_tests_are_skipped_unless_the_switch_is_on(pytester, monkeypatch):
    tests_dir = Path(__file__).parent
    monkeypatch.setenv("PYTHONPATH", str(tests_dir))
    monkeypatch.delenv("ZOHO_PROJECTS_AXI_LIVE", raising=False)
    pytester.makeconftest((tests_dir / "conftest.py").read_text(encoding="utf-8"))
    pytester.makepyfile(
        test_sample="""
        import pytest

        @pytest.mark.live
        def test_live():
            pass
        """
    )
    pytester.runpytest_subprocess().assert_outcomes(skipped=1)
    monkeypatch.setenv("ZOHO_PROJECTS_AXI_LIVE", "1")
    pytester.runpytest_subprocess().assert_outcomes(passed=1)
