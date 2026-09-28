import json
import sqlite3
import zipfile

import httpx
from fastapi.testclient import TestClient
from test_library import HEADERS, drain, png, upload

from figtrace.app import create_app
from figtrace.library import fingerprint


def configure(client, *, automatic=False, limit=20):
    response = client.put(
        "/api/ai/config/analysis",
        json={"model": "classification-test-model", "api_key": "fake-key-never-used"},
    )
    assert response.status_code == 200, response.text
    response = client.put(
        "/api/classification/config",
        json={"rules_enabled": True, "auto_ai": automatic, "daily_limit": limit},
    )
    assert response.status_code == 200, response.text


def result():
    return {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "category": "流程与示意图",
                            "tags": ["细胞", "通路"],
                            "description": "图中包含细胞结构示意。",
                            "visible_text": "Cell",
                        },
                        ensure_ascii=False,
                    )
                }
            }
        ],
    }


def batch(client, **body):
    response = client.post("/api/classification/batch", json={"method": "ai", **body})
    assert response.status_code == 200, response.text
    return response.json()


def records(client, asset_id, origin="ai"):
    response = client.get("/api/assets/" + asset_id)
    assert response.status_code == 200, response.text
    return [
        row for row in response.json()["classifications"] if row["origin"] == origin
    ]


def test_global_batch_reuses_completed_duplicate_without_spending_again(
    workspace, monkeypatch
):
    client, lib, _ = workspace
    configure(client)
    first, _ = upload(client, png(), project="甲项目", tags=["甲的标签"])
    second, _ = upload(client, png(), project="乙项目", tags=["乙的标签"])
    drain(lib)
    assert first != second
    calls = []
    monkeypatch.setattr(
        client.app.state.ai_service,
        "request",
        lambda *args: calls.append(args) or result(),
    )

    initial = batch(client)
    assert initial["queued"] == 1
    assert initial["skipped"] == 1
    assert client.app.state.ai_service.run_one()
    repeated = batch(client)
    assert repeated["processed"] == 1
    assert repeated["queued"] == 0
    assert len(calls) == 1
    assert client.get("/api/classification/config").json()["used_today"] == 1
    for asset_id, project, tags in (
        (first, "甲项目", ["甲的标签"]),
        (second, "乙项目", ["乙的标签"]),
    ):
        detail = client.get("/api/assets/" + asset_id).json()
        assert detail["project"] == project
        assert detail["tags"] == tags
        assert len(records(client, asset_id)) == 1
        assert records(client, asset_id)[0]["category"] == "流程与示意图"


def test_legacy_backup_restore_disables_auto_for_subsequent_imports(
    workspace, monkeypatch, tmp_path
):
    client, lib, _ = workspace
    configure(client, automatic=True)
    backup_name = lib.backup()
    backup_path = lib.data_dir / "backups" / backup_name
    # Convert the archive to the schema emitted before classification existed.
    with zipfile.ZipFile(backup_path) as archive:
        snapshot = tmp_path / "legacy.sqlite3"
        snapshot.write_bytes(archive.read("library.sqlite3"))
        manifest = json.loads(archive.read("manifest.json"))
    with sqlite3.connect(snapshot) as db:
        for table in (
            "classifications",
            "classification_queue",
            "classification_attempts",
        ):
            db.execute("DROP TABLE " + table)
        db.execute("DELETE FROM settings WHERE key='classification'")
    manifest["sha256"] = fingerprint(snapshot)
    with zipfile.ZipFile(backup_path, "w") as archive:
        archive.write(snapshot, "library.sqlite3")
        archive.writestr("manifest.json", json.dumps(manifest))
    calls = []
    monkeypatch.setattr(
        client.app.state.ai_service,
        "request",
        lambda *args: calls.append(args) or result(),
    )
    response = client.post("/api/backups/" + backup_name + "/restore")
    assert response.status_code == 200, response.text
    assert client.get("/api/classification/config").json()["auto_ai"] is False
    upload(client, png(), name="after-restore.png")
    drain(lib)
    assert not client.app.state.classifier.run_one()
    assert not client.app.state.ai_service.run_one()
    assert calls == []


