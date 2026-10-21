"""Images on request (T12): `zoho-axi task|bug <KEY> --images` and the shared `core/attachments` module.

Seams: the CLI product seam (argv/env in, exit code + stdout out, Zoho replayed below it through the real
gateway and cache) and, for the function the MCP front-end (T13) will call, `inline_images`. Expected
values are literals: a 70-byte PNG, the sanitized names and directory modes the ticket demands.

Attachment record shapes are invented-but-plausible (the v3 task-attachment list has no documented sample; see
tests/fixtures/zoho/task_attachments); download hosts are Zoho hosts so the real allowlist is exercised.
"""
import base64
import io
import json
import os
import stat

import pytest
from support.factories import make_gateway
from support.fixtures import BUG, PORTAL, PROJECT, TASK, first_json, replay_for
from support.replay import ReplayTransport

from zoho_axi.cli.main import main
from zoho_axi.core import attachments

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)
assert len(PNG) == 70  # the size every expectation below relies on

BASE = f"https://projectsapi.zoho.com/api/v3/portal/{PORTAL}/projects/{PROJECT}"
DL = "https://download.zoho.com/v1/workdrive/download"


def rec(name, *, size=70, kind="image/png", url=None, **extra):
    """One v3-style task attachment record (name/type/size/download_url)."""
    out = {"attachment_id": f"id-{name}", "name": name, "type": kind, "size": str(size),
           "download_url": url if url is not None else f"{DL}/{name}"}
    out.update(extra)
    return out


def task_replay(*records, bodies=None, comments=204):
    """Task detail + comments + attachments listing; `bodies` maps attachment name -> bytes served for its URL."""
    replay = replay_for("tasks/detail")
    replay.get(f"{BASE}/tasks/{TASK}/comments", status=comments)
    replay.get(f"{BASE}/tasks/{TASK}/attachments", json={"attachment": list(records)})
    for r in records:
        url = r.get("download_url")
        if url and (bodies is None or r["name"] in bodies):
            replay.get(url, body=(bodies or {}).get(r["name"], PNG), content_type=r.get("type") or "image/png",
                       repeat=True)
    return replay


def run(argv, tmp_path, replay, *, env_extra=None):
    env = {
        "HOME": str(tmp_path),
        "ZOHO_MCP_ENV_FILE": str(tmp_path / "none.env"),
        "ZOHO_AXI_PORTAL_ID": PORTAL, "ZOHO_AXI_PROJECT_ID": PROJECT,
        "ZOHO_AXI_CLIENT_ID": "cid", "ZOHO_AXI_CLIENT_SECRET": "csecret", "ZOHO_AXI_REFRESH_TOKEN": "rtoken",
        **(env_extra or {}),
    }
    gateway = make_gateway(tmp_path, replay)
    out, err = io.StringIO(), io.StringIO()
    code = main(argv, env, out, err, gateway=gateway)
    return code, out.getvalue().rstrip("\n"), err.getvalue(), gateway


def attach_dir(tmp_path, kind="task", ident=TASK):
    return tmp_path / ".cache" / "zoho-axi" / "attachments" / f"{kind}-{ident}"


def download_calls(replay):
    return [c for c in replay.calls if c.url.startswith(DL)]


# --- the happy path ----------------------------------------------------------------------------------

def test_images_flag_downloads_the_image_and_prints_name_bytes_path(tmp_path):
    replay = task_replay(rec("shot.png"), rec("notes.pdf", kind="application/pdf"))
    code, out, _, _ = run(["task", TASK, "--images"], tmp_path, replay)
    assert code == 0
    path = attach_dir(tmp_path) / "shot.png"
    assert path.read_bytes() == PNG
    assert f"images[1]{{name,bytes,path}}:\n  shot.png,70,{path}" in out
    assert not (attach_dir(tmp_path) / "notes.pdf").exists()  # a non-image is never fetched
    assert [c.url for c in download_calls(replay)] == [f"{DL}/shot.png"]


def test_a_default_run_downloads_nothing_and_fetches_no_image_bytes(tmp_path):
    replay = task_replay(rec("shot.png"))
    code, out, _, _ = run(["task", TASK, "--attachments"], tmp_path, replay)
    assert code == 0 and "images[" not in out
    assert download_calls(replay) == []
    assert not (tmp_path / ".cache" / "zoho-axi" / "attachments").exists()


def test_a_plain_task_run_downloads_nothing_either(tmp_path):
    replay = task_replay(rec("shot.png"))
    code, out, _, gateway = run(["task", TASK], tmp_path, replay)
    assert code == 0 and "images[" not in out
    assert download_calls(replay) == []
    assert gateway.request_count == 3  # task, comments, attachments: the unchanged default budget


