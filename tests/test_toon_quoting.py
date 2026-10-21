"""Quoting rules per the TOON spec: strings that could be mistaken for another type, or that
contain structural characters, are quoted; everything else stays bare."""
import pytest

from zoho_projects_axi.core.toon import encode


@pytest.mark.parametrize(
    "text, expected",
    [
        ("", '""'),
        (" padded ", '" padded "'),
        ("true", '"true"'),
        ("false", '"false"'),
        ("null", '"null"'),
        ("123", '"123"'),
        ("-3.14", '"-3.14"'),
        ("1e-6", '"1e-6"'),
        ("05", '"05"'),
        ("a:b", '"a:b"'),
        ("https://example.com/x", '"https://example.com/x"'),
        ("2026-10-21T10:00:00Z", '"2026-10-21T10:00:00Z"'),
        ('say "hi"', '"say \\"hi\\""'),
        ("C:\\temp", '"C:\\\\temp"'),
        ("line1\nline2", '"line1\\nline2"'),
        ("a\tb", '"a\\tb"'),
        ("a\rb", '"a\\rb"'),
        ("[x]", '"[x]"'),
        ("{x}", '"{x}"'),
        ("hello, world", '"hello, world"'),
        ("-", '"-"'),
        ("-foo", '"-foo"'),
        ("- item", '"- item"'),
    ],
)
def test_strings_that_need_quotes_are_quoted_and_escaped(text, expected):
    assert encode({"v": text}) == f"v: {expected}"


@pytest.mark.parametrize(
    "text",
    ["hello world", "Ada", "café", "😀 done", "a-b", "x.y", "2026-10-21", "True", "NULL", "snake_case", "50%"],
)
def test_ordinary_strings_stay_bare(text):
    assert encode({"v": text}) == f"v: {text}"


def test_strings_in_inline_arrays_are_quoted_when_they_contain_the_delimiter():
    assert encode({"v": ["a,b", "c"]}) == 'v[2]: "a,b",c'


def test_strings_in_tabular_rows_are_quoted_like_any_value():
    data = {"rows": [{"id": 1, "note": "a: b"}, {"id": 2, "note": "plain"}]}
    assert encode(data) == 'rows[2]{id,note}:\n  1,"a: b"\n  2,plain'


def test_strings_in_list_items_are_quoted_like_any_value():
    assert encode({"v": [1, {"a": 1}, "- x"]}) == 'v[3]:\n  - 1\n  - a: 1\n  - "- x"'


def test_root_string_is_quoted_when_needed():
    assert encode("a:b") == '"a:b"'


@pytest.mark.parametrize(
    "key, rendered",
    [
        ("name", "name"),
        ("_id", "_id"),
        ("user.name", "user.name"),
        ("full name", '"full name"'),
        ("a-b", '"a-b"'),
        ("1st", '"1st"'),
        ("", '""'),
        ("a:b", '"a:b"'),
    ],
)
def test_keys_are_quoted_unless_they_are_simple_identifiers(key, rendered):
    assert encode({key: 1}) == f"{rendered}: 1"


def test_keys_in_tabular_headers_and_array_names_follow_the_same_rule():
    data = {"my rows": [{"first name": "Ada", "id": 1}]}
    assert encode(data) == '"my rows"[1]{"first name",id}:\n  Ada,1'


def test_non_string_keys_are_rejected():
    with pytest.raises(TypeError):
        encode({1: "x"})
