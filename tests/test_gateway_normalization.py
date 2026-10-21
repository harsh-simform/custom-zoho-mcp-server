"""Field-name normalization at the gateway edge: commands never branch on Zoho's casing variants.
Record shapes are invented-but-plausible; the spellings come from the legacy `_first` helper."""
from support.factories import API, Clock, make_settings
from support.replay import ReplayTransport

from zoho_axi.core.gateway import ZohoGateway

IDS = {"portal": "11", "project": "22", "task": "33"}
URL = f"{API}/portal/11/projects/22/tasks/33/attachments"


def attachments(tmp_path, records):
    replay = ReplayTransport().get(URL, json={"attachments": records})
    gateway = ZohoGateway(make_settings(tmp_path), transport=replay, token_provider=lambda: "t", clock=Clock())
    return gateway.get("task_attachments", **IDS)


def test_shouting_and_camel_case_spellings_collapse_to_one_canonical_form(tmp_path):
    [shouted, camel, plain] = attachments(tmp_path, [
        {"FILENAME": "a.png", "CONTENT_TYPE": "image/png", "DOWNLOAD_URL": "https://projects.zoho.com/f/1"},
        {"fileName": "b.png", "contentType": "image/png", "downloadUrl": "https://projects.zoho.com/f/2"},
        {"file_name": "c.png", "content_type": "image/png", "download_url": "https://projects.zoho.com/f/3"},
    ])

    assert shouted == {"file_name": "a.png", "content_type": "image/png", "download_url": "https://projects.zoho.com/f/1"}
    assert camel == {"file_name": "b.png", "content_type": "image/png", "download_url": "https://projects.zoho.com/f/2"}
    assert plain == {"file_name": "c.png", "content_type": "image/png", "download_url": "https://projects.zoho.com/f/3"}


def test_looser_synonyms_fill_in_when_the_canonical_name_is_missing_or_empty(tmp_path):
    [record] = attachments(tmp_path, [
        {"file_name": "", "name": "shot.jpg", "mime_type": "image/jpeg", "link": "https://projects.zoho.com/f/9"},
    ])
    assert record == {"file_name": "shot.jpg", "content_type": "image/jpeg", "download_url": "https://projects.zoho.com/f/9"}


def test_the_canonical_spelling_wins_over_a_synonym_when_both_are_set(tmp_path):
    [record] = attachments(tmp_path, [{"file_name": "real.png", "FILENAME": "other.png", "name": "label"}])
    assert record["file_name"] == "real.png"


def test_unrelated_fields_pass_through_and_numeric_ids_become_strings(tmp_path):
    [record] = attachments(tmp_path, [{"id": 4455, "size": 2048, "FILENAME": "a.txt"}])
    assert record == {"id": "4455", "size": 2048, "file_name": "a.txt"}


def test_attachment_lists_may_be_wrapped_under_any_known_key_or_bare(tmp_path):
    for body in ({"attachment": [{"FILENAME": "x"}]}, {"data": [{"FILENAME": "x"}]}, [{"FILENAME": "x"}]):
        replay = ReplayTransport().get(URL, json=body)
        gateway = ZohoGateway(make_settings(tmp_path), transport=replay, token_provider=lambda: "t")
        assert gateway.get("task_attachments", **IDS) == [{"file_name": "x"}]
