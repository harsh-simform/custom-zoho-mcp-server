"""`zoho-projects-axi people [--users] [--tags]` (T10) at the product seam. Zoho bodies are invented-but-plausible
(see tests/support/world.py); expected texts are hand-written literals."""
import pytest
from support.world import P, World, replay_of, tags_body, users_body

USERS = users_body(("5001", "Alex Sample", "Administrator"), ("5002", "Bea Fixture", "Employee"))
TAGS = tags_body(("7001", "checkout"), ("7002", "needs-design"))


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def both():
    return replay_of((f"{P}/users", USERS), (f"{P}/tags", TAGS))


def test_people_shows_users_and_tags_by_default(world):
    code, out, err = world.run(["people"], both())
    assert code == 0
    assert out == "\n".join([
        "counts: users=2 tags=2",
        "users[2]{id,name,role}:",
        '  "5001",Alex Sample,Administrator',
        '  "5002",Bea Fixture,Employee',
        "tags[2]{id,name}:",
        '  "7001",checkout',
        '  "7002",needs-design',
        "help[1]{run}:",
        "  zoho-projects-axi tasks --owner 'Alex Sample'",
    ])
    assert world.requests(err) == 2


def test_users_flag_fetches_only_users(world):
    code, out, err = world.run(["people", "--users"], replay_of((f"{P}/users", USERS)))
    assert code == 0
    assert "users[2]" in out and "tags" not in out
    assert world.requests(err) == 1


def test_tags_flag_fetches_only_tags(world):
    code, out, err = world.run(["people", "--tags"], replay_of((f"{P}/tags", TAGS)))
    assert code == 0
    assert "tags[2]" in out and "users" not in out
    assert world.requests(err) == 1


def test_no_tags_is_an_explicit_empty_state(world):
    _, out, _ = world.run(["people", "--tags"], replay_of((f"{P}/tags", tags_body())))
    assert out.splitlines()[:2] == ["counts: tags=0", "tags: 0 results"]


def test_warm_call_makes_zero_requests(world):
    cold = world.run(["people"], both())
    warm = world.run(["people"])
    assert warm[:2] == cold[:2]
    assert world.requests(warm[2]) == 0


def test_both_flags_equal_the_default(world):
    code, out, _ = world.run(["people", "--users", "--tags"], both())
    assert code == 0 and "users[2]" in out and "tags[2]" in out


def test_default_row_cap_is_announced_and_limit_raises_it(world):
    many = users_body(*[(str(5000 + n), f"Person {n}", "Employee") for n in range(60)])
    _, out, _ = world.run(["people", "--users"], replay_of((f"{P}/users", many)))
    assert "users[50]{id,name,role}:" in out
    assert 'truncated[1]: "users (+10 rows, --limit 60)"' in out
    _, out, _ = world.run(["people", "--users", "--limit", "60"], replay_of((f"{P}/users", many)))
    assert "users[60]" in out and "truncated" not in out


def test_more_users_on_zoho_than_one_page_marks_the_count_as_a_floor(world):
    body = users_body(("5001", "Alex Sample", "Employee"))
    body["page_info"]["has_next_page"] = True
    _, out, _ = world.run(["people", "--users"], replay_of((f"{P}/users", body)))
    assert out.splitlines()[0] == "counts: users=1+"


def test_json_flag(world):
    import json

    _, out, _ = world.run(["people", "--users", "--json"], replay_of((f"{P}/users", USERS)))
    doc = json.loads(out)
    assert doc["users"][0] == {"id": "5001", "name": "Alex Sample", "role": "Administrator"}


def test_full_adds_the_extra_fields_flat(world):
    _, out, _ = world.run(["people", "--users", "--full"], replay_of((f"{P}/users", USERS)))
    assert "alex@x.test" in out
