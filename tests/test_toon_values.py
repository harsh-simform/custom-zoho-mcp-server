import datetime
import json

import pytest

from zoho_axi.core.toon import encode


@pytest.mark.parametrize(
    "number, expected",
    [
        (0, "0"),
        (-42, "-42"),
        (10**20, "100000000000000000000"),
        (1.0, "1"),
        (1.5, "1.5"),
        (2.50, "2.5"),
        (0.1, "0.1"),
        (-0.0, "0"),
        (1e6, "1000000"),
        (1e21, "1000000000000000000000"),
        (1e-7, "0.0000001"),
        (123456789.123, "123456789.123"),
    ],
)
def test_numbers_are_canonical_decimals_without_exponents_or_trailing_zeros(number, expected):
    assert encode({"n": number}) == f"n: {expected}"


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_numbers_become_null(bad):
    assert encode({"n": bad}) == "n: null"


def test_booleans_are_not_numbers():
    assert encode({"yes": True, "no": False}) == "yes: true\nno: false"


def test_numbers_inside_arrays_and_tables_use_the_same_form():
    assert encode({"v": [1.0, 2.5]}) == "v[2]: 1,2.5"
    assert encode({"rows": [{"a": 1.0}, {"a": 2.0}]}) == "rows[2]{a}:\n  1\n  2"


@pytest.mark.parametrize("unsupported", [datetime.date(2026, 10, 21), {1, 2}, b"bytes", object()])
def test_unsupported_values_raise_a_clear_type_error(unsupported):
    with pytest.raises(TypeError, match="TOON"):
        encode({"v": unsupported})


def test_unsupported_value_inside_a_list_raises_too():
    with pytest.raises(TypeError, match="TOON"):
        encode({"v": [1, {"a": 1}, datetime.date(2026, 10, 21)]})


def test_output_has_no_trailing_newline_or_trailing_spaces():
    out = encode({"a": {"b": [1, {"c": 2}]}, "d": "x"})
    assert not out.endswith("\n")
    assert all(line == line.rstrip() for line in out.split("\n"))


def test_tabular_task_list_is_at_least_25_percent_smaller_than_compact_json():
    """T03 acceptance: measurable saving on a representative tabular payload (character count
    is the proxy here; per-command token budgets arrive with the test harness, T04)."""
    statuses = ["Open", "In Progress", "Closed", "In Review"]
    rows = [
        {
            "key": f"PROJ1-T{1000 + i}",
            "title": f"Fix item number {i} in the checkout flow",
            "status": statuses[i % 4],
            "owner": ["Asha K", "Ben R", "Chen L"][i % 3],
        }
        for i in range(20)
    ]
    toon = encode({"tasks": rows})
    compact_json = json.dumps({"tasks": rows}, separators=(",", ":"))
    saving = 1 - len(toon) / len(compact_json)
    assert saving >= 0.25, f"only {saving:.0%} smaller"
