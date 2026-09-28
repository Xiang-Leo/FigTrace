"""Storage relocation keeps the local catalogue and every original usable."""

import base64
import hashlib
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_library import HEADERS, drain, png, upload

from figtrace.app import create_app


def move_storage(client, directory):
    response = client.put("/api/settings/storage", json={"directory": str(directory)})
    assert response.status_code == 200, response.text
    assert Path(response.json()["directory"]) == directory.resolve()
    return response.json()


def blob(directory, content, extension="png"):
    return directory / (hashlib.sha256(content).hexdigest() + "." + extension)


def test_relocation_preserves_originals_metadata_and_restart(workspace, tmp_path):
    client, library, _ = workspace
    first_data, second_data = png("red"), png("blue")
    first, _ = upload(client, first_data, project="保存项目", tags=["手动标签"])
    second, _ = upload(client, second_data, name="another/figure.png")
    duplicate, _ = upload(client, first_data, name="duplicate/figure.png")
    client.put(
        "/api/assets/" + first,
        json={
            "title": "原有标题",
            "project": "保存项目",
            "notes": "原有备注",
            "tags": ["手动标签"],
        },
    )
    drain(library)
    before = library.query("SELECT id,size,mtime FROM assets ORDER BY id")
    old = library.data_dir / "originals"
    destination = tmp_path / "同步目录" / "FigTrace originals"

    move_storage(client, destination)

    assert library.query("SELECT id,size,mtime FROM assets ORDER BY id") == before
    assert len(list(destination.glob("*.png"))) == 2
    for asset_id, data in (
        (first, first_data),
        (second, second_data),
        (duplicate, first_data),
    ):
        assert blob(destination, data).read_bytes() == data
        assert blob(old, data).read_bytes() == data
        assert client.get(f"/api/assets/{asset_id}/download").content == data
    detail = client.get("/api/assets/" + first).json()
    assert detail["title"] == "原有标题"
    assert detail["notes"] == "原有备注"
    assert detail["tags"] == ["手动标签"]
    assert detail["project"] == "保存项目"
    assert library.db_path.parent == library.data_dir
    assert (library.data_dir / "cache" / (first + ".jpg")).is_file()

    restarted = create_app(library.data_dir, start_worker=False)
    with TestClient(restarted, headers=HEADERS) as fresh:
        assert (
            Path(fresh.get("/api/settings/storage").json()["directory"]) == destination
        )
        assert fresh.get(f"/api/assets/{first}/download").content == first_data
        newer = png("yellow")
        asset_id, _ = upload(fresh, newer, name="new/new.png")
        assert blob(destination, newer).read_bytes() == newer
        assert not blob(old, newer).exists()
        assert fresh.get(f"/api/assets/{asset_id}/download").content == newer


def test_upload_started_before_relocation_finishes_in_current_directory(
    workspace, tmp_path
):
    from test_library import new_upload

    client, library, _ = workspace
    data = png("purple")
    upload_id = new_upload(client, data)
    assert (
        client.put(f"/api/uploads/{upload_id}?offset=0", content=data).status_code
        == 200
    )
    destination = tmp_path / "new-originals"
    move_storage(client, destination)
    response = client.post(f"/api/uploads/{upload_id}/complete")
    assert response.status_code == 200, response.text
    assert blob(destination, data).read_bytes() == data
    assert not blob(library.data_dir / "originals", data).exists()


def test_ai_generation_and_svg_versions_use_configured_directory(
    workspace, tmp_path, monkeypatch
):
    client, library, _ = workspace
    ai = client.app.state.ai_service
    destination = tmp_path / "generated-originals"
    move_storage(client, destination)
    response = client.put(
        "/api/ai/config/generation",
        json={"model": "fixture-model", "api_key": "fixture-key"},
    )
    assert response.status_code == 200
    data = png("orange")
    monkeypatch.setattr(
        ai,
        "request",
        lambda *args, **kwargs: {
            "data": [{"b64_json": base64.b64encode(data).decode()}]
        },
    )
    queued = client.post(
        "/api/ai/generate",
        json={"request_id": "storage-generation-0001", "prompt": "存储测试"},
    )
    assert queued.status_code == 200, queued.text
    assert ai.run_one()
    task = client.get("/api/ai/tasks").json()[0]
    assert task["status"] == "completed", task
    assert blob(destination, data).read_bytes() == data
    assert not blob(library.data_dir / "originals", data).exists()

    svg = '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="80"><rect width="100" height="80" fill="red"/></svg>'
    edited = client.app.state.autofigure_service.save_version(
        task["asset_id"], svg, "storage-svg-edit-0001"
    )
    record = library.one("SELECT * FROM assets WHERE id=?", (edited,))
    assert library.asset_path(record).parent == destination
    assert library.asset_path(record).read_text().startswith("<svg")


