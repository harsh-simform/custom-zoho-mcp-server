"""The gateway's read seam (S1): `get(resource, **ids, params=...)` -> normalized entity, with the
network replaced by the fixture-replay transport at S2. Response bodies below are invented but
plausible Zoho shapes (the recorded fixtures arrive with T04)."""
import pytest
from support.factories import API, LEGACY, Clock, make_settings
from support.replay import ReplayTransport

from zoho_projects_axi.core.errors import AuthError, NotFoundError, UpstreamError
from zoho_projects_axi.core.gateway import ZohoGateway

IDS = {"portal": "11", "project": "22"}


def make_gateway(tmp_path, replay, **kwargs):
    kwargs.setdefault("token_provider", lambda: "tok-abc")
    kwargs.setdefault("sleep", lambda seconds: None)
    kwargs.setdefault("clock", Clock())
    return ZohoGateway(make_settings(tmp_path), transport=replay, **kwargs)


def test_get_single_entity_hits_the_v3_path_with_the_oauth_header(tmp_path):
    replay = ReplayTransport().get(f"{API}/portal/11/projects/22/tasks/33", json={"id": "33", "name": "Fix login"})
    task = make_gateway(tmp_path, replay).get("task", task="33", **IDS)

    assert task == {"id": "33", "name": "Fix login"}
    assert replay.calls[0].method == "GET"
    assert replay.calls[0].headers == {"Authorization": "Zoho-oauthtoken tok-abc"}


def test_get_collection_unwraps_the_list_and_reports_whether_more_pages_exist(tmp_path):
    replay = ReplayTransport().get(
        f"{API}/portal/11/projects/22/tasks",
        params={"range": "10"},
        json={"tasks": [{"id": 1}, {"id": 2}], "page_info": {"has_next_page": True}},  # page_info shape: assumed
    )
    tasks = make_gateway(tmp_path, replay).get("tasks", params={"range": "10"}, **IDS)

    assert [t["id"] for t in tasks] == ["1", "2"]
    assert tasks.has_more is True


def test_a_bare_list_body_is_also_a_collection(tmp_path):
    replay = ReplayTransport().get(f"{API}/portal/11/users", json=[{"id": "5", "name": "Asha"}])
    users = make_gateway(tmp_path, replay).get("users", portal="11")
    assert users == [{"id": "5", "name": "Asha"}]
    assert users.has_more is None


def test_a_single_entity_wrapped_in_a_one_element_list_is_unwrapped(tmp_path):
    replay = ReplayTransport().get(f"{API}/portal/11/projects/22/bugs/9", json={"bugs": [{"id": "9", "title": "Crash"}]})
    assert make_gateway(tmp_path, replay).get("bug", bug="9", **IDS) == {"id": "9", "title": "Crash"}


def test_empty_204_body_means_an_empty_collection_or_entity(tmp_path):
    replay = ReplayTransport()
    replay.get(f"{API}/portal/11/projects/22/tasks/33/comments", status=204)
    replay.get(f"{API}/portal/11/projects/22/tasks/34", status=204)
    gateway = make_gateway(tmp_path, replay)
    assert gateway.get("task_comments", task="33", **IDS) == []
    assert gateway.get("task", task="34", **IDS) == {}


def test_ids_are_percent_encoded_so_they_cannot_alter_the_path(tmp_path):
    replay = ReplayTransport().get(f"{API}/portal/11/projects/22/tasks/..%2F..%2Fportals", json={})
    make_gateway(tmp_path, replay).get("task", task="../../portals", **IDS)
    assert replay.calls[0].url.endswith("/tasks/..%2F..%2Fportals")


def test_the_data_center_selects_the_host(tmp_path):
    replay = ReplayTransport().get("https://projectsapi.zoho.eu/api/v3/portals", json={"portals": []})
    gateway = ZohoGateway(make_settings(tmp_path, ZOHO_PROJECTS_AXI_DC="eu"), transport=replay, token_provider=lambda: "t")
    assert gateway.get("portals") == []


