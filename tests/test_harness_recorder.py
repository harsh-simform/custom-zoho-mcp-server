"""Seam: the fixture recorder `scripts/record_fixture.py` (`main(argv, transport=..., settings=...)`)
and its `RecordingTransport`. The "network" is the existing replay adapter, fed with raw, UNSANITIZED
Zoho-shaped responses; the assertions are about what ends up on disk and what was sent. The script is
never run live here (no credentials, no network).
"""
import importlib.util
import io
import json
from pathlib import Path

import pytest
from support.factories import API, LEGACY, TOKEN_URL, make_settings
from support.fixtures import BUG, IDS, PORTAL, PROJECT, TASK, load_fixture
from support.recording import RecordingTransport
from support.replay import ReplayTransport

from zoho_axi.core.gateway import ZohoGateway

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "record_fixture.py"

REAL_PORTAL, REAL_PROJECT, REAL_TASK = "8123456789012", "4000000000001", "4000000007005"
REAL_EMAIL = "monica.hemsworth@acme-corp.com"
FAKE_ACCESS_TOKEN = "1000." + "a1" * 16 + "." + "b2" * 16
FAKE_REFRESH_TOKEN = "1000." + "c3" * 16 + "." + "d4" * 16
BASE = f"portal/{REAL_PORTAL}/projects/{REAL_PROJECT}"


