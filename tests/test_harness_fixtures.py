"""Seams: the gateway read seam (S1) with the fixture replay at the transport seam (S2), driven by the
committed synthetic fixtures under tests/fixtures/zoho/, plus the token/golden helpers on rendered output.

This file proves the fixture set is usable and covers every "Known Zoho quirk" of 00-overview. Expected
values are facts about the fixtures written out as literals (counts, ids, file names), not recomputed.
"""
import json
import re

import pytest
from support.factories import API, LEGACY, make_gateway
from support.fixtures import (
    BUG,
    IDS,
    MILESTONE,
    PORTAL,
    PROJECT,
    TASK,
    TASK_PARENT,
    all_fixture_names,
    check_provenance,
    entries_for,
    fixture_path,
    replay_for,
)
from support.golden import assert_golden
from support.replay import UnrecordedRequest
from support.sanitizer import detect_secrets
from support.tokens import assert_token_budget

from zoho_axi.core.errors import RateLimitedError, UpstreamError, ZohoAxiError
from zoho_axi.core.render import render
from zoho_axi.core.result import Result, Truncation

PB = f"/portal/{PORTAL}/projects/{PROJECT}"

# The set the ticket asks to capture up front (one entry per bullet; names are <endpoint>/<name>).
REQUIRED_FIXTURES = [
    "portals/list", "projects/list", "projects/detail", "tasklists/list", "milestones/v3_404_legacy_ok",
    "tasks/list", "tasks/detail_with_subtasks", "task_comments/v3_404_legacy_ok", "task_comments/v3_ok",
    "task_status_history/unfiltered_page", "bugs/detail_with_task", "bug_comments/v3_ok",
    "task_attachments/image_and_non_image", "task_attachments/v3_404_legacy_upper_case",
    "users/list", "tags/list", "search/hit", "search/miss",
]


# --- the fixture files themselves ----------------------------------------------------------------


def test_every_fixture_the_ticket_lists_exists():
    missing = [name for name in REQUIRED_FIXTURES if not fixture_path(name).exists()]
    assert missing == []


def test_every_fixture_carries_a_provenance_marker_per_field_group():
    names = all_fixture_names()
    assert len(names) >= 30
    for name in names:
        document = json.loads(fixture_path(name).read_text(encoding="utf-8"))
        assert check_provenance(document) == [], name
        assert document["entries"], name


def test_a_fixture_without_provenance_is_rejected_by_the_loader(tmp_path, monkeypatch):
    from support import fixtures

    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "bad.json").write_text(json.dumps({"entries": []}), encoding="utf-8")
    monkeypatch.setattr(fixtures, "FIXTURE_ROOT", tmp_path)
    with pytest.raises(ValueError, match="_provenance"):
        fixtures.load_fixture("x/bad")


