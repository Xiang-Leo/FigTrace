import base64
import json
import os
import sqlite3
import zipfile

import httpx
import pytest
from fastapi.testclient import TestClient

from figtrace.app import create_app
from figtrace.library import Library
from test_library import HEADERS, drain, png, upload


@pytest.fixture
def setup(tmp_path):
    app = create_app(tmp_path / "data", start_worker=False)
    with TestClient(app, headers=HEADERS) as client:
        yield client, app.state.library, app.state.ai_service


def configure(client, kind="analysis", **extra):
    response = client.put(
        "/api/ai/config/" + kind,
        json={"model": "test-model", "api_key": "test-secret", **extra},
    )
    assert response.status_code == 200, response.text
    return response.json()


def fake_provider(monkeypatch, handler):
    original = httpx.Client
    monkeypatch.setattr(
        "figtrace.features.httpx.Client",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )


def test_projects_exist_without_assets_and_rename_all_references(setup):
    client, lib, _ = setup
    p = client.post(
        "/api/projects", json={"name": "空项目", "description": "课题"}
    ).json()["id"]
    assert "空项目" in client.get("/api/overview").json()["projects"]
    asset, _ = upload(client, png(), project="空项目")
    result = client.put(
        "/api/projects/" + p, json={"name": "改名项目", "archived": True}
    )
    assert result.status_code == 200
    assert client.get("/api/assets/" + asset).json()["project"] == "改名项目"
    assert lib.one("SELECT project FROM uploads")["project"] == "改名项目"
    assert not client.get("/api/overview").json()["projects"]
    assert client.get("/api/projects").json()[0]["figures"] == 1
    assert client.delete("/api/projects/" + p).status_code == 400
    assert client.get("/api/assets").json()["total"] == 1


def test_project_conflict_rolls_back_and_empty_delete(setup):
    client, _, _ = setup
    first = client.post("/api/projects", json={"name": "A"}).json()["id"]
    second = client.post("/api/projects", json={"name": "B"}).json()["id"]
    assert client.put("/api/projects/" + first, json={"name": "B"}).status_code == 409
    assert client.get("/api/overview").json()["projects"] == ["A", "B"]
    assert client.delete("/api/projects/" + second).status_code == 200
    assert client.post("/api/projects", json={"name": "   "}).status_code == 400


def test_legacy_project_migration_and_backup_restore(setup, tmp_path):
    client, lib, _ = setup
    upload(client, png(), project="旧项目")
    backup = lib.backup()
    # Simulate a pre-0.2 backup with no feature tables.
    path = lib.data_dir / "backups" / backup
    with zipfile.ZipFile(path) as archive:
        db_path = tmp_path / "legacy.sqlite3"
        db_path.write_bytes(archive.read("library.sqlite3"))
        manifest = json.loads(archive.read("manifest.json"))
    with sqlite3.connect(db_path) as db:
        db.execute("DROP TABLE projects")
        db.execute("DROP TABLE ai_tasks")
    from figtrace.library import fingerprint

    manifest["sha256"] = fingerprint(db_path)
    with zipfile.ZipFile(path, "w") as archive:
        archive.write(db_path, "library.sqlite3")
        archive.writestr("manifest.json", json.dumps(manifest))
    lib.restore(backup)
    assert client.get("/api/projects").json()[0]["name"] == "旧项目"
    assert client.get("/api/ai/tasks").json() == []


def test_secrets_masking_backup_and_endpoint_change(setup):
    client, lib, ai = setup
    result = configure(client)
    assert result["analysis"]["has_key"]
    assert "test-secret" not in json.dumps(result)
    assert "test-secret" not in client.get("/api/ai/config").text
    if os.name == "posix":
        assert (ai.config_path.stat().st_mode & 0o077) == 0
    with zipfile.ZipFile(lib.data_dir / "backups" / lib.backup()) as archive:
        assert set(archive.namelist()) == {"manifest.json", "library.sqlite3"}
        assert b"test-secret" not in archive.read("library.sqlite3")
    result = client.put(
        "/api/ai/config/analysis",
        json={"base_url": "https://example.org/v1", "model": "vision"},
    ).json()
    assert not result["analysis"]["has_key"]
    assert (
        client.put(
            "/api/ai/config/analysis", json={"base_url": "http://example.org/v1"}
        ).status_code
        == 400
    )


def test_classification_wire_request_review_and_additive_apply(setup, monkeypatch):
    client, lib, ai = setup
    asset, _ = upload(client, png(), tags=["手动标签"])
    drain(lib)
    configure(client)
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-secret"
        body = json.loads(request.content)
        assert body["model"] == "test-model"
        url = body["messages"][1]["content"][1]["image_url"]["url"]
        assert url.startswith("data:image/jpeg;base64,")
        assert "project/figure" not in request.content.decode()
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "category": "示意图",
                                    "tags": ["细胞", "示意图"],
                                    "description": "细胞结构示意",
                                    "visible_text": "A",
                                }
                            )
                        }
                    }
                ]
            },
        )

    fake_provider(monkeypatch, handler)
    ids = client.post("/api/ai/analyze", json={"asset_ids": [asset]}).json()["task_ids"]
    assert (
        client.post("/api/ai/analyze", json={"asset_ids": [asset]}).json()["task_ids"]
        == ids
    )
    assert ai.run_one()
    assert len(calls) == 1
    assert client.get("/api/assets/" + asset).json()["tags"] == ["手动标签"]
    response = client.post(
        "/api/ai/tasks/" + ids[0] + "/apply",
        json={"tags": ["细胞"], "include_description": True},
    )
    assert response.status_code == 200, response.text
    updated = client.get("/api/assets/" + asset).json()
    assert updated["tags"] == ["手动标签", "细胞"]
    assert "细胞结构示意" in updated["notes"]
    assert client.get("/api/assets", params={"q": "细胞结构示意"}).json()["total"] == 1
    assert (
        client.post("/api/ai/tasks/" + ids[0] + "/apply", json={"tags": []}).status_code
        == 400
    )


