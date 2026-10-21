"""Seam: `estimate_tokens(text)` / `assert_token_budget(text, max_tokens, label)` (tests/support/tokens.py).

Expected counts are worked by hand from the documented proxy: ceil(characters / 4).
"""
import pytest
from support import tokens
from support.tokens import assert_token_budget, estimate_tokens


@pytest.fixture(autouse=True)
def _keep_meta_measurements_out_of_the_run_summary():
    before = list(tokens.MEASUREMENTS)
    yield
    tokens.MEASUREMENTS[:] = before


@pytest.mark.parametrize(
    "text, expected",
    [
        ("", 0),
        ("abcd", 1),  # exactly 4 chars
        ("abcde", 2),  # 5 chars rounds up
        ("x" * 400, 100),
        ("x" * 401, 101),
    ],
)
def test_estimate_is_characters_over_four_rounded_up(text, expected):
    assert estimate_tokens(text) == expected


def test_budget_passes_at_the_limit_and_below():
    assert_token_budget("x" * 400, 100)
    assert_token_budget("x" * 400, 150)


def test_budget_failure_names_measured_and_allowed_and_the_label():
    with pytest.raises(AssertionError) as excinfo:
        assert_token_budget("x" * 2400, 500, label="task default")
    message = str(excinfo.value)
    assert "task default" in message
    assert "measured 600" in message
    assert "allowed 500" in message


def test_budget_failure_without_label_still_reports_both_numbers():
    with pytest.raises(AssertionError, match=r"measured 3\b.*allowed 2\b"):
        assert_token_budget("x" * 12, 2)


def test_budget_rejects_a_negative_limit():
    with pytest.raises(ValueError):
        assert_token_budget("abc", -1)
