import base64
import json

import httpx
import pytest
from test_library import drain, png, upload

PROTOCOLS = ("openai-completions", "openai-responses", "anthropic-messages")
KEY = "protocol-test-key"
ANSWER = json.dumps(
    {
        "category": "流程与示意图",
        "description": "细胞图",
        "tags": ["细胞"],
        "visible_text": "A",
    },
    ensure_ascii=False,
)


def configure(client, protocol="openai-completions", **extra):
    response = client.put(
        "/api/ai/config/analysis",
        json={
            "base_url": "https://provider.example/v1",
            "model": "vision-test-model",
            "api_key": KEY,
            "protocol": protocol,
            **extra,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def fake_provider(monkeypatch, handler):
    original = httpx.Client
    monkeypatch.setattr(
        "figtrace.features.httpx.Client",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )


def response_body(protocol):
    if protocol == "openai-responses":
        return {
            "status": "completed",
            "output": [
                {
                    "type": "reasoning",
                    "summary": [
                        {"type": "summary_text", "text": "Ignore this reasoning block"}
                    ],
                },
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": ANSWER[:10]},
                        {"type": "output_text", "text": ANSWER[10:]},
                    ],
                },
            ],
        }
    if protocol == "anthropic-messages":
        return {
            "stop_reason": "end_turn",
            "content": [
                {"type": "thinking", "thinking": "Ignore reasoning"},
                {"type": "text", "text": ANSWER},
            ],
        }
    return {"choices": [{"finish_reason": "stop", "message": {"content": ANSWER}}]}


def assert_auth(request, protocol):
    assert request.headers["accept-encoding"] == "identity"
    if protocol == "anthropic-messages":
        assert request.headers["x-api-key"] == KEY
        assert request.headers["anthropic-version"] == "2023-06-01"
        assert "authorization" not in request.headers
    else:
        assert request.headers["authorization"] == "Bearer " + KEY
        assert "x-api-key" not in request.headers
        assert "anthropic-version" not in request.headers


@pytest.mark.parametrize("protocol", PROTOCOLS)
@pytest.mark.parametrize("workflow", ["review", "classification"])
def test_protocol_wire_format_and_classification_result(
    workspace, monkeypatch, protocol, workflow
):
    client, lib, _ = workspace
    asset_id, _ = upload(
        client,
        png(),
        name="private-filename.png",
        project="private-project",
        tags=["人工标签"],
    )
    drain(lib)
    configure(client, protocol)
    preview = (lib.data_dir / "cache" / (asset_id + ".jpg")).read_bytes()
    calls = []

    def handler(request):
        calls.append(request)
        assert_auth(request, protocol)
        assert request.method == "POST"
        body = json.loads(request.content)
        assert body["model"] == "vision-test-model"
        assert "private-filename" not in request.content.decode()
        assert "private-project" not in request.content.decode()
        if protocol == "openai-responses":
            assert request.url.path == "/v1/responses"
            assert body["store"] is False
            assert "JSON" in body["instructions"]
            content = body["input"][0]["content"]
            assert content[0]["type"] == "input_text"
            assert content[1]["type"] == "input_image"
            encoded = content[1]["image_url"].removeprefix("data:image/jpeg;base64,")
            assert "messages" not in body
        elif protocol == "anthropic-messages":
            assert request.url.path == "/v1/messages"
            assert body["max_tokens"] == 4096
            assert "JSON" in body["system"]
            assert len(body["messages"]) == 1
            content = body["messages"][0]["content"]
            assert content[0]["type"] == "image"
            assert content[0]["source"]["type"] == "base64"
            assert content[0]["source"]["media_type"] == "image/jpeg"
            encoded = content[0]["source"]["data"]
            assert content[1]["type"] == "text"
        else:
            assert request.url.path == "/v1/chat/completions"
            assert body["messages"][0]["role"] == "system"
            content = body["messages"][1]["content"]
            encoded = content[1]["image_url"]["url"].removeprefix(
                "data:image/jpeg;base64,"
            )
        assert base64.b64decode(encoded, validate=True) == preview
        return httpx.Response(200, json=response_body(protocol))

    fake_provider(monkeypatch, handler)
    if workflow == "classification":
        queued = client.post(
            "/api/classification/batch", json={"method": "ai", "asset_ids": [asset_id]}
        )
    else:
        queued = client.post("/api/ai/analyze", json={"asset_ids": [asset_id]})
    assert queued.status_code == 200, queued.text
    assert client.app.state.ai_service.run_one()
    task = client.get("/api/ai/tasks").json()[0]
    assert task["status"] == "completed", task
    assert task["result"]["tags"] == ["细胞"]
    assert task["result"]["description"] == "细胞图"
    assert KEY not in json.dumps(task)
    detail = client.get("/api/assets/" + asset_id).json()
    assert detail["tags"] == ["人工标签"]
    if workflow == "classification":
        assert any(
            row["origin"] == "ai" and row["tags"] == ["细胞"]
            for row in detail["classifications"]
        )
        assert client.get("/api/classification/config").json()["used_today"] == 1
    assert len(calls) == 1


def test_legacy_config_defaults_to_chat_completions(workspace, monkeypatch):
    client, lib, _ = workspace
    configure(client)
    config_path = client.app.state.ai_service.config_path
    config = json.loads(config_path.read_text())
    del config["analysis"]["protocol"]
    config_path.write_text(json.dumps(config))
    assert (
        client.get("/api/ai/config").json()["analysis"]["protocol"]
        == "openai-completions"
    )
    asset_id, _ = upload(client, png())
    drain(lib)
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.path == "/v1/chat/completions"
        return httpx.Response(200, json=response_body("openai-completions"))

    fake_provider(monkeypatch, handler)
    client.post("/api/ai/analyze", json={"asset_ids": [asset_id]})
    assert client.app.state.ai_service.run_one()
    assert len(calls) == 1


