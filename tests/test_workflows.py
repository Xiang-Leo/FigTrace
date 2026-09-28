import hashlib
import shutil
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from figtrace.library import Library, SCHEMA
from test_library import drain, png, upload


def linked(lib, sources, name="before.png"):
    (sources / name).write_bytes(png())
    job = lib.add_root(str(sources), project="论文")
    drain(lib)
    asset = lib.one("SELECT * FROM assets WHERE root_id=?", (job["target"],))
    return asset, job["target"]


def test_rename_preserves_identity_metadata_links_and_history(workspace):
    client, lib, sources = workspace
    asset, root = linked(lib, sources)
    client.put(
        "/api/assets/" + asset["id"],
        json={
            "title": "自定义标题",
            "project": "自定义项目",
            "tags": ["保留"],
            "notes": "记录",
            "stage": "final",
            "version_note": "投稿版",
        },
    )
    source = sources / "script.py"
    source.write_text("pass")
    assert (
        client.post(
            "/api/assets/" + asset["id"] + "/links", json={"path": str(source)}
        ).status_code
        == 200
    )
    sub = sources / "reorganized"
    sub.mkdir()
    (sources / "before.png").rename(sub / "after.png")
    lib.enqueue("scan", root)
    drain(lib)
    detail = client.get("/api/assets/" + asset["id"]).json()
    assert detail["relative_path"] == "reorganized/after.png"
    assert detail["figure_id"] == asset["figure_id"]
    assert detail["title"] == "自定义标题"
    assert detail["project"] == "自定义项目"
    assert detail["tags"] == ["保留"]
    assert detail["stage"] == "final"
    assert detail["version_note"] == "投稿版"
    assert len(detail["links"]) == 1
    assert detail["locations"][0]["old_path"].endswith("before.png")
    assert detail["locations"][0]["new_path"].endswith("after.png")
    assert client.get("/api/overview").json()["assets"] == 1
    assert detail["preview"] == "ready"
    backup = lib.backup()
    lib.restore(backup)
    restored = client.get("/api/assets/" + asset["id"]).json()
    assert restored["locations"] == detail["locations"]
    assert restored["stage"] == "final"


def test_cross_root_move_preserves_original_project(workspace, tmp_path):
    client, lib, sources = workspace
    asset, _ = linked(lib, sources)
    destination = tmp_path / "destination"
    destination.mkdir()
    root = lib.add_root(str(destination), project="新目录项目")["target"]
    drain(lib)
    (sources / "before.png").rename(destination / "moved.png")
    lib.enqueue("scan", root)
    drain(lib)
    detail = client.get("/api/assets/" + asset["id"]).json()
    assert detail["root_id"] == root
    assert detail["project"] == "论文"
    assert client.get("/api/overview").json()["assets"] == 1


@pytest.mark.parametrize(
    "mode", ["copy", "changed", "multiple_new", "multiple_old", "unhashed"]
)
def test_uncertain_moves_do_not_merge(workspace, mode):
    client, lib, sources = workspace
    asset, root = linked(lib, sources)
    if mode == "multiple_old":
        shutil.copyfile(sources / "before.png", sources / "second.png")
        lib.enqueue("scan", root)
        drain(lib)
        (sources / "second.png").unlink()
    if mode == "unhashed":
        with lib.db() as db:
            db.execute("UPDATE assets SET sha256=NULL")
    (sources / "new.png").write_bytes(png("blue") if mode == "changed" else png())
    if mode == "multiple_new":
        (sources / "another.png").write_bytes(png())
    if mode != "copy":
        (sources / "before.png").unlink()
    lib.enqueue("scan", root)
    drain(lib)
    old = client.get("/api/assets/" + asset["id"]).json()
    assert old["relative_path"] == "before.png"
    assert old["locations"] == []
    assert client.get("/api/overview").json()["assets"] >= 2