def test_committed_fixtures_hold_no_secrets_real_looking_emails_or_signed_urls():
    for name in all_fixture_names():
        text = fixture_path(name).read_text(encoding="utf-8")
        assert detect_secrets(json.loads(text)) == [], name
        for email in re.findall(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", text):
            assert email.endswith("@example.test"), (name, email)
        for url in re.findall(r"https?://[^\s\"'<>\\]+", json.dumps(json.loads(text)["entries"])):
            assert "?" not in url and "#" not in url, (name, url)


def test_synthetic_ids_are_the_canonical_fake_ones():
    text = fixture_path("tasks/detail").read_text(encoding="utf-8")
    assert f"/portal/{PORTAL}/projects/{PROJECT}/tasks/{TASK}" in text
    assert "9000000" not in text  # none of the research-sample ids leaked into a committed fixture


# --- replay: unknown requests fail and print the missing key ------------------------------------


def test_an_unrecorded_request_fails_naming_the_method_url_and_params(tmp_path):
    gateway = make_gateway(tmp_path, replay_for("tasks/list"))
    with pytest.raises(UnrecordedRequest) as excinfo:
        gateway.get("tasks", params={"page": "2"}, **IDS)
    message = str(excinfo.value)
    assert f"GET {API}{PB}/tasks" in message
    assert "'page': '2'" in message


def test_a_fixture_entry_is_consumed_once_so_an_unexpected_second_call_fails(tmp_path):
    gateway = make_gateway(tmp_path, replay_for("tasks/detail"))
    gateway.get("task", task=TASK, **IDS)
    with pytest.raises(UnrecordedRequest):
        gateway.get("task", task=TASK, **IDS)


# --- quirk 1: v3 -> legacy fallback on 404 (milestones, comments, attachments) -------------------


@pytest.mark.parametrize(
    "fixture, resource, ids, legacy_path, expected_first",
    [
        ("milestones/v3_404_legacy_ok", "milestones", {}, f"{PB}/milestones/", MILESTONE),
        ("task_comments/v3_404_legacy_ok", "task_comments", {"task": TASK}, f"{PB}/tasks/{TASK}/comments/", None),
        ("task_attachments/v3_404_legacy_upper_case", "task_attachments", {"task": TASK},
         f"{PB}/tasks/{TASK}/attachments/", None),
    ],
)
def test_v3_404_then_legacy_200_is_served_in_that_order(tmp_path, fixture, resource, ids, legacy_path, expected_first):
    replay = replay_for(fixture)
    records = make_gateway(tmp_path, replay).get(resource, **ids, **IDS)

    assert [c.url for c in replay.calls] == [f"{API}{legacy_path[:-1]}", f"{LEGACY}{legacy_path}"]
    assert len(records) >= 1
    if expected_first:
        assert records[0]["id"] == expected_first
    assert replay.unused == []


def test_the_bug_attachments_fallback_sequence_is_v3_404_then_legacy(tmp_path):
    replay = replay_for("bug_attachments/v3_404_legacy_attachment_details")
    make_gateway(tmp_path, replay).get("bug_attachments", bug=BUG, **IDS)
    assert [c.url for c in replay.calls] == [
        f"{API}{PB}/bugs/{BUG}/attachments", f"{LEGACY}{PB}/bugs/{BUG}/attachments/"]


def test_legacy_bug_attachment_details_wrapper_is_unwrapped(tmp_path):
    records = make_gateway(tmp_path, replay_for("bug_attachments/v3_404_legacy_attachment_details")).get(
        "bug_attachments", bug=BUG, **IDS)
    assert [r["file_name"] for r in records] == ["checkout-500.png", "SessionValidation.java"]


def test_v3_bug_attachment_name_and_type_are_lifted_out_of_file_info(tmp_path):
    records = make_gateway(tmp_path, replay_for("bug_attachments/v3_nested_file_info")).get(
        "bug_attachments", bug=BUG, **IDS)
    assert [(r["file_name"], r["content_type"]) for r in records] == [
        ("checkout-500.png", "image/png"), ("SessionValidation.java", "application/x-unknown-mime-type")]


def test_the_v3_comments_fixture_needs_no_fallback(tmp_path):
    replay = replay_for("task_comments/v3_ok")
    comments = make_gateway(tmp_path, replay).get("task_comments", task=TASK, **IDS)
    assert len(replay.calls) == 1
    assert len(comments) == 2 and comments.has_more is False


# --- quirk 2: taskstatushistory ignores task scoping ---------------------------------------------


def test_the_status_history_page_is_unfiltered_and_filtering_is_the_callers_job(tmp_path):
    gateway = make_gateway(tmp_path, replay_for("task_status_history/unfiltered_page"))
    page = gateway.get("task_status_history", params={"task_id": TASK}, **IDS)  # Zoho ignores the param
    assert len(page) == 3
    assert {entry["id"] for entry in page} != {TASK}  # other tasks' histories are on the page too
    assert TASK in {entry["id"] for entry in page}


def test_a_status_history_page_may_not_contain_the_task_at_all(tmp_path):
    gateway = make_gateway(tmp_path, replay_for("task_status_history/target_absent"))
    page = gateway.get("task_status_history", **IDS)
    assert len(page) == 2 and TASK not in {entry["id"] for entry in page}


# --- quirk 3: subtask scoping is ignored; trust association_info.has_subtasks --------------------


def test_the_subtask_probe_returns_the_unfiltered_project_task_page(tmp_path):
    gateway = make_gateway(tmp_path, replay_for("tasks/detail_with_subtasks"))
    parent = gateway.get("task", task=TASK_PARENT, **IDS)
    assert parent["association_info"]["has_subtasks"] is True

    probe = gateway.get("tasks", params={"parent_id": TASK_PARENT}, **IDS)
    depths = sorted(t["depth"] for t in probe)
    assert depths == [0, 0, 0, 1, 1]  # three unrelated top-level tasks came back with the two real subtasks
    assert [t["parental_info"]["parent_task_id"] for t in probe if t["depth"] == 1] == [TASK_PARENT, TASK_PARENT]


def test_a_task_without_subtasks_reports_it_and_has_no_subtask_probe_recorded(tmp_path):
    replay = replay_for("tasks/detail")
    task = make_gateway(tmp_path, replay).get("task", task=TASK, **IDS)
    assert task["association_info"]["has_subtasks"] is False
    assert len(entries_for("tasks/detail")) == 1  # a command that probes anyway raises UnrecordedRequest


# --- quirk 4: human keys resolve through the Search API ------------------------------------------


def test_search_hit_resolves_a_human_key_to_an_id(tmp_path):
    gateway = make_gateway(tmp_path, replay_for("search/hit"))
    hits = gateway.get("search", params={"search_term": "DM1-T2", "module": "tasks"}, **IDS)
    assert [(h["key"], h["id"]) for h in hits] == [("DM1-T2", TASK)]


@pytest.mark.parametrize("fixture", ["search/miss", "search/miss_empty_object"])
def test_search_miss_is_an_empty_collection(tmp_path, fixture):
    gateway = make_gateway(tmp_path, replay_for(fixture))
    assert gateway.get("search", params={"search_term": "NOPE-T999", "module": "tasks"}, **IDS) == []


def test_bug_search_has_two_shapes_the_code_and_the_docs_disagree_on(tmp_path):
    code_shape = make_gateway(tmp_path, replay_for("search/bug_hit_code_shape")).get(
        "search", params={"search_term": "DM1-I1", "module": "bugs"}, **IDS)
    assert [(h["key"], h["id"]) for h in code_shape] == [("DM1-I1", BUG)]
    docs_shape = make_gateway(tmp_path, replay_for("search/bug_hit_docs_shape")).get(
        "search", params={"search_term": "DM1-I1", "module": "bugs"}, **IDS)
    # the documented `issue` wrapper is recognised since T09 (the hit keeps Zoho's own field names)
    assert [(h["prefix"], h["issue_id"]) for h in docs_shape] == [("DM1-I1", 40000000001)]
    assert [h["issue_id_str"] for h in docs_shape] == [BUG]


# --- quirk 5: field casing varies (FILENAME vs file_name) ----------------------------------------


def test_attachment_records_normalize_whatever_casing_they_arrive_in(tmp_path):
    records = make_gateway(tmp_path, replay_for("task_attachments/v3_404_legacy_upper_case")).get(
        "task_attachments", task=TASK, **IDS)
    assert [r["file_name"] for r in records] == [
        "checkout-error.png", "design-review-notes.pdf", "screenshot-2.JPG", "wireframe.webp"]  # UPPER_CASE x3, camelCase x1
    assert [r["content_type"] for r in records] == [
        "image/png", "application/pdf", "application/octet-stream", "image/webp"]
    assert all(r["download_url"].startswith("https://projects.zoho.com/") for r in records)


def test_v3_attachment_records_with_name_and_type_spellings_normalize_too(tmp_path):
    records = make_gateway(tmp_path, replay_for("task_attachments/image_and_non_image")).get(
        "task_attachments", task=TASK, **IDS)
    assert [(r["file_name"], r["content_type"]) for r in records] == [
        ("checkout-error.png", "image/png"), ("design-review-notes.pdf", "application/pdf")]


# --- quirk 6: the OAuth token only ever goes to Zoho hosts ---------------------------------------


def test_the_image_download_fixture_serves_a_stub_png_through_the_guarded_download_path(tmp_path):
    replay = replay_for("task_attachments/image_and_non_image")
    gateway = make_gateway(tmp_path, replay)
    records = gateway.get("task_attachments", task=TASK, **IDS)
    data, content_type = gateway.download(records[0]["download_url"])
    assert content_type == "image/png" and data.startswith(b"\x89PNG\r\n")
    assert len(data) < 200  # a stub, never real customer bytes


@pytest.mark.parametrize("index", [0, 1, 2, 3])
def test_hostile_download_urls_are_refused_before_the_token_or_the_network_is_touched(tmp_path, index):
    replay = replay_for("task_attachments/hostile_download_urls")
    token_reads = []
    gateway = make_gateway(tmp_path, replay, token_provider=lambda: token_reads.append(1) or "tok")
    records = gateway.get("task_attachments", task=TASK, **IDS)
    reads_after_list = len(token_reads)
    with pytest.raises(UpstreamError) as excinfo:
        gateway.download(records[index]["download_url"])
    assert excinfo.value.code == "download.host_refused"
    assert len(token_reads) == reads_after_list and len(replay.calls) == 1  # only the list call ever happened


# --- quirk 7: rate limiting ----------------------------------------------------------------------


def test_a_429_is_retried_and_the_second_answer_is_served(tmp_path):
    replay = replay_for("errors/rate_limited_429_then_ok")
    gateway = make_gateway(tmp_path, replay)
    tasks = gateway.get("tasks", **IDS)
    assert len(tasks) == 4 and gateway.request_count == 2


@pytest.mark.parametrize("fixture", ["errors/rate_limited_429_exhausted", "errors/rate_limited_429_html"])
def test_three_429s_in_a_row_end_as_a_rate_limited_error(tmp_path, fixture):
    gateway = make_gateway(tmp_path, replay_for(fixture))
    with pytest.raises(RateLimitedError):
        gateway.get("tasks", **IDS)
    assert gateway.request_count == 3


# --- error envelopes -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fixture, resource, ids, code",
    [
        ("errors/unauthorized_401", "tasks", {}, "auth.unauthorized"),
        ("errors/forbidden_403", "task", {"task": TASK}, "auth.forbidden"),
        ("errors/server_error_500", "tasks", {}, "upstream.http_500"),
        ("tasks/detail_not_found", "task", {"task": TASK}, "not_found"),
    ],
)
def test_error_fixtures_classify_through_the_gateway(tmp_path, fixture, resource, ids, code):
    gateway = make_gateway(tmp_path, replay_for(fixture))
    with pytest.raises(ZohoAxiError) as excinfo:
        gateway.get(resource, **ids, **IDS)
    assert excinfo.value.code == code


