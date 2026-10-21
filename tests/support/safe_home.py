"""Guard rails for the T14 tests: they exercise commands that write under `~/.claude`, so the real home
directory must be unreachable. The autouse fixture below (import it into a test module) points HOME at a
decoy dir for the test and makes every "ask the OS for the home directory" helper fail loudly, so code
that ignored the injected HOME errors instead of touching a real user's files. Each test also injects its
own HOME through the `env` it hands to the CLI; the decoy must stay empty."""
from __future__ import annotations

import os
import pathlib

import pytest


@pytest.fixture(autouse=True)
def real_home_is_off_limits(monkeypatch, tmp_path_factory):
    decoy = tmp_path_factory.mktemp("decoy-home")
    monkeypatch.setenv("HOME", str(decoy))
    monkeypatch.setenv("USERPROFILE", str(decoy))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)

    def refuse(*_args, **_kwargs):
        raise AssertionError("code asked the OS for the home directory instead of using the injected HOME")

    monkeypatch.setattr(pathlib.Path, "home", classmethod(refuse))
    monkeypatch.setattr(os.path, "expanduser", refuse)
    yield decoy
    assert list(decoy.iterdir()) == [], f"something wrote into the fallback home: {list(decoy.iterdir())}"
