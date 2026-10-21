"""Expected strings come from the TOON spec's own examples (toonformat.dev), not from the encoder."""
from zoho_axi.core.toon import encode


def test_flat_object_of_scalars():
    assert encode({"id": 123, "name": "Ada", "active": True, "gone": None}) == (
        "id: 123\nname: Ada\nactive: true\ngone: null"
    )


def test_empty_object_encodes_to_empty_output():
    assert encode({}) == ""


def test_nested_object_is_indented_two_spaces():
    assert encode({"user": {"id": 1, "profile": {"name": "Ada"}}}) == (
        "user:\n  id: 1\n  profile:\n    name: Ada"
    )


def test_primitive_array_is_inline_with_length():
    assert encode({"tags": ["a", "b"]}) == "tags[2]: a,b"


def test_empty_arrays_in_fields_and_at_the_root():
    assert encode({"items": []}) == "items: []"
    assert encode([]) == "[]"


def test_root_array_of_primitives():
    assert encode([1, 2, 3]) == "[3]: 1,2,3"


def test_uniform_objects_become_a_tabular_array():
    data = {"users": [{"id": 1, "name": "Ada"}, {"id": 2, "name": "Bob"}]}
    assert encode(data) == "users[2]{id,name}:\n  1,Ada\n  2,Bob"


def test_tabular_array_nested_in_an_object_is_indented_with_it():
    data = {"result": {"rows": [{"k": "a", "v": 1}, {"k": "b", "v": 2}]}}
    assert encode(data) == "result:\n  rows[2]{k,v}:\n    a,1\n    b,2"


def test_mixed_array_uses_hyphen_list():
    assert encode({"items": [1, {"a": 1}, "x"]}) == "items[3]:\n  - 1\n  - a: 1\n  - x"


def test_array_of_arrays_uses_inner_headers():
    assert encode({"pairs": [[1, 2], [3, 4]]}) == "pairs[2]:\n  - [2]: 1,2\n  - [2]: 3,4"


def test_objects_with_different_keys_are_not_tabular():
    data = {"items": [{"a": 1, "b": 2}, {"a": 3}]}
    assert encode(data) == "items[2]:\n  - a: 1\n    b: 2\n  - a: 3"


def test_objects_with_non_primitive_values_are_not_tabular():
    data = {"items": [{"id": 1, "tags": ["x"]}, {"id": 2, "tags": ["y"]}]}
    assert encode(data) == (
        "items[2]:\n  - id: 1\n    tags[1]: x\n  - id: 2\n    tags[1]: y"
    )


def test_list_item_whose_first_field_is_tabular_puts_the_header_on_the_hyphen_line():
    data = {"items": [{"users": [{"id": 1, "name": "Ada"}, {"id": 2, "name": "Bob"}], "status": "active"}]}
    assert encode(data) == (
        "items[1]:\n"
        "  - users[2]{id,name}:\n"
        "      1,Ada\n"
        "      2,Bob\n"
        "    status: active"
    )


def test_tuples_are_treated_as_arrays():
    assert encode({"pair": (1, 2)}) == "pair[2]: 1,2"
