"""Runs the official TOON spec encode fixtures (see tests/fixtures/toon/README.md) against `encode`.

Not covered, on purpose:
- cases that set encoder options (delimiter, indent, length marker, key folding): this encoder is
  fixed at comma + two spaces;
- the spec's optional compact forms, keyed tabular headers (`[2:]{...}`) and nested field groups
  (`{a{b,c}}`). The list form we emit instead is valid TOON that decoders accept, just less compact.
  They run as strict xfails so implementing one later forces removing the exclusion.
"""
import json
import re
from pathlib import Path

import pytest

from zoho_projects_axi.core.toon import encode

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "toon"
_COMPACT_EXTENSION = re.compile(r"\[\d*:\]|\{[^}\n]*\{")


def _cases():
    for path in sorted(FIXTURE_DIR.glob("*.json")):
        for case in json.loads(path.read_text(encoding="utf-8"))["tests"]:
            yield pytest.param(case, id=f"{path.stem}::{case['name']}")


@pytest.mark.parametrize("case", list(_cases()))
def test_matches_official_fixture(case, request):
    if case.get("options"):
        pytest.skip("encoder options are not supported (fixed comma delimiter, 2-space indent)")
    if case.get("shouldError"):
        pytest.skip("error-expectation fixtures target option combinations we do not expose")
    if _COMPACT_EXTENSION.search(case["expected"]):
        request.applymarker(pytest.mark.xfail(strict=True, reason="optional compact form not implemented"))
    assert encode(case["input"]) == case["expected"]