def test_generation_wire_idempotency_project_rename_and_provenance(setup, monkeypatch):
    client, lib, ai = setup
    project = client.post("/api/projects", json={"name": "生成项目"}).json()["id"]
    configure(client, "generation")
    data = png("blue")
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.path == "/v1/images/generations"
        body = json.loads(request.content)
        assert body == {"model": "test-model", "prompt": "科学示意图", "n": 1}
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "b64_json": base64.b64encode(data).decode(),
                        "revised_prompt": "refined",
                    }
                ]
            },
        )

    fake_provider(monkeypatch, handler)
    body = {
        "request_id": "generation-request-1234",
        "prompt": "科学示意图",
        "project_id": project,
    }
    first = client.post("/api/ai/generate", json=body).json()
    assert client.post("/api/ai/generate", json=body).json() == first
    assert (
        client.post(
            "/api/ai/generate", json={**body, "prompt": "different"}
        ).status_code
        == 400
    )
    client.put("/api/projects/" + project, json={"name": "生成项目改名"})
    assert ai.run_one()
    assert not ai.run_one()
    assert len(calls) == 1
    task = client.get("/api/ai/tasks").json()[0]
    assert task["status"] == "completed", task
    assert task["result"]["revised_prompt"] == "refined"
    asset = client.get("/api/assets/" + task["asset_id"]).json()
    assert asset["project"] == "生成项目改名"
    assert asset["tags"] == ["AI生成"]
    assert "科学示意图" in asset["notes"]
    assert client.get("/api/assets/" + asset["id"] + "/download").content == data


def test_failed_paid_request_not_retried_or_leaked(setup, monkeypatch):
    client, _, ai = setup
    configure(client, "generation")
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(401, text="credential test-secret invalid")

    fake_provider(monkeypatch, handler)
    client.post(
        "/api/ai/generate",
        json={"request_id": "failure-request-1234", "prompt": "test"},
    )
    assert ai.run_one()
    assert not ai.run_one()
    task = client.get("/api/ai/tasks").json()[0]
    assert task["status"] == "failed"
    assert "HTTP 401" in task["error"]
    assert "test-secret" not in json.dumps(task)
    assert len(calls) == 1


def test_config_change_and_restart_never_replay_tasks(setup, monkeypatch):
    client, lib, ai = setup
    configure(client, "generation")
    client.post(
        "/api/ai/generate", json={"request_id": "config-request-1234", "prompt": "test"}
    )
    configure(client, "generation", model="new-model")
    monkeypatch.setattr(
        ai, "request", lambda *args: pytest.fail("No request should be made")
    )
    ai.run_one()
    assert "设置已改变" in client.get("/api/ai/tasks").json()[0]["error"]
    client.post(
        "/api/ai/generate",
        json={"request_id": "restart-request-1234", "prompt": "test"},
    )
    fresh = Library(lib.data_dir)
    assert (
        fresh.one("SELECT status FROM ai_tasks WHERE id=?", ("restart-request-1234",))[
            "status"
        ]
        == "failed"
    )


def test_partial_analysis_selection_rolls_back_and_stale_preview_rejected(
    setup, monkeypatch
):
    client, lib, ai = setup
    asset, _ = upload(client, png())
    drain(lib)
    configure(client)
    assert (
        client.post(
            "/api/ai/analyze", json={"asset_ids": [asset, "missing"]}
        ).status_code
        == 400
    )
    assert client.get("/api/ai/tasks").json() == []
    monkeypatch.setattr(
        ai,
        "request",
        lambda *args: {"choices": [{"message": {"content": '{"tags":["tag"]}'}}]},
    )
    task = client.post("/api/ai/analyze", json={"asset_ids": [asset]}).json()[
        "task_ids"
    ][0]
    ai.run_one()
    (lib.data_dir / "cache" / (asset + ".jpg")).write_bytes(b"changed")
    assert (
        client.post(
            "/api/ai/tasks/" + task + "/apply", json={"tags": ["tag"]}
        ).status_code
        == 400
    )


def test_restore_blocks_running_ai_and_cancels_snapshot_queue(setup):
    client, lib, _ = setup
    configure(client, "generation")
    client.post(
        "/api/ai/generate",
        json={"request_id": "restore-request-1234", "prompt": "test"},
    )
    backup = lib.backup()
    with lib.db() as db:
        db.execute("UPDATE ai_tasks SET status='running'")
    with pytest.raises(ValueError, match="AI 任务运行中"):
        lib.restore(backup)
    with lib.db() as db:
        db.execute("UPDATE ai_tasks SET status='failed'")
    lib.restore(backup)
    assert lib.one("SELECT status FROM ai_tasks")["status"] == "failed"