def test_images_flag_with_no_image_attachments_says_so(tmp_path):
    replay = task_replay(rec("notes.pdf", kind="application/pdf"))
    code, out, _, _ = run(["task", TASK, "--images"], tmp_path, replay)
    assert code == 0
    assert "images: 0 results" in out
    assert download_calls(replay) == []


def test_the_images_flag_costs_one_request_per_image_on_top_of_the_default_three(tmp_path):
    replay = task_replay(rec("a.png"), rec("b.png"))
    _, _, _, gateway = run(["task", TASK, "--images"], tmp_path, replay)
    assert gateway.request_count == 5


# --- filenames never leave the attachments directory --------------------------------------------------

@pytest.mark.parametrize("hostile, saved", [
    ("../../x.png", "x.png"),
    ("..\\..\\x.png", "x.png"),
    ("/etc/cron.d/x.png", "x.png"),
    ("a/b/../../../x.png", "x.png"),
    (".hidden.png", "hidden.png"),
    ("..png", "png"),
    ("shot\x00.png", "shot_.png"),
])
def test_a_hostile_filename_is_written_inside_the_item_directory_under_a_safe_name(tmp_path, hostile, saved):
    replay = task_replay(rec(hostile, url=f"{DL}/h"))
    code, out, _, _ = run(["task", TASK, "--images"], tmp_path, replay)
    assert code == 0
    root = attach_dir(tmp_path)
    assert sorted(p.name for p in root.iterdir()) == [saved]
    assert (root / saved).read_bytes() == PNG
    # nothing was created anywhere else under tmp_path outside the cache tree
    outside = [p for p in tmp_path.rglob("*") if p.is_file() and p.name.endswith(".png") and root not in p.parents]
    assert outside == []
    assert not (tmp_path.parent / "x.png").exists()


def test_a_name_that_sanitizes_to_nothing_gets_a_generated_one(tmp_path):
    replay = task_replay(rec("..", kind="image/png", url=f"{DL}/h"))
    code, _, _, _ = run(["task", TASK, "--images"], tmp_path, replay)
    assert code == 0
    assert [p.name for p in attach_dir(tmp_path).iterdir()] == ["image-1"]


def test_two_attachments_with_the_same_name_do_not_overwrite_each_other(tmp_path):
    replay = task_replay(rec("shot.png", url=f"{DL}/one"), rec("shot.png", url=f"{DL}/two"),
                         rec("SHOT.png", url=f"{DL}/three"))
    code, _, _, _ = run(["task", TASK, "--images"], tmp_path, replay)
    assert code == 0
    assert sorted(p.name for p in attach_dir(tmp_path).iterdir()) == ["SHOT-3.png", "shot-2.png", "shot.png"]


def test_directories_are_private_and_files_owner_only(tmp_path):
    replay = task_replay(rec("shot.png"))
    run(["task", TASK, "--images"], tmp_path, replay)
    root = attach_dir(tmp_path)
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE(root.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE((root / "shot.png").stat().st_mode) == 0o600


def test_xdg_cache_home_decides_where_files_go(tmp_path):
    xdg = tmp_path / "xdg"
    replay = task_replay(rec("shot.png"))
    code, out, _, _ = run(["task", TASK, "--images"], tmp_path, replay, env_extra={"XDG_CACHE_HOME": str(xdg)})
    assert code == 0
    assert (xdg / "zoho-axi" / "attachments" / f"task-{TASK}" / "shot.png").read_bytes() == PNG
    assert str(xdg / "zoho-axi" / "attachments") in out
    assert not (tmp_path / ".cache" / "zoho-axi" / "attachments").exists()


# --- safety: hosts, caps, counts ------------------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "https://host-c0e47f80.example.test/steal-the-token.png",
    "http://projects.zoho.com/plain.png",
    "https://projects.zoho.com.evil.example.test/x.png",
    "/relative/x.png",
])
def test_a_disallowed_download_host_is_refused_with_a_warning_and_the_token_is_never_sent(tmp_path, url):
    replay = task_replay(rec("bad.png", url=url), rec("good.png"))
    code, out, _, _ = run(["task", TASK, "--images"], tmp_path, replay)
    assert code == 0
    assert [c.url for c in replay.calls if not c.url.startswith(BASE)] == [f"{DL}/good.png"]  # only the good host
    assert "tok-abc" not in out
    assert "bad.png" in out and "not an https Zoho host" in out
    assert (attach_dir(tmp_path) / "good.png").exists() and not (attach_dir(tmp_path) / "bad.png").exists()
    assert "images[1]" in out