# --- the other fixtures read back sensibly -------------------------------------------------------


def test_envelope_variants_all_normalize_to_lists(tmp_path):
    for fixture, resource, ids, expected in [
        ("portals/list", "portals", {}, 1),  # bare array
        ("portals/list_v3_1_shape", "portals", {}, 1),  # wrapped
        ("projects/list", "projects", {"portal": PORTAL}, 2),  # bare array
        ("projects/list_wrapped", "projects", {"portal": PORTAL}, 2),  # wrapped with page_info
        ("users/list", "users", {"portal": PORTAL}, None),
        ("users/list_v3_1_shape", "users", {"portal": PORTAL}, None),
        ("tags/list", "tags", {"portal": PORTAL}, None),
        ("tasklists/list", "tasklists", IDS, None),
    ]:
        records = make_gateway(tmp_path, replay_for(fixture)).get(resource, **ids)
        assert len(records) >= 1, fixture
        if expected is not None:
            assert len(records) == expected, fixture


def test_single_entity_fixtures_normalize_to_one_record(tmp_path):
    gateway = make_gateway(tmp_path, replay_for("projects/detail", "bugs/detail_with_task", "tasklists/detail"))
    assert gateway.get("project", project=PROJECT, portal=PORTAL)["id"] == PROJECT
    assert gateway.get("bug", bug=BUG, **IDS)["associated_task"]["id"] == TASK