def test_restore_keeps_current_storage_directory(workspace, tmp_path):
    client, library, _ = workspace
    data = png()
    asset_id, _ = upload(client, data)
    archive = library.backup()
    destination = tmp_path / "new-originals"
    move_storage(client, destination)
    upload(client, png("blue"), name="after-backup.png")

    response = client.post(f"/api/backups/{archive}/restore")

    assert response.status_code == 200, response.text
    assert Path(client.get("/api/settings/storage").json()["directory"]) == destination
    assert client.get(f"/api/assets/{asset_id}/download").content == data
    assert client.get("/api/overview").json()["assets"] == 1


def test_conflicting_destination_is_not_overwritten_or_activated(workspace, tmp_path):
    client, library, _ = workspace
    data = png()
    asset_id, _ = upload(client, data)
    destination = tmp_path / "new-originals"
    destination.mkdir()
    conflict = blob(destination, data)
    conflict.write_bytes(b"different existing content")

    response = client.put("/api/settings/storage", json={"directory": str(destination)})

    assert response.status_code == 400, response.text
    assert conflict.read_bytes() == b"different existing content"
    assert (
        Path(client.get("/api/settings/storage").json()["directory"])
        == library.data_dir / "originals"
    )
    assert client.get(f"/api/assets/{asset_id}/download").content == data


def test_corrupt_source_does_not_activate_destination(workspace, tmp_path):
    client, library, _ = workspace
    data = png()
    upload(client, data)
    blob(library.data_dir / "originals", data).write_bytes(b"damaged original")

    response = client.put(
        "/api/settings/storage", json={"directory": str(tmp_path / "new-originals")}
    )

    assert response.status_code == 400, response.text
    assert (
        Path(client.get("/api/settings/storage").json()["directory"])
        == library.data_dir / "originals"
    )


@pytest.mark.parametrize("directory", ["cache", "uploads", "backups"])
def test_internal_directories_cannot_be_used_for_originals(workspace, directory):
    client, library, _ = workspace
    response = client.put(
        "/api/settings/storage", json={"directory": str(library.data_dir / directory)}
    )
    assert response.status_code == 400, response.text


def test_linked_files_are_not_migrated(workspace, tmp_path):
    client, library, sources = workspace
    source = sources / "linked.png"
    data = png("pink")
    source.write_bytes(data)
    assert client.post("/api/roots", json={"path": str(sources)}).status_code == 200
    drain(library)
    asset_id = library.one("SELECT id FROM assets")["id"]
    destination = tmp_path / "new-originals"

    move_storage(client, destination)

    assert source.read_bytes() == data
    assert not list(destination.glob("*.png"))
    assert client.get(f"/api/assets/{asset_id}/download").content == data


def test_unreferenced_blobs_are_preserved_for_older_metadata_backups(
    workspace, tmp_path
):
    client, library, _ = workspace
    data = png("purple")
    # A metadata restore can remove an asset row while its original remains on disk.
    archive = library.backup()
    upload(client, data)
    assert client.post(f"/api/backups/{archive}/restore").status_code == 200
    assert client.get("/api/overview").json()["assets"] == 0
    destination = tmp_path / "new-originals"

    move_storage(client, destination)

    assert blob(destination, data).read_bytes() == data


def test_copy_failure_preserves_active_directory_and_downloads(
    workspace, tmp_path, monkeypatch
):
    client, library, _ = workspace
    first, _ = upload(client, png("red"))
    second, _ = upload(client, png("blue"), name="second.png")
    original_copy = shutil.copy2
    calls = []

    def fail_second(*args, **kwargs):
        calls.append(args[0])
        if len(calls) == 2:
            raise OSError("simulated disk failure")
        return original_copy(*args, **kwargs)

    monkeypatch.setattr("figtrace.library.shutil.copy2", fail_second)
    response = client.put(
        "/api/settings/storage", json={"directory": str(tmp_path / "new-originals")}
    )

    assert response.status_code == 400, response.text
    assert len(calls) == 2
    assert (
        Path(client.get("/api/settings/storage").json()["directory"])
        == library.data_dir / "originals"
    )
    assert client.get(f"/api/assets/{first}/download").content == png("red")
    assert client.get(f"/api/assets/{second}/download").content == png("blue")