def test_failed_attempt_quota_survives_restart(workspace, monkeypatch):
    client, lib, _ = workspace
    configure(client, automatic=True, limit=1)
    first, _ = upload(client, png(), name="first.png")
    drain(lib)
    calls = []

    def ambiguous_failure(*args):
        calls.append(args)
        raise httpx.ReadTimeout("Provider may already have charged")

    monkeypatch.setattr(client.app.state.ai_service, "request", ambiguous_failure)
    client.app.state.classifier.run_one()
    assert client.app.state.ai_service.run_one()
    assert client.get("/api/classification/config").json()["used_today"] == 1
    assert batch(client, asset_ids=[first])["queued"] == 0
    second, _ = upload(client, png("blue"), name="second.png")
    drain(lib)

    restarted = create_app(lib.data_dir, start_worker=False)
    with TestClient(restarted, headers=HEADERS) as fresh:
        monkeypatch.setattr(fresh.app.state.ai_service, "request", ambiguous_failure)
        fresh.app.state.classifier.run_one()
        assert not fresh.app.state.ai_service.run_one()
        config = fresh.get("/api/classification/config").json()
        assert config["used_today"] == 1
        assert config["remaining_today"] == 0
        assert fresh.get("/api/ai/tasks", params={"asset_id": second}).json() == []
        assert records(fresh, first) == []
    assert len(calls) == 1


def test_changed_original_before_dispatch_never_contacts_provider(
    workspace, monkeypatch
):
    client, lib, sources = workspace
    configure(client)
    path = sources / "source.png"
    path.write_bytes(png())
    lib.add_root(str(sources))
    drain(lib)
    asset_id = client.get("/api/assets").json()["items"][0]["id"]
    queued = batch(client, asset_ids=[asset_id])
    assert queued["queued"] == 1
    path.write_bytes(png("blue", dimensions=(81, 61)))
    calls = []
    monkeypatch.setattr(
        client.app.state.ai_service,
        "request",
        lambda *args: calls.append(args) or result(),
    )
    assert client.app.state.ai_service.run_one()
    assert calls == []
    tasks = client.get("/api/ai/tasks", params={"asset_id": asset_id}).json()
    assert tasks[0]["status"] == "failed"
    assert records(client, asset_id) == []


def test_preview_changed_during_analysis_is_not_published(workspace, monkeypatch):
    client, lib, _ = workspace
    configure(client)
    asset_id, _ = upload(client, png())
    drain(lib)
    assert batch(client, asset_ids=[asset_id])["queued"] == 1

    def changed_preview(*args):
        (lib.data_dir / "cache" / (asset_id + ".jpg")).write_bytes(
            b"changed-during-analysis"
        )
        return result()

    monkeypatch.setattr(client.app.state.ai_service, "request", changed_preview)
    assert client.app.state.ai_service.run_one()
    assert records(client, asset_id) == []
    assert (
        client.get("/api/ai/tasks", params={"asset_id": asset_id}).json()[0]["status"]
        == "failed"
    )


def test_preferred_change_keeps_classification_on_its_original_asset(
    workspace, monkeypatch
):
    client, lib, _ = workspace
    configure(client)
    first, _ = upload(client, png(), tags=["人工标签"])
    second, _ = upload(client, png("blue"), tags=["新版本标签"])
    drain(lib)
    response = client.post("/api/group", json={"asset_ids": [first, second]})
    assert response.status_code == 200, response.text
    assert batch(client, asset_ids=[first])["queued"] == 1

    def switch_preferred(*args):
        response = client.post("/api/assets/" + second + "/preferred")
        assert response.status_code == 200, response.text
        return result()

    monkeypatch.setattr(client.app.state.ai_service, "request", switch_preferred)
    assert client.app.state.ai_service.run_one()
    assert len(records(client, first)) == 1
    assert records(client, second) == []
    detail = client.get("/api/assets/" + second).json()
    assert detail["preferred"] == second
    assert "细胞" not in detail["tags"]


def test_edited_svg_enters_classification_like_other_new_assets(workspace, monkeypatch):
    client, lib, _ = workspace
    svg = '<svg xmlns="http://www.w3.org/2000/svg" width="160" height="100"><text x="10" y="30">Before</text></svg>'
    source_id, _ = upload(client, svg.encode(), name="workflow.svg")
    drain(lib)
    configure(client, automatic=True)
    calls = []
    monkeypatch.setattr(
        client.app.state.ai_service,
        "request",
        lambda *args: calls.append(args) or result(),
    )
    response = client.put(
        "/api/autofigure/assets/" + source_id + "/svg",
        json={
            "request_id": "classification-edit-0001",
            "svg": svg.replace("Before", "After"),
        },
    )
    assert response.status_code == 200, response.text
    edited_id = response.json()["asset_id"]
    assert records(client, edited_id, "rules")[0]["category"] == "流程与示意图"
    drain(lib)
    client.app.state.classifier.run_one()
    assert client.app.state.ai_service.run_one()
    assert len(calls) == 1
    assert len(records(client, edited_id)) == 1
    assert records(client, source_id) == []
