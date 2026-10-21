"""Seam: the fixture sanitizer (tests/support/sanitizer.py).

Inputs are invented Zoho-shaped payloads built from literals below; expectations are literals or
patterns written by hand, never produced by calling the sanitizer.
"""
import json
import re

import pytest
from support.sanitizer import SecretDetected, Sanitizer, detect_secrets

# Obviously fake credentials in the real Zoho formats (access/refresh tokens are 1000.<32hex>.<32hex>).
FAKE_ACCESS_TOKEN = "1000." + "a1" * 16 + "." + "b2" * 16
FAKE_REFRESH_TOKEN = "1000." + "c3" * 16 + "." + "d4" * 16
FAKE_CLIENT_SECRET = "e5" * 20  # 40 hex chars, the shape of a Zoho client secret

EMAIL_RE = re.compile(r"^user-[0-9a-f]{8}@example\.test$")


def clean(value, **kwargs):
    return Sanitizer(**kwargs).sanitize_value(value)


# --- the ticket's acceptance case ---------------------------------------------------------------


def test_a_response_with_a_fake_token_email_and_portal_id_emerges_scrubbed():
    raw = {
        "portals": [
            {
                "id": "8123456789012",
                "name": "AcmeInternalPortal",
                "owner": {"zpuid": "7000000012345", "full_name": "Monica Hemsworth", "email": "monica@acme-corp.com"},
            }
        ],
        "note": f"debug dump: Authorization: Zoho-oauthtoken {FAKE_ACCESS_TOKEN} see https://projects.acme-corp.com/portal/AcmeInternalPortal/x",
    }
    out = clean(raw, literals=["AcmeInternalPortal", "acme-corp"])
    text = json.dumps(out)

    for leaked in (
        FAKE_ACCESS_TOKEN,
        "monica@acme-corp.com",
        "acme-corp",
        "AcmeInternalPortal",
        "8123456789012",
        "7000000012345",
        "Monica",
        "Hemsworth",
    ):
        assert leaked not in text, leaked
    assert EMAIL_RE.match(out["portals"][0]["owner"]["email"])
    assert re.fullmatch(r"[1-9]\d{12}", out["portals"][0]["id"])  # same shape: 13 digits, string


# --- emails and names ---------------------------------------------------------------------------


def test_emails_are_replaced_consistently_in_fields_and_in_free_text():
    raw = {
        "created_by": {"zpuid": "1", "email": "Asha.K@Example-Corp.com", "name": "Asha K"},
        "comment": "cc asha.k@example-corp.com and ravi@example-corp.com please",
    }
    out = clean(raw)
    field_email = out["created_by"]["email"]
    assert EMAIL_RE.match(field_email)
    assert out["comment"].count("@example.test") == 2
    assert field_email in out["comment"]  # same address (case-insensitive) -> same replacement
    ravi = re.findall(r"user-[0-9a-f]{8}@example\.test", out["comment"])[1]
    assert ravi != field_email


def test_person_names_are_replaced_consistently_across_name_fields_and_comment_text():
    raw = {
        "owner": {
            "zpuid": "5000000000001",
            "name": "Priyanka Venkataraman",
            "full_name": "Priyanka Venkataraman",
            "first_name": "Priyanka",
            "last_name": "Venkataraman",
            "email": "p@v.com",
        },
        "comment": "Thanks Priyanka Venkataraman, and Priyanka again.",
    }
    out = clean(raw)
    owner = out["owner"]
    assert "Priyanka" not in json.dumps(out) and "Venkataraman" not in json.dumps(out)
    assert owner["full_name"] == f"{owner['first_name']} {owner['last_name']}"
    assert owner["name"] == owner["full_name"]
    assert out["comment"] == f"Thanks {owner['full_name']}, and {owner['first_name']} again."


