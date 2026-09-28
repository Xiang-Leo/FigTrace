import json
import time

import httpx
import pytest
from test_library import drain, png, upload


def configure(client, *, automatic=False, limit=20, rules=True):
    response = client.put(
        "/api/ai/config/analysis",
        json={"model": "fake-vision", "api_key": "classification-secret"},
    )
    assert response.status_code == 200, response.text
    return settings(client, automatic=automatic, limit=limit, rules=rules)


def settings(client, *, automatic=False, limit=20, rules=True):
    response = client.put(
        "/api/classification/config",
        json={"rules_enabled": rules, "auto_ai": automatic, "daily_limit": limit},
    )
    assert response.status_code == 200, response.text
    return response.json()


def classify(client, method="ai", ids=None, **extra):
    response = client.post(
        "/api/classification/batch",
        json={"method": method, "asset_ids": ids or [], **extra},
    )
    assert response.status_code == 200, response.text
    return response.json()


def records(client, asset, origin=None):
    items = client.get("/api/assets/" + asset).json()["classifications"]
    return [item for item in items if not origin or item["origin"] == origin]


def response(category="统计图表"):
    return {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "category": category,
                            "tags": ["信号强度"],
                            "description": "显示各组信号强度",
                            "visible_text": "A B",
                        }
                    )
                }
            }
        ]
    }


def test_rules_classify_new_imports_without_ai_or_touching_metadata(
    workspace, monkeypatch
):
    client, _, _ = workspace
    monkeypatch.setattr(
        client.app.state.ai_service,
        "request",
        lambda *args: pytest.fail("Rules must not contact a provider"),
    )
    asset, _ = upload(client, png(), name="study/HEATMAP-final.png", tags=["人工标签"])
    record = records(client, asset)[0]
    assert record["origin"] == "rules"
    assert record["category"] == "统计图表"
    assert record["tags"] == ["PNG"]
    assert "未识别图片内容" in record["description"]
    assert client.get("/api/assets/" + asset).json()["tags"] == ["人工标签"]
    assert client.get("/api/ai/tasks").json() == []
    config = client.get("/api/classification/config").json()
    assert config["rules_enabled"] is True
    assert config["auto_ai"] is False
    assert config["daily_limit"] == 20
    assert config["analysis_ready"] is False
    assert not client.app.state.classifier.run_one()


def test_generic_raster_is_not_assumed_to_be_a_photograph(workspace):
    client, _, _ = workspace
    asset, _ = upload(client, png(), name="Barbara.png")
    assert records(client, asset)[0]["category"] == "其他图片"


def test_enabling_auto_only_processes_future_imports_once_preview_is_ready(
    workspace, monkeypatch
):
    client, lib, _ = workspace
    old, _ = upload(client, png(), name="existing.png")
    config = configure(client, automatic=True)
    assert config["analysis_base_url"] == "https://api.openai.com/v1"
    assert config["analysis_model"] == "fake-vision"
    assert "classification-secret" not in json.dumps(config)
    future, _ = upload(client, png("blue"), name="future.png")
    calls = []
    monkeypatch.setattr(
        client.app.state.ai_service,
        "request",
        lambda *args: calls.append(args) or response(),
    )
    assert not client.app.state.classifier.run_one()
    drain(lib)
    assert client.app.state.classifier.run_one()
    assert client.app.state.ai_service.run_one()
    assert not client.app.state.classifier.run_one()
    assert len(calls) == 1
    assert records(client, old, "ai") == []
    assert records(client, future, "ai")[0]["category"] == "统计图表"
    task = client.get("/api/ai/tasks").json()[0]
    assert task["options"]["classification"] is True
    assert task["options"]["automatic"] is True


def test_classification_wire_only_sends_preview_and_does_not_overwrite_metadata(
    workspace, monkeypatch
):
    client, lib, _ = workspace
    asset, _ = upload(
        client,
        png(),
        name="confidential-project/secret-file.png",
        project="保密项目",
        tags=["人工"],
    )
    client.put(
        "/api/assets/" + asset,
        json={
            "title": "人工标题",
            "project": "保密项目",
            "tags": ["人工"],
            "notes": "人工笔记",
        },
    )
    drain(lib)
    configure(client)
    calls = []

    def fake_request(config, path, body):
        calls.append(body)
        assert path == "/chat/completions"
        content = json.dumps(body, ensure_ascii=False)
        assert "confidential-project" not in content
        assert "secret-file" not in content
        assert "保密项目" not in content
        assert "人工笔记" not in content
        assert body["messages"][1]["content"][1]["image_url"]["url"].startswith(
            "data:image/jpeg;base64,"
        )
        assert "统计图表" in body["messages"][0]["content"]
        return response("示意图")

    monkeypatch.setattr(client.app.state.ai_service, "request", fake_request)
    assert classify(client, ids=[asset])["queued"] == 1
    assert client.app.state.ai_service.run_one()
    detail = client.get("/api/assets/" + asset).json()
    assert (detail["title"], detail["tags"], detail["notes"], detail["project"]) == (
        "人工标题",
        ["人工"],
        "人工笔记",
        "保密项目",
    )
    assert records(client, asset, "ai")[0]["category"] == "流程与示意图"
    assert len(calls) == 1


