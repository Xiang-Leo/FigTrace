import io
import json
import zipfile
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from test_library import HEADERS, drain, png, upload

from figtrace.app import create_app
from figtrace.svg import sanitize_svg

SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="160" height="100"><rect x="10" y="10" width="140" height="80" fill="#4477aa"/><text x="20" y="55">Figure A</text></svg>'
EDITED_SVG = SVG.replace(b"Figure A", b"Figure B")
TOKEN = "autofigure-test-private-token"
MODEL_KEY = "autofigure-test-model-secret"
SAM_KEY = "autofigure-test-sam-secret"
SECRETS = (TOKEN, MODEL_KEY, SAM_KEY)


def configure(client, **extra):
    response = client.put(
        "/api/autofigure/config",
        json={
            "enabled": True,
            "base_url": "http://127.0.0.1:8788",
            "token": TOKEN,
            "provider": "custom",
            "svg_model": "test-svg-model",
            "model_base_url": "https://model.example/v1",
            "api_key": MODEL_KEY,
            "sam_backend": "fal",
            "sam_api_key": SAM_KEY,
            **extra,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def fake_provider(monkeypatch, handler):
    original = httpx.Client
    monkeypatch.setattr(
        "figtrace.autofigure.httpx.Client",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )


def remote_handler(calls, *, svg=SVG, fail_run=False, fail_poll=False):
    def handler(request):
        calls.append(request)
        assert request.headers["authorization"] == "Bearer " + TOKEN
        path = request.url.path
        if path == "/healthz":
            assert request.method == "GET"
            return httpx.Response(200, json={"status": "ok"})
        if path == "/api/upload":
            assert request.method == "POST"
            assert "multipart/form-data" in request.headers["content-type"]
            assert b'name="file"' in request.content
            return httpx.Response(200, json={"path": "uploads/test.png"})
        if path == "/api/run":
            assert request.method == "POST"
            body = json.loads(request.content)
            assert body["input_figure_path"] == "uploads/test.png"
            assert body["provider"] == "custom"
            assert body["svg_model"] == "test-svg-model"
            assert body["base_url"] == "https://model.example/v1"
            assert body["api_key"] == MODEL_KEY
            assert body["sam_backend"] == "fal"
            assert body["sam_api_key"] == SAM_KEY
            assert body["enable_upscale"] is False
            if fail_run:
                raise httpx.ReadTimeout(
                    "The submission response was lost", request=request
                )
            return httpx.Response(200, json={"job_id": "job-123"})
        if path == "/api/history/job-123":
            assert request.method == "GET"
            if fail_poll:
                return httpx.Response(503, text="temporarily unavailable")
            return httpx.Response(
                200,
                json={
                    "job_id": "job-123",
                    "status": "complete",
                    "artifacts": [{"kind": "final_svg", "path": "final.svg"}],
                },
            )
        if path == "/api/artifacts/job-123/final.svg":
            assert request.method == "GET"
            return httpx.Response(
                200, content=svg, headers={"content-type": "image/svg+xml"}
            )
        pytest.fail(f"Unexpected AutoFigure request: {request.method} {path}")

    return handler


def queue(client, asset_id, request_id="autofigure-request-0001"):
    response = client.post(
        "/api/autofigure/tasks",
        json={"asset_id": asset_id, "request_id": request_id},
    )
    assert response.status_code == 200, response.text
    return response.json()["task_id"]


def task(client, task_id, asset_id):
    response = client.get("/api/autofigure/tasks", params={"asset_id": asset_id})
    assert response.status_code == 200, response.text
    return next(row for row in response.json() if row["id"] == task_id)


def complete(client, lib, asset_id):
    task_id = queue(client, asset_id)
    service = client.app.state.autofigure_service
    assert service.run_one()
    assert service.run_one()
    assert task(client, task_id, asset_id)["status"] == "completed"
    drain(lib)
    detail = client.get("/api/assets/" + asset_id).json()
    versions = [row for row in detail["versions_list"] if row["id"] != asset_id]
    assert len(versions) == 1
    return task_id, versions[0]["id"]


def test_disabled_by_default_and_connection_check_never_submits(workspace, monkeypatch):
    client, _, _ = workspace
    config = client.get("/api/autofigure/config").json()
    assert config["enabled"] is False
    assert not config["has_token"]
    asset_id, _ = upload(client, png())
    assert (
        client.post(
            "/api/autofigure/tasks",
            json={"asset_id": asset_id, "request_id": "disabled-request-001"},
        ).status_code
        == 400
    )
    calls = []
    fake_provider(monkeypatch, remote_handler(calls))
    configure(client)
    response = client.post("/api/autofigure/test")
    assert response.status_code == 200, response.text
    assert [(request.method, request.url.path) for request in calls] == [
        ("GET", "/healthz")
    ]


def test_tokens_are_private_excluded_from_backup_and_clearable(workspace):
    client, lib, _ = workspace
    result = configure(client)
    assert result["has_token"]
    assert result["has_api_key"]
    assert result["has_sam_api_key"]
    for secret in SECRETS:
        assert secret not in json.dumps(result)
        assert secret not in client.get("/api/autofigure/config").text
    with zipfile.ZipFile(lib.data_dir / "backups" / lib.backup()) as archive:
        assert set(archive.namelist()) == {"manifest.json", "library.sqlite3"}
        for secret in SECRETS:
            assert secret.encode() not in archive.read("library.sqlite3")
    response = client.put(
        "/api/autofigure/config",
        json={
            "enabled": True,
            "base_url": "http://127.0.0.1:8788",
            "clear_token": True,
            "clear_api_key": True,
            "clear_sam_api_key": True,
        },
    )
    assert response.status_code == 200, response.text
    assert not response.json()["has_token"]
    assert not response.json()["has_api_key"]
    assert not response.json()["has_sam_api_key"]


def test_changing_service_destination_clears_all_secrets(workspace):
    client, _, _ = workspace
    configure(client)
    response = client.put(
        "/api/autofigure/config",
        json={
            "enabled": True,
            "base_url": "https://other-service.example",
            "provider": "custom",
            "svg_model": "test-svg-model",
            "model_base_url": "https://model.example/v1",
            "sam_backend": "fal",
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()
    for name in ("token", "api_key", "sam_api_key"):
        assert not result["has_" + name]


@pytest.mark.parametrize("missing", ["api_key", "sam_api_key"])
def test_missing_inference_credentials_prevents_submission(
    workspace, monkeypatch, missing
):
    client, _, _ = workspace
    asset_id, _ = upload(client, png())
    configure(client, **{missing: ""})
    monkeypatch.setattr(
        client.app.state.autofigure_service,
        "request",
        lambda *args, **kwargs: pytest.fail("Incomplete settings must not send data"),
    )
    response = client.post(
        "/api/autofigure/tasks",
        json={"asset_id": asset_id, "request_id": "missing-key-request-001"},
    )
    assert response.status_code == 400, response.text
    assert client.get("/api/autofigure/tasks").json() == []


def test_conversion_preserves_original_project_and_adds_draft_version(
    workspace, monkeypatch
):
    client, lib, _ = workspace
    data = png()
    asset_id, _ = upload(client, data, project="论文", tags=["原有标签"])
    drain(lib)
    assert (
        client.put(
            "/api/assets/" + asset_id,
            json={
                "title": "待转换的科研图",
                "project": "论文",
                "tags": ["原有标签"],
                "stage": "final",
            },
        ).status_code
        == 200
    )
    original = client.get("/api/assets/" + asset_id).json()
    calls = []
    fake_provider(monkeypatch, remote_handler(calls))
    configure(client)
    task_id, result_id = complete(client, lib, asset_id)
    result = client.get("/api/assets/" + result_id).json()
    assert result["figure_id"] == original["figure_id"]
    assert result["project"] == "论文"
    assert result["tags"] == ["原有标签"]
    assert result["stage"] == "draft"
    assert result["format"] == "svg"
    assert result["derivations"][0]["parent_asset_id"] == asset_id
    assert result["derivations"][0]["kind"] == "autofigure"
    assert result["preview"] == "ready"
    assert client.get("/api/assets/" + result_id + "/preview").status_code == 200
    assert client.get("/api/assets/" + asset_id + "/download").content == data
    for secret in SECRETS:
        assert secret not in client.get("/api/autofigure/tasks").text
        assert secret not in json.dumps(result)
    with zipfile.ZipFile(lib.data_dir / "backups" / lib.backup()) as archive:
        for secret in SECRETS:
            assert secret.encode() not in archive.read("library.sqlite3")
    assert task(client, task_id, asset_id)["status"] == "completed"
    assert [request.url.path for request in calls] == [
        "/api/upload",
        "/api/run",
        "/api/history/job-123",
        "/api/artifacts/job-123/final.svg",
    ]


def test_conversion_request_idempotency_survives_completion(workspace, monkeypatch):
    client, lib, _ = workspace
    asset_id, _ = upload(client, png())
    drain(lib)
    calls = []
    fake_provider(monkeypatch, remote_handler(calls))
    configure(client)
    first = queue(client, asset_id)
    assert queue(client, asset_id) == first
    service = client.app.state.autofigure_service
    service.run_one()
    service.run_one()
    assert queue(client, asset_id) == first
    assert not service.run_one()
    assert sum(request.url.path == "/api/run" for request in calls) == 1
    other, _ = upload(client, png("blue"))
    response = client.post(
        "/api/autofigure/tasks",
        json={"asset_id": other, "request_id": "autofigure-request-0001"},
    )
    assert response.status_code in (400, 409)


def test_uncertain_submission_is_not_automatically_retried(workspace, monkeypatch):
    client, lib, _ = workspace
    asset_id, _ = upload(client, png())
    drain(lib)
    calls = []
    fake_provider(monkeypatch, remote_handler(calls, fail_run=True))
    configure(client)
    task_id = queue(client, asset_id)
    service = client.app.state.autofigure_service
    assert service.run_one()
    assert task(client, task_id, asset_id)["status"] == "failed"
    assert not service.run_one()
    assert queue(client, asset_id) == task_id
    client.post("/api/autofigure/tasks/" + task_id + "/refresh")
    assert sum(request.url.path == "/api/run" for request in calls) == 1
    assert sum(request.url.path == "/api/upload" for request in calls) == 1


def test_failed_poll_can_refresh_without_resubmitting(workspace, monkeypatch):
    client, lib, _ = workspace
    asset_id, _ = upload(client, png())
    drain(lib)
    calls = []
    fake_provider(monkeypatch, remote_handler(calls, fail_poll=True))
    configure(client)
    task_id = queue(client, asset_id)
    service = client.app.state.autofigure_service
    service.run_one()
    service.run_one()
    assert task(client, task_id, asset_id)["status"] == "failed"
    # Change the mock handler, without wrapping the already patched constructor.
    monkeypatch.undo()
    fake_provider(monkeypatch, remote_handler(calls))
    response = client.post("/api/autofigure/tasks/" + task_id + "/refresh")
    assert response.status_code == 200, response.text
    service.run_one()
    assert task(client, task_id, asset_id)["status"] == "completed"
    assert sum(request.url.path == "/api/run" for request in calls) == 1
    assert sum(request.url.path == "/api/upload" for request in calls) == 1


def test_svg_editor_saves_new_version_idempotently_without_ai(workspace, monkeypatch):
    client, lib, _ = workspace
    asset_id, _ = upload(client, SVG, name="editable.svg")
    drain(lib)
    monkeypatch.setattr(
        client.app.state.autofigure_service,
        "request",
        lambda *args, **kwargs: pytest.fail("Local editing must not call AutoFigure"),
    )
    source = client.get("/api/autofigure/assets/" + asset_id + "/svg")
    assert source.status_code == 200, source.text
    assert "Figure A" in source.json()["svg"]
    body = {"request_id": "svg-edit-request-0001", "svg": EDITED_SVG.decode()}
    response = client.put("/api/autofigure/assets/" + asset_id + "/svg", json=body)
    assert response.status_code == 200, response.text
    result_id = response.json()["asset_id"]
    assert result_id != asset_id
    assert (
        client.put("/api/autofigure/assets/" + asset_id + "/svg", json=body).json()[
            "asset_id"
        ]
        == result_id
    )
    result = client.get("/api/assets/" + result_id).json()
    original = client.get("/api/assets/" + asset_id).json()
    assert result["figure_id"] == original["figure_id"]
    assert result["project"] == original["project"]
    assert result["derivations"][0]["parent_asset_id"] == asset_id
    assert len(result["versions_list"]) == 2
    assert client.get("/api/assets/" + asset_id + "/download").content == SVG
    assert b"Figure B" in client.get("/api/assets/" + result_id + "/download").content
    changed = client.put(
        "/api/autofigure/assets/" + asset_id + "/svg",
        json={**body, "svg": SVG.decode()},
    )
    assert changed.status_code == 400
    assert len(client.get("/api/assets/" + asset_id).json()["versions_list"]) == 2


def test_remote_artifact_urls_are_not_followed(workspace, monkeypatch):
    client, lib, _ = workspace
    asset_id, _ = upload(client, png())
    drain(lib)
    calls = []
    normal = remote_handler(calls)

    def handler(request):
        if request.url.path == "/api/history/job-123":
            calls.append(request)
            return httpx.Response(
                200,
                json={
                    "status": "complete",
                    "artifacts": [
                        {
                            "kind": "final_svg",
                            "path": "../../credentials.json",
                            "url": "https://untrusted.example/leak",
                        }
                    ],
                },
            )
        return normal(request)

    fake_provider(monkeypatch, handler)
    configure(client)
    complete(client, lib, asset_id)
    assert all(request.url.host == "127.0.0.1" for request in calls)
    assert calls[-1].url.path == "/api/artifacts/job-123/final.svg"


def test_changed_source_is_not_uploaded_using_stale_task(workspace, monkeypatch):
    client, lib, sources = workspace
    source = sources / "linked.png"
    source.write_bytes(png())
    lib.add_root(str(sources))
    drain(lib)
    asset = lib.one("SELECT * FROM assets WHERE name='linked.png'")
    configure(client)
    task_id = queue(client, asset["id"])
    source.write_bytes(png("blue"))
    monkeypatch.setattr(
        client.app.state.autofigure_service,
        "request",
        lambda *args, **kwargs: pytest.fail("Changed source must not be sent"),
    )
    assert client.app.state.autofigure_service.run_one()
    assert task(client, task_id, asset["id"])["status"] == "failed"


@pytest.mark.parametrize(
    "unsafe",
    [
        '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
        '<svg xmlns="http://www.w3.org/2000/svg"><image href="https://example.com/tracker.png"/></svg>',
        '<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"></svg>',
    ],
)
def test_svg_editor_rejects_active_or_external_content(workspace, unsafe):
    client, lib, _ = workspace
    asset_id, _ = upload(client, SVG, name="editable.svg")
    drain(lib)
    before = client.get("/api/overview").json()["assets"]
    response = client.put(
        "/api/autofigure/assets/" + asset_id + "/svg",
        json={"request_id": "unsafe-edit-request-0001", "svg": unsafe},
    )
    assert response.status_code == 400, response.text
    assert client.get("/api/overview").json()["assets"] == before


def test_identical_svg_does_not_reassign_another_figures_asset(workspace, monkeypatch):
    client, lib, _ = workspace
    existing_id, _ = upload(
        client,
        sanitize_svg(SVG).encode(),
        name="already-present.svg",
        project="保留项目",
    )
    source_id, _ = upload(client, png(), project="转换项目")
    drain(lib)
    existing = client.get("/api/assets/" + existing_id).json()
    calls = []
    fake_provider(monkeypatch, remote_handler(calls))
    configure(client)
    _, converted_id = complete(client, lib, source_id)
    assert converted_id != existing_id
    retained = client.get("/api/assets/" + existing_id).json()
    assert retained["figure_id"] == existing["figure_id"]
    assert retained["project"] == "保留项目"
    assert len(retained["versions_list"]) == 1
    converted = client.get("/api/assets/" + converted_id).json()
    assert converted["project"] == "转换项目"
    assert converted["sha256"] == retained["sha256"]


def test_restore_and_interrupted_submission_do_not_repeat_external_work(
    workspace, monkeypatch
):
    client, lib, _ = workspace
    asset_id, _ = upload(client, png())
    drain(lib)
    configure(client)
    task_id = queue(client, asset_id)
    stale_task = lib.one("SELECT * FROM autofigure_tasks WHERE id=?", (task_id,))
    backup = lib.backup()
    calls = []
    fake_provider(monkeypatch, remote_handler(calls))
    lib.restore(backup)
    # Simulate a worker that selected its next row immediately before restore.
    client.app.state.autofigure_service.process(stale_task)
    assert not client.app.state.autofigure_service.run_one()
    assert task(client, task_id, asset_id)["status"] == "failed"
    assert calls == []
    monkeypatch.undo()
    interrupted_id = queue(client, asset_id, request_id="interrupted-request-0002")
    # A crash after starting submission but before persisting its remote id has
    # an ambiguous billing outcome, so this task must never replay on restart.
    with lib.db() as db:
        db.execute(
            "UPDATE autofigure_tasks SET status='running' WHERE id=?", (interrupted_id,)
        )
    restarted = create_app(lib.data_dir, start_worker=False)
    with TestClient(restarted, headers=HEADERS) as fresh:
        monkeypatch.setattr(
            fresh.app.state.autofigure_service,
            "request",
            lambda *args, **kwargs: pytest.fail(
                "Restart must not repeat ambiguous work"
            ),
        )
        assert not fresh.app.state.autofigure_service.run_one()
        assert task(fresh, interrupted_id, asset_id)["status"] == "failed"


class TrackedStream(httpx.SyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.read_count = 0
        self.closed = False

    def __iter__(self):
        for chunk in self.chunks:
            self.read_count += 1
            yield chunk

    def close(self):
        self.closed = True


def test_transport_requests_identity_and_reads_plain_bytes(workspace, monkeypatch):
    client, _, _ = workspace
    service = client.app.state.autofigure_service
    stream = TrackedStream([b"plain", b" response"])
    calls = []

    def handler(request):
        calls.append(request)
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(200, stream=stream)

    fake_provider(monkeypatch, handler)
    assert service.request(service.config(), "GET", "/healthz") == b"plain response"
    assert len(calls) == 1
    assert stream.closed


def test_transport_rejects_compression_before_reading_body(workspace, monkeypatch):
    client, _, _ = workspace
    service = client.app.state.autofigure_service
    stream = TrackedStream([b"not-even-a-valid-gzip-stream"])
    fake_provider(
        monkeypatch,
        lambda request: httpx.Response(
            200, headers={"Content-Encoding": "gzip"}, stream=stream
        ),
    )
    with pytest.raises(ValueError, match="未压缩"):
        service.request(service.config(), "GET", "/healthz")
    assert stream.read_count == 0
    assert stream.closed


def test_transport_stops_stream_at_response_size_limit(workspace, monkeypatch):
    client, _, _ = workspace
    service = client.app.state.autofigure_service
    stream = TrackedStream([b"abc", b"def", b"must-not-be-read"])
    monkeypatch.setattr("figtrace.autofigure.MAX_RESPONSE", 5)
    fake_provider(monkeypatch, lambda request: httpx.Response(200, stream=stream))
    with pytest.raises(ValueError, match="限制"):
        service.request(service.config(), "GET", "/healthz")
    assert stream.read_count == 2
    assert stream.closed


def test_transport_checks_total_deadline_on_tiny_chunks(workspace, monkeypatch):
    client, _, _ = workspace
    service = client.app.state.autofigure_service
    stream = TrackedStream([b"a", b"b", b"must-not-be-read"])
    ticks = iter([100.0, 101.0, 221.0])
    # Replace only this module's time reference, leaving the TestClient event
    # loop and httpx clocks intact. No wall-clock wait is needed for the test.
    monkeypatch.setattr(
        "figtrace.autofigure.time", SimpleNamespace(monotonic=lambda: next(ticks))
    )
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, stream=stream)

    fake_provider(monkeypatch, handler)
    with pytest.raises(ValueError, match="两分钟"):
        service.request(service.config(), "GET", "/healthz")
    assert stream.read_count == 2
    assert stream.closed
    assert len(calls) == 1


def test_saved_svg_exports_valid_png_with_bounded_dimensions(workspace, monkeypatch):
    client, _, _ = workspace
    source_id, _ = upload(client, SVG, name="source.svg")
    large_svg = EDITED_SVG.replace(
        b'width="160" height="100"', b'width="4000" height="2000"'
    )
    monkeypatch.setattr(
        client.app.state.autofigure_service,
        "request",
        lambda *args, **kwargs: pytest.fail("SVG export must remain local"),
    )
    saved = client.put(
        "/api/autofigure/assets/" + source_id + "/svg",
        json={"request_id": "export-png-request-0001", "svg": large_svg.decode()},
    )
    assert saved.status_code == 200, saved.text
    asset_id = saved.json()["asset_id"]
    response = client.get("/api/autofigure/assets/" + asset_id + "/png")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG\r\n\x1a\n")
    with Image.open(io.BytesIO(response.content)) as image:
        assert image.format == "PNG"
        assert image.size == (1600, 800)
        image.verify()
    assert client.get("/api/assets/" + source_id + "/download").content == SVG