def test_names_of_things_that_are_not_people_are_kept():
    raw = {
        "name": "Fix login redirect loop",
        "status": {"id": "1", "name": "In Progress"},
        "tasklist": {"id": "2", "name": "Auth"},
        "owner": {"zpuid": "5000000000001", "name": "Zed Zebra", "email": "z@z.com"},
    }
    out = clean(raw)
    assert out["name"] == "Fix login redirect loop"
    assert out["status"]["name"] == "In Progress"
    assert out["tasklist"]["name"] == "Auth"
    assert out["owner"]["name"] != "Zed Zebra"


# --- ids ----------------------------------------------------------------------------------------


def test_ids_keep_their_length_type_and_digits_only_and_map_consistently():
    raw = {
        "id": "4000000007005",
        "tasklist": {"id": "4000000007005"},
        "numeric": {"id": 73746290},
        "zuid": 27805036,
        "entity_id": "4000000007005",
        "ids": ["4000000007005", "4000000007006"],
    }
    out = clean(raw)
    assert re.fullmatch(r"[1-9]\d{12}", out["id"]) and out["id"] != "4000000007005"
    assert out["tasklist"]["id"] == out["id"] == out["entity_id"]  # one real id, one fake id
    assert isinstance(out["numeric"]["id"], int) and len(str(out["numeric"]["id"])) == 8
    assert out["numeric"]["id"] != 73746290
    assert isinstance(out["zuid"], int) and out["zuid"] != 27805036
    assert out["ids"][0] == out["id"] and out["ids"][1] != out["id"]


def test_short_ids_and_counters_are_left_alone():
    out = clean({"id": "7", "page": 1, "count": 12345678, "total_attachments_count": 3})
    assert out == {"id": "7", "page": 1, "count": 12345678, "total_attachments_count": 3}


def test_time_size_and_duration_values_that_look_like_ids_are_left_alone():
    raw = {"duration_in_millis": "28942361038", "size": "4197012345", "created_time": "2024-12-16T06:40:24.426Z"}
    assert clean(raw) == raw


def test_long_digit_runs_in_free_text_are_treated_as_ids():
    out = clean({"comment": "see task 4000000007005 for details"})
    assert "4000000007005" not in out["comment"]
    assert re.fullmatch(r"see task [1-9]\d{12} for details", out["comment"])


def test_pinned_ids_map_to_the_canonical_fake_so_tests_can_name_them():
    out = clean({"id": "4000000007005", "other": {"id": "4000000007006"}}, pins={"4000000007005": "30000000001"})
    assert out["id"] == "30000000001"
    assert out["other"]["id"] != "30000000001"


# --- urls ---------------------------------------------------------------------------------------


def test_zoho_hosts_survive_but_query_strings_fragments_and_ids_do_not():
    url = "https://download.zoho.com/v1/workdrive/download/b8vet771b86e7c97147af9b7e1754c19b5118?x-cli-msg=SIGNEDBLOB&authId=%7B%22a%22%7D#frag"
    out = Sanitizer().sanitize_url(url)
    assert out.startswith("https://download.zoho.com/v1/workdrive/download/")
    assert "?" not in out and "#" not in out and "SIGNEDBLOB" not in out
    assert "b8vet771b86e7c97147af9b7e1754c19b5118" not in out
    assert len(out.rsplit("/", 1)[1]) == len("b8vet771b86e7c97147af9b7e1754c19b5118")  # same shape


def test_request_urls_keep_their_structure_with_mapped_ids():
    san = Sanitizer(pins={"8123456789012": "10000000001", "4000000007005": "30000000001"})
    out = san.sanitize_url("https://projectsapi.zoho.com/api/v3/portal/8123456789012/projects/4000000000001/tasks/4000000007005")
    assert out.startswith("https://projectsapi.zoho.com/api/v3/portal/10000000001/projects/")
    assert out.endswith("/tasks/30000000001")
    assert "4000000000001" not in out


def test_non_zoho_hosts_and_portal_names_in_paths_are_replaced():
    out = Sanitizer().sanitize_url("https://projects.acme-corp.com/portal/acmeportal/milestones/6000000019045")
    assert "acme" not in out
    assert re.fullmatch(r"https://host-[0-9a-f]{8}\.example\.test/portal/portal-[0-9a-f]{6}/milestones/[1-9]\d{12}", out)