def test_unknown_resources_and_wrong_ids_are_programmer_errors(tmp_path):
    gateway = make_gateway(tmp_path, ReplayTransport())
    with pytest.raises(ValueError, match="unknown resource"):
        gateway.get("forums", **IDS)
    with pytest.raises(ValueError, match="task"):
        gateway.get("task", **IDS)  # missing the task id
    with pytest.raises(ValueError, match="surprise"):
        gateway.get("task", task="1", surprise="x", **IDS)


# --- v3 -> legacy fallback and error classification ---

def test_v3_404_falls_back_to_the_legacy_path_for_resources_that_have_one(tmp_path):
    replay = ReplayTransport()
    replay.get(f"{API}/portal/11/projects/22/milestones", status=404)
    replay.get(f"{LEGACY}/portal/11/projects/22/milestones/", json={"milestones": [{"id": 7, "name": "Beta"}]})
    milestones = make_gateway(tmp_path, replay).get("milestones", **IDS)

    assert milestones == [{"id": "7", "name": "Beta"}]
    assert [c.url for c in replay.calls] == [
        f"{API}/portal/11/projects/22/milestones",
        f"{LEGACY}/portal/11/projects/22/milestones/",
    ]


def test_v3_failures_other_than_404_do_not_fall_back(tmp_path):
    for status in (400, 500, 503):
        replay = ReplayTransport().get(f"{API}/portal/11/projects/22/milestones", status=status)
        with pytest.raises(UpstreamError) as exc:
            make_gateway(tmp_path, replay).get("milestones", **IDS)
        assert exc.value.exit_code == 5
        assert str(status) in exc.value.message
        assert len(replay.calls) == 1


def test_404_on_a_resource_without_a_legacy_path_is_not_found_with_one_call(tmp_path):
    replay = ReplayTransport().get(f"{API}/portal/11/projects/22/tasks/99", status=404)
    with pytest.raises(NotFoundError) as exc:
        make_gateway(tmp_path, replay).get("task", task="99", **IDS)
    assert exc.value.exit_code == 4
    assert exc.value.code == "not_found"
    assert len(replay.calls) == 1


def test_404_on_both_paths_is_not_found(tmp_path):
    replay = ReplayTransport()
    replay.get(f"{API}/portal/11/projects/22/tasks/5/comments", status=404)
    replay.get(f"{LEGACY}/portal/11/projects/22/tasks/5/comments/", status=404)
    with pytest.raises(NotFoundError):
        make_gateway(tmp_path, replay).get("task_comments", task="5", **IDS)
    assert len(replay.calls) == 2


def test_search_only_exists_on_the_legacy_path(tmp_path):
    replay = ReplayTransport().get(
        f"{LEGACY}/portal/11/projects/22/search",
        params={"search_term": "PROJ1-T1000", "module": "tasks"},
        json={"tasks": [{"id": 33, "key": "PROJ1-T1000"}]},
    )
    found = make_gateway(tmp_path, replay).get("search", params={"search_term": "PROJ1-T1000", "module": "tasks"}, **IDS)
    assert found == [{"id": "33", "key": "PROJ1-T1000"}]


def test_401_and_403_are_auth_errors_exit_3(tmp_path):
    for status, code in ((401, "auth.unauthorized"), (403, "auth.forbidden")):
        replay = ReplayTransport().get(f"{API}/portals", status=status)
        with pytest.raises(AuthError) as exc:
            make_gateway(tmp_path, replay).get("portals")
        assert (exc.value.code, exc.value.exit_code) == (code, 3)


def test_a_non_json_success_body_is_an_upstream_error(tmp_path):
    replay = ReplayTransport().get(f"{API}/portals", body=b"<html>gateway down</html>")
    with pytest.raises(UpstreamError) as exc:
        make_gateway(tmp_path, replay).get("portals")
    assert exc.value.code == "upstream.bad_response"


def test_a_connection_failure_is_upstream_unreachable_and_leaks_nothing(tmp_path):
    replay = ReplayTransport().get(f"{API}/portals", error=ConnectionError("dns for tok-abc failed"))
    with pytest.raises(UpstreamError) as exc:
        make_gateway(tmp_path, replay).get("portals")
    assert exc.value.code == "upstream.unreachable"
    assert "tok-abc" not in exc.value.message + (exc.value.hint or "")