def test_the_task_page_has_24_rows_and_says_more_exist(tmp_path):
    tasks = make_gateway(tmp_path, replay_for("tasks/list")).get("tasks", **IDS)
    assert len(tasks) == 24 and tasks.has_more is True
    assert tasks[1]["id"] == TASK and tasks[4]["id"] == TASK_PARENT  # DM1-T2 and DM1-T5 keep their canonical ids


# --- harness end to end: fixture -> gateway -> render -> token budget + golden --------------------

TASKS_LIST_BUDGET = 500  # PRD initial target: a 20-row tasks list


def _task_rows(tasks):
    return [
        {"key": t["prefix"], "title": t["name"], "status": t["status"]["name"],
         "owner": t["owners_and_work"]["owners"][0]["name"]}
        for t in tasks
    ]


def test_a_rendered_20_row_list_built_from_the_fixture_stays_inside_its_budget(tmp_path):
    tasks = make_gateway(tmp_path, replay_for("tasks/list")).get("tasks", **IDS)
    result = Result(
        entity="task", payload=_task_rows(tasks)[:20], counts={"shown": 20, "on_page": len(tasks)},
        truncations=[Truncation("tasks", omitted=4, expand={"limit": 24})],
    )
    text = render(result, mode="cli", full=False).text
    assert_token_budget(text, TASKS_LIST_BUDGET, label="tasks 20-row list (harness smoke)")
    assert_golden("harness/tasks_20_rows", text)


def test_the_budget_helper_fails_on_a_deliberately_fat_rendering(tmp_path):
    tasks = make_gateway(tmp_path, replay_for("tasks/list")).get("tasks", **IDS)
    text = render(Result(entity="task", payload=_task_rows(tasks)), mode="cli", full=False).text
    with pytest.raises(AssertionError, match=r"measured \d+ > allowed 100"):
        assert_token_budget(text, 100, label="too small on purpose")
    from support import tokens

    tokens.MEASUREMENTS.pop()  # keep the intentional failure out of the run summary
