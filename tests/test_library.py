import hashlib
import io
import json
import shutil
import zipfile

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from figtrace.app import create_app
from figtrace.library import Library


HEADERS = {"X-Figtrace-Request": "1"}


def png(color="green", dimensions=(80, 60)):
    buffer = io.BytesIO()
    Image.new("RGB", dimensions, color).save(buffer, format="PNG")
    return buffer.getvalue()


def new_upload(client, data, name="project/figure.png", project="论文", tags=None):
    response = client.post(
        "/api/uploads",
        json={
            "relative_path": name,
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "project": project,
            "tags": tags or ["折线图"],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def upload(client, data, name="project/figure.png", project="论文", tags=None):
    upload_id = new_upload(client, data, name, project, tags)
    response = client.put(f"/api/uploads/{upload_id}?offset=0", content=data)
    assert response.status_code == 200, response.text
    response = client.post(f"/api/uploads/{upload_id}/complete")
    assert response.status_code == 200, response.text
    return response.json()["asset_id"], upload_id


def drain(library, limit=50):
    for _ in range(limit):
        if not library.run_one():
            return
    raise AssertionError("Worker failed to drain")


def test_upload_resume_survives_restart_and_preserves_path(workspace):
    client, library, _ = workspace
    data = png()
    upload_id = new_upload(client, data)
    split = len(data) // 2
    assert (
        client.put(
            f"/api/uploads/{upload_id}?offset=0", content=data[:split]
        ).status_code
        == 200
    )
    restarted = create_app(library.data_dir, start_worker=False)
    with TestClient(restarted, headers=HEADERS) as fresh:
        assert fresh.get(f"/api/uploads/{upload_id}").json()["offset"] == split
        assert (
            fresh.put(
                f"/api/uploads/{upload_id}?offset=0", content=data[split:]
            ).status_code
            == 400
        )
        assert (
            fresh.put(
                f"/api/uploads/{upload_id}?offset={split}", content=data[split:]
            ).status_code
            == 200
        )
        result = fresh.post(f"/api/uploads/{upload_id}/complete").json()
        assert (
            fresh.post(f"/api/uploads/{upload_id}/complete").json()["asset_id"]
            == result["asset_id"]
        )
        asset = fresh.get("/api/assets/" + result["asset_id"]).json()
        assert asset["source_path"] == "project/figure.png"
        assert fresh.get("/api/assets/" + asset["id"] + "/download").content == data
        assert fresh.get("/api/overview").json()["assets"] == 1


def test_wrong_content_cannot_complete(workspace):
    client, _, _ = workspace
    data = png()
    upload_id = new_upload(client, data)
    assert (
        client.put(
            f"/api/uploads/{upload_id}?offset=0", content=b"x" * len(data)
        ).status_code
        == 200
    )
    assert client.post(f"/api/uploads/{upload_id}/complete").status_code == 400
    assert client.get("/api/overview").json()["assets"] == 0


def test_incomplete_upload_and_oversized_chunk_rejected(workspace):
    client, _, _ = workspace
    upload_id = new_upload(client, png())
    assert client.post(f"/api/uploads/{upload_id}/complete").status_code == 400
    assert (
        client.put(
            f"/api/uploads/{upload_id}?offset=0", content=b"x" * (4 * 1024**2 + 1)
        ).status_code
        == 413
    )
    assert client.get(f"/api/uploads/{upload_id}").json()["offset"] == 0


@pytest.mark.parametrize(
    "path",
    [
        "../secret.png",
        "/etc/a.png",
        "C:\\a.png",
        "root/../../a.png",
        "a/./b.png",
        "a\x00.png",
    ],
)
def test_path_traversal_rejected(workspace, path):
    client, _, _ = workspace
    response = client.post(
        "/api/uploads", json={"relative_path": path, "size": 5, "sha256": "a" * 64}
    )
    assert response.status_code == 400


def test_same_name_different_content_and_duplicate_sources(workspace):
    client, library, _ = workspace
    first, _ = upload(client, png("red"))
    second, _ = upload(client, png("blue"))
    third, _ = upload(client, png("red"), name="other/figure.png")
    assert len({first, second, third}) == 3
    assert len(list((library.data_dir / "originals").iterdir())) == 2
    assert (
        client.get("/api/assets/" + first + "/download").content
        != client.get("/api/assets/" + second + "/download").content
    )
    assert (
        client.get("/api/assets/" + third).json()["source_path"] == "other/figure.png"
    )


def test_preview_pdf_tiff_and_bad_file_do_not_block_other_jobs(workspace):
    client, library, _ = workspace
    image = Image.new("RGB", (90, 70), "red")
    pdf, tiff = io.BytesIO(), io.BytesIO()
    image.save(
        pdf, "PDF", save_all=True, append_images=[Image.new("RGB", (90, 70), "blue")]
    )
    image.save(
        tiff, "TIFF", save_all=True, append_images=[Image.new("RGB", (90, 70), "green")]
    )
    pdf_id, _ = upload(client, pdf.getvalue(), "report.pdf")
    tiff_id, _ = upload(client, tiff.getvalue(), "cells.tiff")
    bad_id, _ = upload(client, b"not a real image", "bad.png")
    good_id, _ = upload(client, png(), "good.png")
    drain(library)
    for asset_id in (pdf_id, tiff_id):
        asset = client.get("/api/assets/" + asset_id).json()
        assert asset["preview"] == "ready", asset
        assert asset["pages"] == 2
        assert (
            client.get("/api/assets/" + asset_id + "/preview?page=1").status_code == 200
        )
        assert (
            client.get("/api/assets/" + asset_id + "/preview?page=2").status_code == 400
        )
    assert client.get("/api/assets/" + bad_id).json()["preview"] == "failed"
    assert client.get("/api/assets/" + good_id + "/preview").status_code == 200


def test_scan_incremental_missing_and_inaccessible_root(workspace):
    client, library, sources = workspace
    (sources / "a.png").write_bytes(png())
    (sources / "nested").mkdir()
    (sources / "nested" / "b.png").write_bytes(png("blue"))
    response = client.post(
        "/api/roots", json={"path": str(sources), "project": "项目 A"}
    )
    root_id = response.json()["target"]
    drain(library)
    initial = client.get("/api/assets").json()
    assert initial["total"] == 2
    previous_jobs = library.one("SELECT COUNT(*) AS n FROM jobs")["n"]
    library.scan(root_id)
    assert library.one("SELECT COUNT(*) AS n FROM jobs")["n"] == previous_jobs
    (sources / "a.png").unlink()
    library.scan(root_id)
    assert (
        library.one("SELECT COUNT(*) AS n FROM assets WHERE preview='missing'")["n"]
        == 1
    )
    shutil.move(sources, sources.with_name("moved"))
    with pytest.raises(ValueError):
        library.scan(root_id)
    assert client.get("/api/assets").json()["total"] == 2
    assert (
        client.put(
            "/api/roots/" + root_id, json={"path": str(sources.with_name("moved"))}
        ).status_code
        == 200
    )
    drain(library)
    assert (
        library.one("SELECT status FROM roots WHERE id=?", (root_id,))["status"]
        == "ready"
    )


def test_link_and_group_preserve_versions_notes_and_search(workspace):
    client, _, sources = workspace
    first, _ = upload(client, png(), name="current.png")
    second, _ = upload(client, png("blue"), name="old-special-version.png")
    for asset_id, title, tag in [(first, "图 A", "tag-a"), (second, "图 B", "tag-b")]:
        assert (
            client.put(
                "/api/assets/" + asset_id,
                json={
                    "title": title,
                    "tags": [tag],
                    "notes": title + "备注",
                    "project": "论文",
                },
            ).status_code
            == 200
        )
    script = sources / "plot.py"
    script.write_text('print("source")')
    assert (
        client.post(
            "/api/assets/" + second + "/links", json={"path": str(script)}
        ).status_code
        == 200
    )
    assert (
        client.post("/api/group", json={"asset_ids": [first, second]}).status_code
        == 200
    )
    assert client.get("/api/assets").json()["total"] == 1
    assert client.get("/api/assets?q=old-special").json()["total"] == 1
    merged = client.get("/api/assets/" + second).json()
    assert len(merged["versions_list"]) == 2
    assert set(merged["tags"]) == {"tag-a", "tag-b"}
    assert "图 B备注" in merged["notes"]
    assert len(merged["links"]) == 1
    client.post("/api/assets/" + second + "/preferred")
    assert client.get("/api/assets").json()["items"][0]["id"] == second
    client.post("/api/assets/" + second + "/ungroup")
    assert client.get("/api/assets").json()["total"] == 2
    assert client.get("/api/assets/" + second).json()["links"][0]["path"] == str(script)


def test_backup_restore_to_empty_data_dir_and_bad_checksum(workspace, tmp_path):
    client, library, _ = workspace
    asset_id, _ = upload(client, png(), name="figure.png")
    client.put(
        "/api/assets/" + asset_id,
        json={"title": "待恢复的图", "tags": ["重要"], "notes": "保留备注"},
    )
    name = library.backup()
    archive_path = library.data_dir / "backups" / name
    with zipfile.ZipFile(archive_path) as archive:
        assert set(archive.namelist()) == {"library.sqlite3", "manifest.json"}
        assert json.loads(archive.read("manifest.json"))["includes_originals"] is False
    fresh = Library(tmp_path / "fresh")
    shutil.copy(archive_path, fresh.data_dir / "backups" / name)
    result = fresh.restore(name)
    assert (fresh.data_dir / "backups" / result["safety_backup"]).is_file()
    assert fresh.one("SELECT title FROM figures")["title"] == "待恢复的图"
    assert fresh.one("SELECT notes FROM figures")["notes"] == "保留备注"
    assert not list(
        (fresh.data_dir / "originals").iterdir()
    )  # Metadata-only scope is explicit.
    assert fresh.one("SELECT path FROM roots WHERE id='managed'")["path"] == str(
        fresh.data_dir / "originals"
    )
    assert client.get("/api/assets/" + asset_id + "/download").content == png()
    broken = library.data_dir / "backups" / "figtrace-corrupt.zip"
    with zipfile.ZipFile(archive_path) as src, zipfile.ZipFile(broken, "w") as out:
        out.writestr("library.sqlite3", src.read("library.sqlite3"))
        out.writestr("manifest.json", json.dumps({"version": 1, "sha256": "wrong"}))
    assert client.post("/api/backups/figtrace-corrupt.zip/restore").status_code == 400
    assert client.get("/api/assets/" + asset_id).json()["title"] == "待恢复的图"


def test_cloud_auth_csrf_and_root_scope(tmp_path):
    allowed = tmp_path / "allowed"
    forbidden = tmp_path / "forbidden"
    allowed.mkdir()
    forbidden.mkdir()
    app = create_app(
        tmp_path / "data",
        start_worker=False,
        password="test-only-password",
        allowed_roots=[allowed],
        local_mode=False,
    )
    with TestClient(app, headers=HEADERS) as client:
        assert client.get("/api/overview").status_code == 401
        assert client.post("/api/login", json={"password": "bad"}).status_code == 401
        assert (
            client.post(
                "/api/login", json={"password": "test-only-password"}
            ).status_code
            == 200
        )
        assert client.get("/api/overview").status_code == 200
        assert (
            client.post("/api/roots", json={"path": str(forbidden)}).status_code == 400
        )
        assert (
            client.post(
                "/api/roots",
                json={"path": str(allowed)},
                headers={"Origin": "https://evil.example"},
            ).status_code
            == 403
        )
        assert (
            client.get("/api/overview", headers={"Host": "evil.example"}).status_code
            == 400
        )
        assert client.post("/api/roots", json={"path": str(allowed)}).status_code == 200
        assert client.post("/api/logout").status_code == 200
        assert client.get("/api/overview").status_code == 401
    with TestClient(app) as no_header:
        assert (
            no_header.post(
                "/api/login", json={"password": "test-only-password"}
            ).status_code
            == 403
        )


def test_symlink_escape_never_indexed(workspace, tmp_path):
    client, library, sources = workspace
    outside = tmp_path / "outside.png"
    outside.write_bytes(png())
    try:
        (sources / "escape.png").symlink_to(outside)
    except OSError:
        pytest.skip("Symlink creation not available")
    client.post("/api/roots", json={"path": str(sources)})
    drain(library)
    assert client.get("/api/overview").json()["assets"] == 0


def test_bulk_adds_tags_without_erasing_existing_metadata(workspace):
    client, _, _ = workspace
    first, _ = upload(client, png(), tags=["原标签"])
    second, _ = upload(client, png("blue"), project="另一项目", tags=["保留"])
    result = client.post(
        "/api/bulk", json={"asset_ids": [first, second], "tags": ["新标签"]}
    )
    assert result.status_code == 200
    assert set(client.get("/api/assets/" + first).json()["tags"]) == {
        "原标签",
        "新标签",
    }
    assert client.get("/api/assets/" + second).json()["project"] == "另一项目"
    assert (
        client.post(
            "/api/bulk", json={"asset_ids": [first, "missing"], "project": "不应写入"}
        ).status_code
        == 400
    )
    assert client.get("/api/assets/" + first).json()["project"] == "论文"


def test_reupload_repairs_corrupt_managed_blob(workspace):
    client, library, _ = workspace
    data = png()
    first, _ = upload(client, data)
    path = library.asset_path(library.one("SELECT * FROM assets WHERE id=?", (first,)))
    path.write_bytes(b"partial blob from interrupted copy")
    second, _ = upload(client, data, name="copy.png")
    assert client.get("/api/assets/" + second + "/download").content == data


def test_updated_multipage_source_invalidates_cached_pages(workspace):
    client, library, sources = workspace
    original = sources / "pages.tiff"
    Image.new("RGB", (20, 20), "red").save(
        original,
        "TIFF",
        save_all=True,
        append_images=[Image.new("RGB", (20, 20), "blue")],
    )
    root_id = client.post("/api/roots", json={"path": str(sources)}).json()["target"]
    drain(library)
    asset_id = client.get("/api/assets").json()["items"][0]["id"]
    before = client.get("/api/assets/" + asset_id + "/preview?page=1").content
    Image.new("RGB", (30, 30), "green").save(
        original,
        "TIFF",
        save_all=True,
        append_images=[Image.new("RGB", (30, 30), "yellow")],
    )
    library.scan(root_id)
    assert not (library.data_dir / "cache" / (asset_id + "-p1.jpg")).exists()
    drain(library)
    after = client.get("/api/assets/" + asset_id + "/preview?page=1").content
    assert before != after


def test_relocating_root_also_relocates_source_links(workspace):
    client, library, sources = workspace
    (sources / "figure.png").write_bytes(png())
    (sources / "plot.py").write_text("# linked source")
    root_id = client.post("/api/roots", json={"path": str(sources)}).json()["target"]
    drain(library)
    asset_id = client.get("/api/assets").json()["items"][0]["id"]
    client.post(
        "/api/assets/" + asset_id + "/links", json={"path": str(sources / "plot.py")}
    )
    destination = sources.with_name("relocated")
    shutil.move(sources, destination)
    assert (
        client.put("/api/roots/" + root_id, json={"path": str(destination)}).status_code
        == 200
    )
    detail = client.get("/api/assets/" + asset_id).json()
    assert detail["links"][0]["path"] == str(destination / "plot.py")
    assert (
        client.get("/api/links/" + detail["links"][0]["id"] + "/download").status_code
        == 200
    )