def test_unavailable_source_does_not_silently_create_empty_storage(workspace, tmp_path):
    client, library, _ = workspace
    upload(client, png())
    original = library.data_dir / "originals"
    original.rename(library.data_dir / "temporarily-offline-originals")

    response = client.put(
        "/api/settings/storage", json={"directory": str(tmp_path / "new-originals")}
    )

    assert response.status_code == 400, response.text
    assert Path(client.get("/api/settings/storage").json()["directory"]) == original
    assert not original.exists()


def test_target_symlink_blob_and_unrelated_content_are_rejected(workspace, tmp_path):
    client, library, _ = workspace
    data = png()
    upload(client, data)
    destination = tmp_path / "new-originals"
    destination.mkdir()
    original = blob(library.data_dir / "originals", data)
    target = blob(destination, data)
    try:
        target.symlink_to(original)
    except OSError:
        pytest.skip("Symlink creation not available")

    response = client.put("/api/settings/storage", json={"directory": str(destination)})
    assert response.status_code == 400, response.text
    assert target.is_symlink()
    target.unlink()
    (destination / "unrelated.txt").write_text("do not touch")
    response = client.put("/api/settings/storage", json={"directory": str(destination)})
    assert response.status_code == 400, response.text
    assert (destination / "unrelated.txt").read_text() == "do not touch"


def test_linked_root_overlap_and_backup_overlap_are_rejected(workspace):
    client, library, sources = workspace
    assert client.post("/api/roots", json={"path": str(sources)}).status_code == 200
    for destination in (
        sources,
        sources / "originals",
        sources.parent,
        library.data_dir / "backups" / "originals",
    ):
        response = client.put(
            "/api/settings/storage", json={"directory": str(destination)}
        )
        assert response.status_code == 400, (destination, response.text)


def test_remote_storage_setting_is_read_only(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    app = create_app(
        tmp_path / "data",
        start_worker=False,
        password="test-only-password",
        allowed_roots=[allowed],
        local_mode=False,
    )
    with TestClient(app, headers=HEADERS) as client:
        assert (
            client.post(
                "/api/login", json={"password": "test-only-password"}
            ).status_code
            == 200
        )
        config = client.get("/api/settings/storage")
        assert config.status_code == 200, config.text
        assert config.json()["local_mode"] is False
        response = client.put(
            "/api/settings/storage", json={"directory": str(allowed / "originals")}
        )
        assert response.status_code == 400, response.text
        assert not (allowed / "originals").exists()


def test_scanning_parent_of_active_storage_does_not_duplicate_managed_blobs(
    workspace, tmp_path
):
    client, library, _ = workspace
    upload(client, png("red"))
    parent = tmp_path / "synced-project"
    destination = parent / "originals"
    move_storage(client, destination)
    (parent / "linked-figure.png").write_bytes(png("blue"))

    response = client.post("/api/roots", json={"path": str(parent)})
    assert response.status_code == 200, response.text
    drain(library)

    assert client.get("/api/overview").json()["assets"] == 2
    linked = library.query("SELECT name FROM assets WHERE root_id<>'managed'")
    assert linked == [{"name": "linked-figure.png"}]


def test_switching_back_to_default_directory_preserves_new_originals(
    workspace, tmp_path
):
    client, library, _ = workspace
    first_data, second_data = png("red"), png("blue")
    first, _ = upload(client, first_data)
    destination = tmp_path / "custom-originals"
    move_storage(client, destination)
    second, _ = upload(client, second_data, name="second.png")

    result = move_storage(client, library.data_dir / "originals")

    assert result["copied_files"] == 1
    assert client.get(f"/api/assets/{first}/download").content == first_data
    assert client.get(f"/api/assets/{second}/download").content == second_data
    assert blob(destination, first_data).read_bytes() == first_data
    assert blob(destination, second_data).read_bytes() == second_data
