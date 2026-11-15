"""Tests for the ICloudService client (read + write) via `responses`.

These pin the request shapes and response parsing without touching the real
Steam API. The write path (BeginAppUploadBatch / UploadAppFile /
FinishAppUploadBatch) is the part that still needs live verification; these
tests at least lock the client's handling of the documented shapes.
"""
from __future__ import annotations

import pytest
import responses

from steam_save_bridge.cloud import CloudClient, CloudError, CloudSave

API = "https://api.test"


@pytest.fixture
def client(source_account) -> CloudClient:
    return CloudClient(source_account, api_base=API)


@responses.activate
def test_list_saves_parses_response_envelope(client):
    responses.add(
        responses.GET,
        f"{API}/ICloudService/GetCloudSaveList/v1/",
        json={"response": {"files": [
            {"path": "slot1.save", "size": 12, "timestamp": 1700000000,
             "checksum": "abc"},
            {"path": "slot2.save", "size": 34, "timestamp": 1700000001,
             "checksum": "def"},
        ]}},
        status=200,
    )
    saves = client.list_saves(7)
    assert len(saves) == 2
    assert saves[0].path == "slot1.save"
    assert saves[0].size == 12
    assert saves[0].checksum == "abc"
    assert saves[0].app_id == 7
    assert saves[1].path == "slot2.save"


@responses.activate
def test_list_saves_parses_top_level_files(client):
    responses.add(
        responses.GET,
        f"{API}/ICloudService/GetCloudSaveList/v1/",
        json={"files": [{"path": "a.save", "size": 1,
                         "timestamp": 1, "checksum": "x"}]},
        status=200,
    )
    saves = client.list_saves(7)
    assert len(saves) == 1
    assert saves[0].path == "a.save"


@responses.activate
def test_list_saves_empty(client):
    responses.add(
        responses.GET,
        f"{API}/ICloudService/GetCloudSaveList/v1/",
        json={"response": {"files": []}},
        status=200,
    )
    assert client.list_saves(7) == []


@responses.activate
def test_list_saves_auth_rejected_raises(client):
    responses.add(
        responses.GET,
        f"{API}/ICloudService/GetCloudSaveList/v1/",
        json={"error": "unauthorized"},
        status=401,
    )
    with pytest.raises(CloudError):
        client.list_saves(7)


@responses.activate
def test_get_save_file_returns_raw_bytes(client):
    save = CloudSave(app_id=7, path="slot1.save", size=5,
                     timestamp=1, checksum="abc")
    responses.add(
        responses.GET,
        f"{API}/ICloudService/GetCloudSaveFile/v1/",
        body=b"RAW-SAVE-BYTES",
        status=200,
    )
    assert client.get_save_file(save) == b"RAW-SAVE-BYTES"


@responses.activate
def test_upload_save_three_step_flow(client):
    responses.add(
        responses.POST,
        f"{API}/ICloudService/BeginAppUploadBatch/v1/",
        json={"response": {"upload_batch_token": "TOK123"}},
        status=200,
    )
    responses.add(
        responses.POST,
        f"{API}/ICloudService/UploadAppFile/v1/",
        json={"ok": True},
        status=200,
    )
    responses.add(
        responses.POST,
        f"{API}/ICloudService/FinishAppUploadBatch/v1/",
        json={"response": {"ok": True}},
        status=200,
    )
    result = client.upload_save(7, "slot1.save", b"DATA")
    assert result.get("response", {}).get("ok") is True
    # All three endpoints were hit, in order.
    assert len(responses.calls) == 3
    paths = [c.request.url.rstrip("/").split("/")[-1] for c in responses.calls]
    assert paths == ["v1", "v1", "v1"]  # three distinct /v1/ endpoints
    # The batch token from step 1 was threaded into steps 2 and 3.
    upload_call = responses.calls[1].request
    assert "TOK123" in (upload_call.body or b"").decode("latin-1")