def test_protocol_change_disables_automatic_and_cancels_queued_calls(
    workspace, monkeypatch
):
    client, lib, _ = workspace
    configure(client)
    client.put(
        "/api/classification/config",
        json={"rules_enabled": True, "auto_ai": True, "daily_limit": 5},
    )
    upload(client, png())
    drain(lib)
    client.app.state.classifier.run_one()
    assert client.get("/api/ai/tasks").json()[0]["status"] == "queued"
    monkeypatch.setattr(
        client.app.state.ai_service,
        "request",
        lambda *args: pytest.fail("Old consent must not authorize a new protocol"),
    )
    saved = configure(client, "anthropic-messages")
    assert saved["analysis"]["protocol"] == "anthropic-messages"
    assert client.get("/api/classification/config").json()["auto_ai"] is False
    assert not client.app.state.ai_service.run_one()
    assert client.get("/api/ai/tasks").json()[0]["status"] == "dismissed"


@pytest.mark.parametrize("protocol", PROTOCOLS)
def test_model_discovery_uses_get_and_protocol_auth(workspace, monkeypatch, protocol):
    client, _, _ = workspace
    configure(client, protocol)
    calls = []

    def handler(request):
        calls.append(request)
        assert_auth(request, protocol)
        assert request.method == "GET"
        assert request.url.path == "/v1/models"
        return httpx.Response(
            200, json={"data": [{"id": "vision-test-model"}], "has_more": True}
        )

    fake_provider(monkeypatch, handler)
    response = client.post("/api/ai/test/analysis")
    assert response.status_code == 200, response.text
    assert response.json()["models"] == ["vision-test-model"]
    assert response.json()["discovery_supported"] is True
    assert len(calls) == 1


@pytest.mark.parametrize("status", [404, 405, 501])
def test_missing_discovery_does_not_trigger_inference(workspace, monkeypatch, status):
    client, _, _ = workspace
    configure(client, "anthropic-messages")
    calls = []
    fake_provider(
        monkeypatch,
        lambda request: (
            calls.append(request) or httpx.Response(status, text="no models route")
        ),
    )
    response = client.post("/api/ai/test/analysis")
    assert response.status_code == 200, response.text
    assert response.json()["discovery_supported"] is False
    assert "未验证" in response.json()["message"]
    assert response.json()["models"] == []
    assert [(request.method, request.url.path) for request in calls] == [
        ("GET", "/v1/models")
    ]


@pytest.mark.parametrize("status", [401, 403])
def test_discovery_auth_failure_is_reported_without_leaking_key(
    workspace, monkeypatch, status
):
    client, _, _ = workspace
    configure(client, "anthropic-messages")
    fake_provider(
        monkeypatch, lambda request: httpx.Response(status, text="Rejected key: " + KEY)
    )
    response = client.post("/api/ai/test/analysis")
    assert response.status_code == 400
    assert "HTTP " + str(status) in response.text
    assert KEY not in response.text


@pytest.mark.parametrize("protocol", PROTOCOLS)
def test_partial_responses_are_not_saved_as_complete_classification(
    workspace, monkeypatch, protocol
):
    client, lib, _ = workspace
    configure(client, protocol)
    asset_id, _ = upload(client, png())
    drain(lib)
    response = response_body(protocol)
    if protocol == "openai-responses":
        response["status"] = "incomplete"
    elif protocol == "anthropic-messages":
        response["stop_reason"] = "max_tokens"
    else:
        response["choices"][0]["finish_reason"] = "length"
    calls = []
    fake_provider(
        monkeypatch,
        lambda request: calls.append(request) or httpx.Response(200, json=response),
    )
    client.post(
        "/api/classification/batch", json={"method": "ai", "asset_ids": [asset_id]}
    )
    assert client.app.state.ai_service.run_one()
    assert not client.app.state.ai_service.run_one()
    task = client.get("/api/ai/tasks").json()[0]
    assert task["status"] == "failed"
    assert "未完成" in task["error"]
    assert not any(
        row["origin"] == "ai"
        for row in client.get("/api/assets/" + asset_id).json()["classifications"]
    )
    assert len(calls) == 1


def test_generation_remains_openai_images_when_analysis_is_anthropic(
    workspace, monkeypatch
):
    client, _, _ = workspace
    configure(client, "anthropic-messages")
    rejected = client.put(
        "/api/ai/config/generation",
        json={"model": "image-test", "protocol": "anthropic-messages"},
    )
    assert rejected.status_code == 400
    saved = client.put(
        "/api/ai/config/generation",
        json={
            "base_url": "https://images.example/v1",
            "model": "image-test",
            "api_key": KEY,
        },
    )
    assert saved.status_code == 200, saved.text
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.path == "/v1/images/generations"
        assert_auth(request, "openai-completions")
        assert json.loads(request.content)["model"] == "image-test"
        return httpx.Response(
            200, json={"data": [{"b64_json": base64.b64encode(png()).decode()}]}
        )

    fake_provider(monkeypatch, handler)
    queued = client.post(
        "/api/ai/generate",
        json={"request_id": "image-protocol-request-001", "prompt": "测试图"},
    )
    assert queued.status_code == 200, queued.text
    assert client.app.state.ai_service.run_one()
    assert client.get("/api/ai/tasks").json()[0]["status"] == "completed"
    assert len(calls) == 1