def test_a_record_without_a_download_url_is_a_warning_not_a_crash(tmp_path):
    replay = task_replay(rec("nolink.png", url=""), rec("good.png"))
    code, out, _, _ = run(["task", TASK, "--images"], tmp_path, replay)
    assert code == 0
    assert "nolink.png: no download URL" in out
    assert "images[1]" in out


def test_an_image_whose_listed_size_is_over_the_cap_is_skipped_without_a_download(tmp_path):
    replay = task_replay(rec("huge.png", size=500), rec("small.png"))
    code, out, _, _ = run(["task", TASK, "--images"], tmp_path, replay,
                          env_extra={"ZOHO_AXI_MAX_ATTACHMENT_BYTES": "100"})
    assert code == 0
    assert "huge.png: 500 B is over the 100 B limit" in out
    assert f"{DL}/huge.png" not in [c.url for c in replay.calls]
    assert (attach_dir(tmp_path) / "small.png").exists()


def test_an_image_that_turns_out_bigger_than_listed_is_stopped_by_the_cap_and_reported(tmp_path):
    liar = rec("liar.png", size=10)  # claims 10 bytes, serves 70
    replay = task_replay(liar)
    code, out, _, _ = run(["task", TASK, "--images"], tmp_path, replay,
                          env_extra={"ZOHO_AXI_MAX_ATTACHMENT_BYTES": "50"})
    assert code == 0
    assert "liar.png" in out and "50-byte cap" in out
    assert not attach_dir(tmp_path).exists() or list(attach_dir(tmp_path).iterdir()) == []


def test_images_over_the_count_cap_are_reported_and_not_downloaded(tmp_path):
    replay = task_replay(rec("a.png"), rec("b.png"), rec("c.png"), rec("d.png"))
    code, out, _, _ = run(["task", TASK, "--images"], tmp_path, replay, env_extra={"ZOHO_AXI_MAX_IMAGES": "2"})
    assert code == 0
    assert sorted(p.name for p in attach_dir(tmp_path).iterdir()) == ["a.png", "b.png"]
    assert "images[2]" in out
    assert "2 more images not downloaded (max_images is 2): c.png, d.png" in out
    assert [c.url for c in download_calls(replay)] == [f"{DL}/a.png", f"{DL}/b.png"]


def test_a_failed_download_is_a_warning_and_the_others_still_arrive(tmp_path):
    broken = rec("broken.png", url=f"{DL}/broken")
    replay = task_replay(broken, rec("good.png"), bodies={"good.png": PNG})  # no 200 for the broken one
    replay.get(f"{DL}/broken", status=500)
    code, out, _, _ = run(["task", TASK, "--images"], tmp_path, replay)
    assert code == 0
    assert "broken.png" in out and "HTTP 500" in out
    assert "images[1]" in out


# --- idempotence ------------------------------------------------------------------------------------------

def test_a_rerun_reuses_files_of_the_listed_size_and_downloads_nothing(tmp_path):
    _, first, _, _ = run(["task", TASK, "--images"], tmp_path, task_replay(rec("shot.png")))
    again = task_replay(rec("shot.png"))
    code, second, _, _ = run(["task", TASK, "--images"], tmp_path, again)
    assert code == 0
    assert download_calls(again) == []
    assert second == first
    assert (attach_dir(tmp_path) / "shot.png").read_bytes() == PNG


def test_fresh_downloads_again(tmp_path):
    run(["task", TASK, "--images"], tmp_path, task_replay(rec("shot.png")))
    again = task_replay(rec("shot.png"))
    code, _, _, _ = run(["task", TASK, "--images", "--fresh"], tmp_path, again)
    assert code == 0
    assert [c.url for c in download_calls(again)] == [f"{DL}/shot.png"]


def test_an_existing_file_of_another_size_is_replaced(tmp_path):
    root = attach_dir(tmp_path)
    root.mkdir(parents=True)
    (root / "shot.png").write_bytes(b"stale")
    code, _, _, _ = run(["task", TASK, "--images"], tmp_path, task_replay(rec("shot.png")))
    assert code == 0
    assert (root / "shot.png").read_bytes() == PNG
    assert sorted(os.listdir(root)) == ["shot.png"]  # no temp files left behind


# --- record shapes --------------------------------------------------------------------------------------