def test_daily_budget_is_rolling_includes_failures_and_limits_manual_batches(
    workspace, monkeypatch
):
    client, lib, _ = workspace
    first, _ = upload(client, png(), name="first.png")
    second, _ = upload(client, png("blue"), name="second.png")
    drain(lib)
    configure(client, limit=1)
    calls = []

    def fail(*args):
        calls.append(args)
        raise httpx.ReadTimeout("ambiguous")

    monkeypatch.setattr(client.app.state.ai_service, "request", fail)
    result = classify(client)
    assert result["queued"] == 1 and result["skipped"] == 1
    client.app.state.ai_service.run_one()
    assert client.get("/api/classification/config").json()["remaining_today"] == 0
    assert classify(client, ids=[first])["queued"] == 0
    assert classify(client, ids=[second])["queued"] == 0
    with lib.db() as db:
        db.execute(
            "UPDATE classification_attempts SET created=?", (time.time() - 86401,)
        )
    assert client.get("/api/classification/config").json()["remaining_today"] == 1
    # The old ambiguous failure is never retried, even after its quota expires.
    assert classify(client, ids=[first])["queued"] == 0
    assert classify(client, ids=[second])["queued"] == 1
    assert len(calls) == 1


@pytest.mark.parametrize("limit", [0, 201])
def test_invalid_daily_budget_rejected(workspace, limit):
    client, _, _ = workspace
    assert (
        client.put(
            "/api/classification/config", json={"daily_limit": limit}
        ).status_code
        == 422
    )


def test_missing_provider_and_batch_limits_fail_without_partial_changes(workspace):
    client, lib, _ = workspace
    assert (
        client.put("/api/classification/config", json={"auto_ai": True}).status_code
        == 400
    )
    assert client.get("/api/classification/config").json()["auto_ai"] is False
    settings(client, rules=False)
    asset, _ = upload(client, png())
    assert records(client, asset) == []
    assert (
        client.post(
            "/api/classification/batch",
            json={"method": "rules", "asset_ids": [asset, "missing"]},
        ).status_code
        == 400
    )
    assert records(client, asset) == []
    assert (
        client.post(
            "/api/classification/batch",
            json={"method": "rules", "asset_ids": [asset] * 101},
        ).status_code
        == 422
    )
    assert lib.query("SELECT * FROM ai_tasks") == []


def test_global_rules_batch_respects_project_and_does_not_reprocess_records(workspace):
    client, _, _ = workspace
    settings(client, rules=False)
    first, _ = upload(client, png(), project="甲", name="flowchart.png")
    second, _ = upload(client, png("blue"), project="乙", name="confocal.png")
    result = classify(client, method="rules", project="甲")
    assert result["processed"] == 1
    assert records(client, first)[0]["category"] == "流程与示意图"
    assert records(client, second) == []
    assert classify(client, method="rules", project="甲")["processed"] == 0
    assert classify(client, method="rules")["processed"] == 1


def test_disabling_auto_cancels_queue_and_running_result_but_preserves_manual_batch(
    workspace, monkeypatch
):
    client, lib, _ = workspace
    configure(client, automatic=True)
    first, _ = upload(client, png(), name="first.png")
    drain(lib)
    client.app.state.classifier.run_one()
    calls = []
    monkeypatch.setattr(
        client.app.state.ai_service,
        "request",
        lambda *args: calls.append(args) or response(),
    )
    settings(client, automatic=False)
    assert not client.app.state.ai_service.run_one()
    assert calls == []
    assert records(client, first, "ai") == []
    settings(client, automatic=True)
    second, _ = upload(client, png("blue"), name="second.png")
    drain(lib)
    client.app.state.classifier.run_one()

    def disable_during_request(*args):
        calls.append(args)
        settings(client, automatic=False)
        return response()

    monkeypatch.setattr(client.app.state.ai_service, "request", disable_during_request)
    assert client.app.state.ai_service.run_one()
    assert records(client, second, "ai") == []
    third, _ = upload(client, png("red"), name="third.png")
    drain(lib)
    assert classify(client, ids=[third])["queued"] == 1
    assert client.app.state.ai_service.run_one()
    assert records(client, third, "ai")[0]["category"] == "统计图表"
    assert len(calls) == 2


def test_provider_save_disables_auto_and_cancels_its_pending_requests(
    workspace, monkeypatch
):
    client, lib, _ = workspace
    configure(client, automatic=True)
    upload(client, png())
    drain(lib)
    client.app.state.classifier.run_one()
    client.put("/api/ai/config/analysis", json={"model": "new-provider-model"})
    assert client.get("/api/classification/config").json()["auto_ai"] is False
    monkeypatch.setattr(
        client.app.state.ai_service,
        "request",
        lambda *args: pytest.fail("Provider change must not send queued images"),
    )
    assert not client.app.state.ai_service.run_one()