def load_script():
    spec = importlib.util.spec_from_file_location("record_fixture_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def zoho_network():
    """What the real Zoho would answer, raw: the token refresh, a v3 comments 404, then the legacy 200."""
    net = ReplayTransport()
    net.add("POST", TOKEN_URL, json={"access_token": FAKE_ACCESS_TOKEN, "expires_in": 3600})
    net.get(f"{API}/{BASE}/tasks/{REAL_TASK}/comments", status=404,
            json={"error": {"status_code": "404", "title": "RESOURCE_NOT_FOUND"}})
    net.get(f"{LEGACY}/{BASE}/tasks/{REAL_TASK}/comments/", json={"comments": [{
        "id": "4000000040001", "content": f"Ping {REAL_EMAIL} about task {REAL_TASK}",
        "added_by": {"zpuid": "4000000002471", "name": "Monica Hemsworth", "email": REAL_EMAIL},
    }]})
    return net


def record(tmp_path, argv, net=None):
    script = load_script()
    out, err = io.StringIO(), io.StringIO()
    net = net if net is not None else zoho_network()
    code = script.main(
        [*argv, "--fixtures-dir", str(tmp_path / "fx")],
        transport=net, settings=make_settings(tmp_path), stdout=out, stderr=err,
        gateway_options={"sleep": lambda s: None},
    )
    return code, out.getvalue(), err.getvalue(), net


COMMENTS_CALL = ["task_comments/v3_404_legacy_ok", "--call",
                 f"task_comments portal={REAL_PORTAL} project={REAL_PROJECT} task={REAL_TASK}"]


def test_recorded_fallback_scenario_is_written_sanitized_with_canonical_ids(tmp_path):
    code, out, err, _ = record(tmp_path, COMMENTS_CALL)
    assert code == 0, err
    path = tmp_path / "fx" / "task_comments" / "v3_404_legacy_ok.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    first, second = document["entries"]

    assert (first["method"], first["status"]) == ("GET", 404)
    assert first["url"] == f"{API}/portal/{PORTAL}/projects/{PROJECT}/tasks/{TASK}/comments"
    assert (second["method"], second["status"]) == ("GET", 200)
    assert second["url"] == f"{LEGACY}/portal/{PORTAL}/projects/{PROJECT}/tasks/{TASK}/comments/"
    text = path.read_text(encoding="utf-8")
    for real in (REAL_PORTAL, REAL_PROJECT, REAL_TASK, REAL_EMAIL, "Monica", "Hemsworth", "acme-corp"):
        assert real not in text, real
    assert document["_provenance"]["groups"] == {"all": "recorded: live Zoho response, sanitized"}


def test_the_recorded_file_replays_through_the_gateway_with_the_canonical_ids(tmp_path):
    record(tmp_path, COMMENTS_CALL)
    document = json.loads((tmp_path / "fx" / "task_comments" / "v3_404_legacy_ok.json").read_text(encoding="utf-8"))
    replay = ReplayTransport.from_entries(document["entries"])
    gateway = ZohoGateway(make_settings(tmp_path), transport=replay, token_provider=lambda: "t", sleep=lambda s: None)

    comments = gateway.get("task_comments", task=TASK, **IDS)
    assert len(comments) == 1
    assert comments[0]["id"] != "4000000040001"


def test_only_get_requests_are_recorded_and_the_token_exchange_never_reaches_the_file_or_output(tmp_path):
    code, out, err, net = record(tmp_path, COMMENTS_CALL)
    assert code == 0
    assert [c.method for c in net.calls if c.method != "GET"] == ["POST"]  # the OAuth refresh, nothing else
    assert all(c.url == TOKEN_URL for c in net.calls if c.method == "POST")
    document = (tmp_path / "fx" / "task_comments" / "v3_404_legacy_ok.json").read_text(encoding="utf-8")
    for stream in (document, out, err):
        assert FAKE_ACCESS_TOKEN not in stream
        assert "refresh-token-value" not in stream and "super-secret-value" not in stream  # make_settings credentials
    assert json.loads(document)["entries"][0]["method"] == "GET"
    assert "POST" not in document


def test_a_credential_in_a_response_refuses_the_write_and_names_only_the_kind(tmp_path):
    net = ReplayTransport()
    net.add("POST", TOKEN_URL, json={"access_token": "tok", "expires_in": 3600})
    net.get(f"{API}/{BASE}/tasks/{REAL_TASK}/comments", json={"comments": [], "debug": FAKE_REFRESH_TOKEN})
    code, out, err, _ = record(tmp_path, COMMENTS_CALL, net)

    assert code == 1
    assert "zoho-token" in err
    assert FAKE_REFRESH_TOKEN not in out + err
    assert not (tmp_path / "fx").exists()


def test_an_existing_fixture_is_not_overwritten_without_force(tmp_path):
    record(tmp_path, COMMENTS_CALL)
    target = tmp_path / "fx" / "task_comments" / "v3_404_legacy_ok.json"
    original = target.read_text(encoding="utf-8")
    code, _, err, _ = record(tmp_path, [*COMMENTS_CALL])
    assert code == 1 and "--force" in err
    assert target.read_text(encoding="utf-8") == original

    code, *_ = record(tmp_path, [*COMMENTS_CALL, "--force"])
    assert code == 0


def test_dry_run_prints_the_sanitized_document_and_writes_nothing(tmp_path):
    code, out, err, _ = record(tmp_path, [*COMMENTS_CALL, "--dry-run"])
    assert code == 0, err
    assert not (tmp_path / "fx").exists()
    printed = json.loads(out)
    assert printed["entries"][1]["status"] == 200
    assert REAL_EMAIL not in out


def test_a_failing_call_is_still_recorded_so_error_fixtures_can_be_made(tmp_path):
    net = ReplayTransport()
    net.add("POST", TOKEN_URL, json={"access_token": "tok", "expires_in": 3600})
    net.get(f"{API}/{BASE}/tasks/{REAL_TASK}", status=404,
            json={"error": {"status_code": "404", "title": "RESOURCE_NOT_FOUND", "details": [{"message": "Resource Not Found"}]}})
    argv = ["errors/task_not_found", "--call", f"task portal={REAL_PORTAL} project={REAL_PROJECT} task={REAL_TASK}"]
    code, out, err, _ = record(tmp_path, argv, net)

    assert code == 0
    assert "not_found" in err  # reported on stderr, never fatal for the recording
    entry = json.loads((tmp_path / "fx" / "errors" / "task_not_found.json").read_text(encoding="utf-8"))["entries"][0]
    assert entry["status"] == 404 and entry["json"]["error"]["title"] == "RESOURCE_NOT_FOUND"


def test_query_params_are_recorded_and_part_of_the_replay_key(tmp_path):
    net = ReplayTransport()
    net.add("POST", TOKEN_URL, json={"access_token": "tok", "expires_in": 3600})
    net.get(f"{LEGACY}/{BASE}/search", params={"search_term": "RU1-T1", "module": "tasks"},
            json={"tasks": [{"id": REAL_TASK, "key": "RU1-T1", "name": "Prepare release notes"}]})
    argv = ["search/hit", "--call",
            f"search portal={REAL_PORTAL} project={REAL_PROJECT} ?search_term=RU1-T1 ?module=tasks"]
    code, _, err, _ = record(tmp_path, argv, net)

    assert code == 0, err
    entry = json.loads((tmp_path / "fx" / "search" / "hit.json").read_text(encoding="utf-8"))["entries"][0]
    assert entry["params"] == {"search_term": "RU1-T1", "module": "tasks"}
    assert entry["url"] == f"{LEGACY}/portal/{PORTAL}/projects/{PROJECT}/search"
    # No `task=` id was named in this session, so the task id is hashed rather than pinned.
    assert entry["json"]["tasks"][0]["id"] not in ("", REAL_TASK) and len(entry["json"]["tasks"][0]["id"]) == 13


def test_a_download_is_recorded_with_a_stub_body_never_the_real_bytes(tmp_path):
    net = ReplayTransport()
    net.add("POST", TOKEN_URL, json={"access_token": "tok", "expires_in": 3600})
    url = "https://download.zoho.com/v1/workdrive/download/b8vet771b86e7c97147af9b7e1754c19b5118"
    net.get(url, body=b"\x89PNG-REAL-CUSTOMER-SCREENSHOT", content_type="image/png")
    code, _, err, _ = record(tmp_path, ["attachments/image_download", "--download", url], net)

    assert code == 0, err
    text = (tmp_path / "fx" / "attachments" / "image_download.json").read_text(encoding="utf-8")
    entry = json.loads(text)["entries"][0]
    assert entry["content_type"] == "image/png"
    assert entry["url"].startswith("https://download.zoho.com/v1/workdrive/download/")
    assert "b8vet771b86e7c97147af9b7e1754c19b5118" not in entry["url"]
    import base64

    assert base64.b64decode(entry["body_base64"]).startswith(b"\x89PNG\r\n")  # the stub, a real tiny PNG
    assert b"REAL-CUSTOMER" not in base64.b64decode(entry["body_base64"])


def test_portal_names_from_a_portals_response_are_scrubbed_everywhere(tmp_path):
    net = ReplayTransport()
    net.add("POST", TOKEN_URL, json={"access_token": "tok", "expires_in": 3600})
    net.get(f"{API}/portals", json={"portals": [{"id": REAL_PORTAL, "name": "acmeportal", "company_name": "Acme Corp Ltd",
                                                 "url": "https://projects.zoho.com/portal/acmeportal"}]})
    code, _, err, _ = record(tmp_path, ["portals/list", "--call", "portals"], net)
    assert code == 0, err
    text = (tmp_path / "fx" / "portals" / "list.json").read_text(encoding="utf-8")
    assert "acmeportal" not in text and "Acme Corp" not in text


@pytest.mark.parametrize(
    "argv",
    [
        ["no_slash_name", "--call", "portals"],
        ["a/b", "--call", "nonsense portal=1"],
        ["a/b"],  # nothing to record
        ["a/b", "--call", "task portal=1"],  # wrong ids for the resource
        ["a/b", "--call", "portals", "--bogus"],
    ],
)
def test_usage_errors_exit_2_and_touch_nothing(tmp_path, argv):
    code, out, err, net = record(tmp_path, argv)
    assert code == 2
    assert not (tmp_path / "fx").exists()
    assert net.calls == []


def test_missing_credentials_are_fatal_not_an_empty_recording(tmp_path):
    from zoho_axi.core.config import load_settings

    script = load_script()
    out, err = io.StringIO(), io.StringIO()
    net = ReplayTransport()
    settings = load_settings(env={"HOME": str(tmp_path)}, legacy_env_file=tmp_path / "none.env")
    code = script.main(["portals/list", "--call", "portals", "--fixtures-dir", str(tmp_path / "fx")],
                       transport=net, settings=settings, stdout=out, stderr=err)
    assert code == 3  # auth/config exit code
    assert "auth.missing" in err.getvalue()
    assert not (tmp_path / "fx").exists() and net.calls == []


def test_a_refused_download_host_is_fatal_and_nothing_is_sent(tmp_path):
    argv = ["attachments/x", "--download", "https://evil.example.test/a.png"]
    code, out, err, net = record(tmp_path, argv)
    assert code == 5 and "download.host_refused" in err
    assert net.calls == []


def test_the_script_only_imports_what_it_needs_to_issue_get_requests():
    source = SCRIPT.read_text(encoding="utf-8")
    assert "import requests" not in source and "from requests" not in source  # HTTP only via the gateway
    assert '"POST"' not in source and "'POST'" not in source


def test_recording_transport_passes_responses_through_unchanged_and_skips_non_get():
    net = ReplayTransport().get("https://x.zoho.com/a", json={"a": 1}).add("POST", "https://accounts.zoho.com/t", json={"access_token": "s"})
    recorder = RecordingTransport(net)
    assert recorder.send("GET", "https://x.zoho.com/a", {}, None, 5).status == 200
    assert recorder.send("POST", "https://accounts.zoho.com/t", {}, None, 5, data={"x": "y"}).status == 200
    assert [e["method"] for e in recorder.entries()] == ["GET"]
    assert recorder.entries()[0]["json"] == {"a": 1}