def test_legacy_upper_case_records_are_found_by_extension_and_downloaded(tmp_path):
    legacy_url = "https://projects.zoho.com/portal/p/downloadfile?id=9"
    replay = replay_for("tasks/detail")
    replay.get(f"{BASE}/tasks/{TASK}/comments", status=204)
    replay.get(f"{BASE}/tasks/{TASK}/attachments", status=404, json={"error": {"title": "RESOURCE_NOT_FOUND"}})
    replay.get(f"https://projectsapi.zoho.com/restapi/portal/{PORTAL}/projects/{PROJECT}/tasks/{TASK}/attachments/",
               json=[{"FILENAME": "screenshot-2.JPG", "DOWNLOAD_URL": legacy_url,
                      "CONTENT_TYPE": "application/octet-stream"},
                     {"FILENAME": "a.pdf", "DOWNLOAD_URL": legacy_url, "CONTENT_TYPE": "application/pdf"}])
    replay.get(legacy_url, body=PNG, content_type="application/octet-stream")
    code, out, _, _ = run(["task", TASK, "--images"], tmp_path, replay)
    assert code == 0
    assert (attach_dir(tmp_path) / "screenshot-2.JPG").read_bytes() == PNG
    assert "images[1]{name,bytes,path}:" in out


def test_bug_images_read_the_nested_file_info_shape(tmp_path):
    replay = replay_for("bugs/detail_with_task", "bug_comments/v3_ok", "bug_attachments/v3_nested_file_info")
    replay.get("https://download.zoho.com/v1/workdrive/download/fakebug7", body=PNG, content_type="image/png")
    code, out, _, _ = run(["bug", BUG, "--images"], tmp_path, replay)
    assert code == 0
    path = attach_dir(tmp_path, "bug", BUG) / "checkout-500.png"
    assert path.read_bytes() == PNG
    assert f"checkout-500.png,70,{path}" in out
    assert not any("fakebug8" in c.url for c in replay.calls)  # the .java is not an image


def test_json_output_carries_the_same_rows(tmp_path):
    code, out, _, _ = run(["task", TASK, "--images", "--json"], tmp_path, task_replay(rec("shot.png")))
    assert code == 0
    assert json.loads(out)["images"] == [
        {"name": "shot.png", "bytes": 70, "path": str(attach_dir(tmp_path) / "shot.png")}]


def test_the_attachments_slice_still_lists_name_type_size_without_urls(tmp_path):
    code, out, _, _ = run(["task", TASK, "--attachments"], tmp_path, task_replay(rec("shot.png")))
    assert code == 0 and "download" not in out and "http" not in out


# --- the function the MCP front-end calls (T13) ------------------------------------------------------------

def _gateway(tmp_path, *records):
    replay = ReplayTransport()
    for r in records:
        replay.get(r["download_url"], body=PNG, content_type="image/png", repeat=True)
    return make_gateway(tmp_path, replay), replay


def test_inline_images_returns_bytes_and_content_type_under_the_same_caps(tmp_path):
    records = [rec("a.png"), rec("doc.pdf", kind="application/pdf"), rec("b.png"), rec("c.png")]
    gateway, replay = _gateway(tmp_path, *records)
    batch = attachments.inline_images(gateway.download, records, max_images=2, max_bytes=1000)
    assert [(i.name, i.data, i.content_type, i.size, i.path) for i in batch.images] == [
        ("a.png", PNG, "image/png", 70, None), ("b.png", PNG, "image/png", 70, None)]
    assert [s.reason for s in batch.skipped] == ["over_count"]
    assert batch.warnings() == ["1 more image not downloaded (max_images is 2): c.png"]
    assert len(replay.calls) == 2


def test_inline_images_takes_the_content_type_from_the_record_when_zoho_says_octet_stream(tmp_path):
    records = [rec("a.jpg", kind="image/jpeg")]
    replay = ReplayTransport().get(records[0]["download_url"], body=PNG, content_type="application/octet-stream")
    batch = attachments.inline_images(make_gateway(tmp_path, replay).download, records, max_images=5, max_bytes=1000)
    assert batch.images[0].content_type == "image/jpeg"


def test_inline_images_refuses_a_foreign_host_without_calling_it(tmp_path):
    records = [rec("a.png", url="https://evil.example.test/a.png")]
    gateway, replay = _gateway(tmp_path)
    batch = attachments.inline_images(gateway.download, records, max_images=5, max_bytes=1000)
    assert batch.images == [] and replay.calls == []
    assert [s.reason for s in batch.skipped] == ["failed"]
    assert "not an https Zoho host" in batch.warnings()[0]