def test_reenabling_auto_cannot_revive_a_result_from_cancelled_consent(
    workspace, monkeypatch
):
    client, lib, _ = workspace
    configure(client, automatic=True)
    asset, _ = upload(client, png())
    drain(lib)
    client.app.state.classifier.run_one()

    def toggle_during_request(*args):
        settings(client, automatic=False)
        settings(client, automatic=True)
        return response()

    monkeypatch.setattr(client.app.state.ai_service, "request", toggle_during_request)
    assert client.app.state.ai_service.run_one()
    assert records(client, asset, "ai") == []
    assert client.get("/api/ai/tasks").json()[0]["status"] == "failed"


def test_failed_preview_leaves_auto_queue_without_contacting_provider(
    workspace, monkeypatch
):
    client, lib, _ = workspace
    configure(client, automatic=True)
    upload(client, b"invalid image", name="invalid.png")
    assert client.get("/api/classification/config").json()["pending"] == 1
    monkeypatch.setattr(
        client.app.state.ai_service,
        "request",
        lambda *args: pytest.fail("Failed previews must not be sent"),
    )
    drain(lib)
    assert client.get("/api/classification/config").json()["pending"] == 0
    assert not client.app.state.classifier.run_one()
    assert not client.app.state.ai_service.run_one()


def test_corrected_and_dismissed_classifications_survive_rescans_batches_and_search(
    workspace,
):
    client, _, _ = workspace
    asset, _ = upload(client, png(), name="heatmap.png")
    record = records(client, asset)[0]
    assert (
        client.get("/api/assets", params={"category": "统计图表"}).json()["total"] == 1
    )
    result = client.put(
        "/api/classification/" + record["id"],
        json={
            "category": "显微与实验图",
            "tags": ["用户确认"],
            "description": "荧光信号",
        },
    )
    assert result.status_code == 200
    classify(client, method="rules", ids=[asset])
    edited = records(client, asset)[0]
    assert edited["manual_override"] is True
    assert edited["category"] == "显微与实验图"
    assert client.get("/api/assets", params={"q": "荧光信号"}).json()["total"] == 1
    assert (
        client.get("/api/assets", params={"category": "统计图表"}).json()["total"] == 0
    )
    assert (
        client.post("/api/classification/" + record["id"] + "/dismiss").status_code
        == 200
    )
    assert classify(client, method="rules", ids=[asset])["processed"] == 0
    assert records(client, asset)[0]["status"] == "dismissed"
    assert client.get("/api/assets", params={"q": "荧光信号"}).json()["total"] == 0
    assert client.get("/api/assets").json()["items"][0]["classifications"] == []


def test_meaningful_rules_remove_pending_but_unknown_format_does_not(workspace):
    client, _, _ = workspace
    known, _ = upload(client, png(), name="heatmap.png")
    unknown, _ = upload(client, png("blue"), name="generic.png")
    for asset in (known, unknown):
        client.put(
            "/api/assets/" + asset, json={"title": "test", "project": "", "tags": []}
        )
    assert client.get("/api/overview").json()["pending"] == 1
    pending = client.get("/api/assets", params={"pending": "true"}).json()
    assert pending["total"] == 1
    assert pending["items"][0]["id"] == unknown


def test_existing_manual_analysis_is_reused_without_second_paid_request(
    workspace, monkeypatch
):
    client, lib, _ = workspace
    asset, _ = upload(client, png())
    drain(lib)
    configure(client)
    calls = []
    monkeypatch.setattr(
        client.app.state.ai_service,
        "request",
        lambda *args: calls.append(args) or response(),
    )
    client.post("/api/ai/analyze", json={"asset_ids": [asset]})
    assert classify(client, ids=[asset])["queued"] == 0
    assert client.app.state.ai_service.run_one()
    assert classify(client, ids=[asset])["processed"] == 1
    assert len(calls) == 1
    assert client.get("/api/classification/config").json()["used_today"] == 0


def test_stale_classification_hidden_from_detail_search_and_filters(workspace):
    client, lib, sources = workspace
    path = sources / "heatmap.png"
    path.write_bytes(png())
    lib.add_root(str(sources))
    drain(lib)
    asset = client.get("/api/assets").json()["items"][0]["id"]
    record = records(client, asset)[0]
    client.put(
        "/api/classification/" + record["id"],
        json={"category": "统计图表", "tags": ["过期标签"]},
    )
    path.write_bytes(png("blue", dimensions=(81, 61)))
    lib.scan(lib.one("SELECT id FROM roots WHERE kind='linked'")["id"])
    assert client.get("/api/assets", params={"q": "过期标签"}).json()["total"] == 0
    assert records(client, asset) == []