def test_urls_inside_free_text_are_scrubbed_too():
    out = clean({"comment": "logs at https://evil.example.org/p?token=abc and done"})
    assert "evil.example.org" not in out["comment"] and "token=abc" not in out["comment"]
    assert out["comment"].endswith(" and done")


# --- tokens and secrets -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, kind",
    [
        ({"x": FAKE_ACCESS_TOKEN}, "zoho-token"),
        ({"x": f"Authorization: Zoho-oauthtoken {FAKE_ACCESS_TOKEN}"}, "zoho-token"),
        ({"x": "Bearer abcdefghijklmnopqrstuvwxyz0123"}, "bearer-token"),
        ({"x": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijk"}, "jwt"),
        ({"access_token": "whatever"}, "key:access_token"),
        ({"nested": [{"refresh_token": FAKE_REFRESH_TOKEN}]}, "key:refresh_token"),
        ({"client_secret": "shh"}, "key:client_secret"),
        ({"x": f"leaked {FAKE_CLIENT_SECRET}"}, "client-secret-shaped"),
        ({"x": "https://x.zoho.com/cb?access_token=abc123"}, "secret-in-query"),
    ],
)
def test_secrets_are_detected_by_shape_and_by_key(raw, kind):
    assert kind in detect_secrets(raw)


def test_ordinary_zoho_payloads_have_no_secret_findings():
    ordinary = {
        "tasks": [{"id": "4000000007005", "prefix": "RU1-T1", "name": "Prepare release notes"}],
        "page_info": {"has_next_page": False},
        "token": "",  # empty values are not secrets
    }
    assert detect_secrets(ordinary) == []


def test_scrubbing_removes_token_values_even_though_writing_will_be_refused():
    out = clean({"msg": f"tok {FAKE_ACCESS_TOKEN}", "access_token": "plain-value", "client_secret": FAKE_CLIENT_SECRET})
    assert FAKE_ACCESS_TOKEN not in json.dumps(out) and "plain-value" not in json.dumps(out)
    assert FAKE_CLIENT_SECRET not in json.dumps(out)
    assert detect_secrets(out) == []  # the scrubbed copy is clean


def test_findings_never_contain_the_secret_value():
    kinds = detect_secrets({"x": FAKE_ACCESS_TOKEN, "client_secret": FAKE_CLIENT_SECRET})
    assert all(FAKE_ACCESS_TOKEN not in k and FAKE_CLIENT_SECRET not in k for k in kinds)


# --- other scrubbing, determinism ---------------------------------------------------------------


def test_phone_numbers_are_masked():
    assert clean({"phone": "+44 20 7946 0958", "mobile": "9845252323"}) == {"phone": "+0000000000", "mobile": "+0000000000"}


def test_free_text_can_be_masked_to_same_length_filler_when_requested():
    raw = {"description": "Customer Jane's card ends 4242 and she lives at 5 Elm St.", "name": "Keep me"}
    out = clean(raw, mask_text_keys=["description"])
    assert len(out["description"]) == len(raw["description"])
    assert "Jane" not in out["description"] and "Elm" not in out["description"]
    assert out["name"] == "Keep me"


def test_sanitizing_is_deterministic_across_instances_and_runs():
    raw = {
        "id": "4000000007005",
        "owner": {"zpuid": "5000000000001", "full_name": "Asha Kumar", "email": "asha@corp.com"},
        "url": "https://projects.corp.com/portal/corp/tasks/4000000007005?x=1",
    }
    first = json.dumps(clean(raw))
    second = json.dumps(clean(raw))
    assert first == second


def test_the_hashing_scheme_is_pinned_so_changing_it_is_a_conscious_re_record():
    # Literal values captured once from the shipped scheme (SHA-256 + fixed salt). If this fails,
    # every recorded fixture would map differently on re-recording: bump the salt on purpose.
    out = clean({"id": "8123456789012", "owner": {"zpuid": "7000000012345", "email": "monica@acme-corp.com"}})
    assert out["id"] == "5325939514643"
    assert out["owner"]["zpuid"] == "1122802330240"
    assert out["owner"]["email"] == "user-3d47dfab@example.test"


def test_the_input_is_not_mutated():
    raw = {"owner": {"zpuid": "5000000000001", "email": "asha@corp.com"}}
    snapshot = json.dumps(raw)
    clean(raw)
    assert json.dumps(raw) == snapshot


# --- recorded entries ---------------------------------------------------------------------------


def test_entries_are_scrubbed_in_url_params_and_body_but_keep_method_and_status():
    entry = {
        "method": "GET",
        "url": "https://projectsapi.zoho.com/restapi/portal/8123456789012/projects/4000000000001/search",
        "params": {"search_term": "RU1-T1", "module": "tasks", "filter": '{"value": ["4000000007005"]}'},
        "status": 200,
        "json": {"tasks": [{"id": "4000000007005", "key": "RU1-T1"}]},
    }
    out = Sanitizer().sanitize_entry(entry)
    assert out["method"] == "GET" and out["status"] == 200
    assert out["params"]["search_term"] == "RU1-T1" and out["params"]["module"] == "tasks"
    blob = json.dumps(out)
    for real in ("8123456789012", "4000000000001", "4000000007005"):
        assert real not in blob
    # the id inside the JSON-in-a-string param is the same fake as the one in the body
    assert out["json"]["tasks"][0]["id"] in out["params"]["filter"]


def test_binary_download_bodies_are_never_kept():
    entry = {"method": "GET", "url": "https://download.zoho.com/v1/x", "status": 200,
             "content_type": "image/png", "body_base64": "UkVBTFBJWEVMUw=="}
    out = Sanitizer().sanitize_entry(entry)
    assert out["body_base64"] != "UkVBTFBJWEVMUw=="
    assert out["content_type"] == "image/png"


# --- refuse to write ----------------------------------------------------------------------------


def test_write_refuses_and_writes_nothing_when_a_secret_is_present(tmp_path):
    from support.recording import write_fixture

    target = tmp_path / "x" / "leak.json"
    entries = [{"method": "GET", "url": "https://projectsapi.zoho.com/api/v3/portals", "status": 200,
                "json": {"portals": [], "debug": FAKE_REFRESH_TOKEN}}]
    with pytest.raises(SecretDetected) as excinfo:
        write_fixture(target, entries, groups={"all": "recorded"}, sanitizer=Sanitizer())
    assert FAKE_REFRESH_TOKEN not in str(excinfo.value)
    assert not target.exists() and not target.parent.exists()


def test_write_stores_the_scrubbed_entries_with_provenance(tmp_path):
    from support.recording import write_fixture

    target = tmp_path / "portals" / "list.json"
    entries = [{"method": "GET", "url": "https://projectsapi.zoho.com/api/v3/portals", "status": 200,
                "json": {"portals": [{"id": "8123456789012", "name": "P"}]}}]
    write_fixture(target, entries, groups={"envelope": "recorded"}, sanitizer=Sanitizer())
    stored = json.loads(target.read_text(encoding="utf-8"))
    assert stored["_provenance"]["groups"] == {"envelope": "recorded"}
    assert "8123456789012" not in target.read_text(encoding="utf-8")
    assert stored["entries"][0]["json"]["portals"][0]["name"] == "P"
    assert target.read_text(encoding="utf-8").endswith("}\n")


def test_write_does_not_overwrite_without_force(tmp_path):
    from support.recording import write_fixture

    target = tmp_path / "a.json"
    target.write_text("{}", encoding="utf-8")
    entries = [{"method": "GET", "url": "https://projectsapi.zoho.com/api/v3/portals", "status": 200, "json": {}}]
    with pytest.raises(FileExistsError):
        write_fixture(target, entries, groups={"all": "recorded"}, sanitizer=Sanitizer())
    write_fixture(target, entries, groups={"all": "recorded"}, sanitizer=Sanitizer(), force=True)
    assert "_provenance" in json.loads(target.read_text(encoding="utf-8"))