def test_offline_root_not_mistaken_for_move(workspace, tmp_path):
    _, lib, sources = workspace
    asset, _ = linked(lib, sources)
    destination = tmp_path / "other"
    destination.mkdir()
    (destination / "copy.png").write_bytes(png())
    sources.rename(tmp_path / "offline")
    lib.add_root(str(destination))
    drain(lib)
    assert (
        lib.one("SELECT relative_path FROM assets WHERE id=?", (asset["id"],))[
            "relative_path"
        ]
        == "before.png"
    )
    assert lib.one("SELECT COUNT(*) AS count FROM assets")["count"] == 2


def test_no_new_paths_avoids_rehashing(workspace, monkeypatch):
    _, lib, sources = workspace
    _, root = linked(lib, sources)
    monkeypatch.setattr(
        "figtrace.library.fingerprint",
        lambda _: pytest.fail("Unchanged scan must not hash"),
    )
    lib.scan(root)


def test_slow_preview_does_not_block_upload_and_restore_is_guarded(
    workspace, monkeypatch
):
    client, lib, _ = workspace
    asset, _ = upload(client, png())
    backup = lib.backup()
    started, finish = threading.Event(), threading.Event()
    original = lib.preview

    def slow_preview(asset_id):
        started.set()
        assert finish.wait(8)
        return original(asset_id)

    monkeypatch.setattr(lib, "preview", slow_preview)
    with ThreadPoolExecutor(max_workers=2) as pool:
        converting = pool.submit(lib.run_one)
        try:
            assert started.wait(2)
            data = png("blue")
            task = lib.create_upload(
                "parallel.png", len(data), hashlib.sha256(data).hexdigest(), "", []
            )

            def transfer():
                lib.append_upload(task["id"], 0, data)
                return lib.complete_upload(task["id"])

            assert pool.submit(transfer).result(timeout=2)["asset_id"] != asset
            with pytest.raises(ValueError, match="扫描或预览"):
                lib.restore(backup)
        finally:
            finish.set()
        assert converting.result(timeout=5)


def test_stages_filter_bulk_split_group_and_project_counts(workspace):
    client, lib, _ = workspace
    first, _ = upload(client, png(), project="课题")
    second, _ = upload(client, png("blue"), project="课题")
    assert client.get("/api/assets/" + first).json()["stage"] == "draft"
    assert (
        client.post(
            "/api/bulk", json={"asset_ids": [first, second], "stage": "review"}
        ).status_code
        == 200
    )
    assert client.get("/api/assets", params={"stage": "review"}).json()["total"] == 2
    assert client.get("/api/assets", params={"stage": "final"}).json()["total"] == 0
    assert client.get("/api/projects").json()[0]["stages"] == {
        "draft": 0,
        "review": 2,
        "final": 0,
    }
    assert (
        client.post(
            "/api/bulk", json={"asset_ids": [first], "stage": "unknown"}
        ).status_code
        == 422
    )
    client.put(
        "/api/assets/" + first,
        json={"title": "figure", "project": "课题", "stage": "final"},
    )
    client.put(
        "/api/assets/" + first, json={"title": "legacy client", "project": "课题"}
    )
    assert client.get("/api/assets/" + first).json()["stage"] == "final"
    client.post("/api/group", json={"asset_ids": [first, second]})
    assert client.get("/api/assets/" + second).json()["stage"] == "final"
    client.post("/api/assets/" + second + "/ungroup")
    assert client.get("/api/assets/" + second).json()["stage"] == "final"
    assert client.get("/api/projects").json()[0]["stages"]["final"] == 2
    name = lib.backup()
    client.post("/api/bulk", json={"asset_ids": [first], "stage": "draft"})
    lib.restore(name)
    assert client.get("/api/assets/" + first).json()["stage"] == "final"


def test_pre_stage_database_migrates_without_losing_figures(tmp_path):
    data = tmp_path / "legacy"
    data.mkdir()
    with sqlite3.connect(data / "library.sqlite3") as db:
        db.executescript(SCHEMA)
        db.execute(
            "INSERT INTO figures(id,title,project,created) VALUES ('old','旧图','旧课题',1)"
        )
    lib = Library(data)
    assert lib.one("SELECT stage,title FROM figures") == {
        "stage": "draft",
        "title": "旧图",
    }
    assert lib.one("SELECT name FROM projects")["name"] == "旧课题"
