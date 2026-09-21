"""Optional AutoFigure-Edit HTTP adapter and immutable SVG versions.

Only an explicit task submission can upload a library image. Task polling uses
the persistent history endpoint, avoiding the upstream single-consumer SSE queue.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import FileResponse
from PIL import Image
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .library import uid
from .svg import sanitize_svg

MAX_INPUT = 20 * 1024**2
MAX_RESPONSE = 20 * 1024**2
REQUEST_ID = r"^[a-zA-Z0-9-]{16,80}$"
REMOTE_ID = re.compile(r"^[a-zA-Z0-9_-]{1,120}$")


class ServiceConfig(BaseModel):
    enabled: bool = False
    base_url: str = Field(default="http://127.0.0.1:8788", max_length=1000)
    token: str = Field(default="", max_length=2000)
    clear_token: bool = False
    provider: Literal[
        "custom", "openai_response", "openrouter", "bianxie", "gemini"
    ] = "custom"
    svg_model: str = Field(default="", max_length=200)
    model_base_url: str = Field(default="https://api.openai.com/v1", max_length=1000)
    api_key: str = Field(default="", max_length=2000)
    clear_api_key: bool = False
    sam_backend: Literal["local", "fal", "roboflow"] = "local"
    sam_api_key: str = Field(default="", max_length=2000)
    clear_sam_api_key: bool = False


class TaskInput(BaseModel):
    request_id: str = Field(pattern=REQUEST_ID)
    asset_id: str = Field(min_length=1, max_length=100)


class EditInput(BaseModel):
    request_id: str = Field(pattern=REQUEST_ID)
    svg: str = Field(min_length=1, max_length=MAX_RESPONSE)


class RemoteError(ValueError):
    def __init__(self, status):
        self.status = status
        super().__init__(f"AutoFigure 服务返回 HTTP {status}；请检查服务配置及运行记录")


class AutoFigureService:
    def __init__(self, library):
        self.library = library
        self.config_path = library.data_dir / "autofigure-credentials.json"
        self.config_lock = threading.RLock()
        self.run_lock = threading.RLock()

    def config(self):
        with self.config_lock:
            if self.config_path.exists():
                return ServiceConfig().model_dump() | json.loads(
                    self.config_path.read_text(encoding="utf-8")
                )
            return ServiceConfig().model_dump() | {"revision": ""}

    def public_config(self):
        config = self.config()
        hidden = {
            "token",
            "api_key",
            "sam_api_key",
            "revision",
            "clear_token",
            "clear_api_key",
            "clear_sam_api_key",
        }
        return {k: v for k, v in config.items() if k not in hidden} | {
            "has_" + key: bool(config[key])
            for key in ("token", "api_key", "sam_api_key")
        }

    def save_config(self, body):
        url = body.base_url.strip().rstrip("/")
        parsed = urlsplit(url)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "请输入完整的 AutoFigure 服务地址，不包含密钥、查询参数或片段"
            )
        if parsed.scheme == "http" and parsed.hostname not in (
            "localhost",
            "127.0.0.1",
            "::1",
        ):
            raise ValueError("远程 AutoFigure 服务须使用 HTTPS；本机服务可使用 HTTP")
        model_url = body.model_base_url.strip().rstrip("/")
        model_parts = urlsplit(model_url)
        if (
            model_parts.scheme not in ("https", "http")
            or not model_parts.hostname
            or model_parts.username
            or model_parts.password
            or model_parts.query
            or model_parts.fragment
        ):
            raise ValueError("模型接口地址无效")
        if model_parts.scheme == "http" and model_parts.hostname not in (
            "localhost",
            "127.0.0.1",
            "::1",
        ):
            raise ValueError("远程模型接口须使用 HTTPS")
        with self.library.work_lock, self.config_lock:
            if self.library.query(
                "SELECT id FROM autofigure_tasks WHERE status IN ('queued','running')"
            ):
                raise ValueError("请等待 AutoFigure 任务结束后再修改服务设置")
            old = self.config()
            config = body.model_dump(
                exclude={"clear_token", "clear_api_key", "clear_sam_api_key"}
            ) | {
                "base_url": url,
                "model_base_url": model_url,
                "svg_model": body.svg_model.strip(),
                "revision": uid(),
            }
            for key in ("token", "api_key", "sam_api_key"):
                same_destination = old["base_url"] == url
                if key == "api_key":
                    same_destination = (
                        same_destination
                        and old["model_base_url"] == model_url
                        and old["provider"] == body.provider
                    )
                if key == "sam_api_key":
                    same_destination = (
                        same_destination and old["sam_backend"] == body.sam_backend
                    )
                config[key] = getattr(body, key).strip() or (
                    old[key] if same_destination else ""
                )
                if getattr(body, "clear_" + key):
                    config[key] = ""
                if any(ord(c) < 32 or ord(c) == 127 for c in config[key]):
                    raise ValueError("密钥不能包含控制字符")
            staging = self.config_path.with_suffix(".tmp")
            fd = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as file:
                    json.dump(config, file)
                    file.flush()
                    os.fsync(file.fileno())
                staging.replace(self.config_path)
            finally:
                staging.unlink(missing_ok=True)
        return self.public_config()

    def request(self, config, method, path, **kwargs):
        headers = (
            {"Authorization": "Bearer " + config["token"]} if config["token"] else {}
        )
        headers["Accept-Encoding"] = "identity"
        deadline = time.monotonic() + 120
        with httpx.Client(
            timeout=httpx.Timeout(45, connect=10),
            trust_env=False,
            follow_redirects=False,
        ) as client:
            with client.stream(
                method, config["base_url"] + path, headers=headers, **kwargs
            ) as response:
                if not 200 <= response.status_code < 300:
                    raise RemoteError(response.status_code)
                if (
                    response.headers.get("content-encoding", "identity").lower()
                    != "identity"
                ):
                    raise ValueError("AutoFigure 响应必须使用未压缩传输")
                content = bytearray()
                # No accumulation to a minimum chunk size: check the deadline
                # even when the peer sends tiny pieces. Compression is refused.
                for chunk in response.iter_bytes():
                    if time.monotonic() > deadline:
                        raise ValueError("AutoFigure 响应超过两分钟；未自动重发请求")
                    content.extend(chunk)
                    if len(content) > MAX_RESPONSE:
                        raise ValueError("AutoFigure 产物超过 20 MB 限制")
                return bytes(content)

    def request_json(self, config, method, path, **kwargs):
        result = json.loads(self.request(config, method, path, **kwargs))
        if not isinstance(result, dict):
            raise ValueError("AutoFigure 返回格式无效")
        return result

    def input_data(self, asset):
        path = self.library.asset_path(asset)
        if asset["format"] not in {"jpg", "jpeg", "png", "webp", "bmp", "gif"}:
            raise ValueError(
                "请先选择 PNG、JPEG、WebP、BMP 或 GIF 图片；PDF/TIFF 可先导出所需页面"
            )
        if path.stat().st_size > MAX_INPUT:
            raise ValueError("输入图片不能超过 20 MB")
        with path.open("rb") as file:
            data = file.read(MAX_INPUT + 1)
        if len(data) > MAX_INPUT:
            raise ValueError("输入图片不能超过 20 MB")
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > 40_000_000:
                raise ValueError("输入图片不能超过 4000 万像素")
            image.verify()
        return data

    def enqueue(self, body):
        lib = self.library
        with lib.work_lock:
            old = lib.query(
                "SELECT * FROM autofigure_tasks WHERE id=?", (body.request_id,)
            )
            if old:
                if old[0]["asset_id"] != body.asset_id:
                    raise ValueError("请求编号已用于不同图片")
                return {"task_id": body.request_id}
            config = self.config()
            if not config["enabled"] or not config["svg_model"]:
                raise ValueError("请先在设置中启用 AutoFigure 服务并填写 SVG 模型")
            if not config["api_key"]:
                raise ValueError(
                    "请先填写重建模型的 API 密钥；密钥将发送到所配置的 AutoFigure 服务"
                )
            if config["sam_backend"] != "local" and not config["sam_api_key"]:
                raise ValueError("远程分割服务需要填写 SAM API 密钥")
            asset = lib.one("SELECT * FROM assets WHERE id=?", (body.asset_id,))
            data = self.input_data(asset)
            with lib.db() as db:
                existing = db.execute(
                    "SELECT id FROM autofigure_tasks WHERE asset_id=? AND status IN ('queued','running')",
                    (body.asset_id,),
                ).fetchone()
                if existing:
                    return {"task_id": existing["id"]}
                now = time.time()
                snapshot = {
                    k: config[k]
                    for k in (
                        "base_url",
                        "revision",
                        "provider",
                        "svg_model",
                        "model_base_url",
                        "sam_backend",
                    )
                }
                db.execute(
                    "INSERT INTO autofigure_tasks(id,asset_id,config,input_sha256,created,updated,message) VALUES (?,?,?,?,?,?,?)",
                    (
                        body.request_id,
                        body.asset_id,
                        json.dumps(snapshot),
                        hashlib.sha256(data).hexdigest(),
                        now,
                        now,
                        "等待提交至 AutoFigure",
                    ),
                )
            return {"task_id": body.request_id}

    def task(self, task_id):
        row = self.library.one("SELECT * FROM autofigure_tasks WHERE id=?", (task_id,))
        return {k: v for k, v in row.items() if k not in ("config", "input_sha256")}

    def fail(self, task_id, error):
        if isinstance(error, httpx.TimeoutException):
            message = (
                "连接超时，远端可能已处理或计费；未自动重发。已有远端编号时可检查结果"
            )
        elif isinstance(error, ValueError) and not isinstance(
            error, json.JSONDecodeError
        ):
            message = str(error)[:500]
        else:
            message = "AutoFigure 请求或产物解析失败；请检查服务运行记录，未自动重发"
        for key in ("token", "api_key", "sam_api_key"):
            secret = self.config().get(key, "")
            if secret:
                message = message.replace(secret, "[已隐藏]")
        with self.library.db() as db:
            db.execute(
                "UPDATE autofigure_tasks SET status='failed',error=?,message='',updated=? WHERE id=?",
                (message, time.time(), task_id),
            )

    def run_one(self):
        if not self.run_lock.acquire(blocking=False):
            return False
        try:
            # updated ordering lets multiple remote jobs make progress fairly.
            rows = self.library.query(
                "SELECT * FROM autofigure_tasks WHERE status IN ('queued','running') ORDER BY updated LIMIT 1"
            )
            if not rows:
                return False
            task = rows[0]
            self.process(task)
            return True
        finally:
            self.run_lock.release()

    def process(self, task, *, refresh=False):
        lib = self.library
        try:
            with lib.work_lock:
                # Restore may have invalidated a row selected just before this lock.
                task = lib.one(
                    "SELECT * FROM autofigure_tasks WHERE id=?", (task["id"],)
                )
                if task["status"] == "completed" or (
                    not refresh and task["status"] not in ("queued", "running")
                ):
                    return
                config = self.config()
                snapshot = json.loads(task["config"])
                if not config["enabled"] or config["base_url"] != snapshot["base_url"]:
                    raise ValueError(
                        "AutoFigure 服务已停用或地址改变；请恢复原服务配置后检查已有任务"
                    )
                if not task["remote_id"] and config["revision"] != snapshot["revision"]:
                    raise ValueError("服务设置已改变，请按新设置重新提交")
                if refresh and not task["remote_id"]:
                    raise ValueError(
                        "没有可查询的远端任务编号；请检查服务端记录，不能自动重发"
                    )
                if task["status"] == "completed":
                    return
                with lib.db() as db:
                    db.execute(
                        "UPDATE autofigure_tasks SET status='running',error='',updated=? WHERE id=?",
                        (time.time(), task["id"]),
                    )
            if not task["remote_id"]:
                asset = lib.one("SELECT * FROM assets WHERE id=?", (task["asset_id"],))
                data = self.input_data(asset)
                if hashlib.sha256(data).hexdigest() != task["input_sha256"]:
                    raise ValueError("原图在提交后发生变化，请重新提交")
                ext = asset["format"]
                uploaded = self.request_json(
                    config,
                    "POST",
                    "/api/upload",
                    files={
                        "file": (
                            "input." + ext,
                            data,
                            "image/" + ("jpeg" if ext in {"jpg", "jpeg"} else ext),
                        )
                    },
                )
                path = uploaded.get("path", "")
                if (
                    not isinstance(path, str)
                    or not re.fullmatch(r"uploads/[a-zA-Z0-9_.-]+", path)
                    or ".." in path
                ):
                    raise ValueError("AutoFigure 上传路径无效")
                remote = self.request_json(
                    config,
                    "POST",
                    "/api/run",
                    json={
                        "input_figure_path": path,
                        "provider": snapshot["provider"],
                        "svg_model": snapshot["svg_model"],
                        "enable_upscale": False,
                        "api_key": config["api_key"],
                        "base_url": snapshot["model_base_url"],
                        "sam_backend": snapshot["sam_backend"],
                        "sam_api_key": config["sam_api_key"] or None,
                    },
                )
                remote_id = remote.get("job_id", "")
                if not isinstance(remote_id, str) or not REMOTE_ID.fullmatch(remote_id):
                    raise ValueError(
                        "AutoFigure 未返回有效任务编号；请检查服务记录，未自动重发"
                    )
                with lib.db() as db:
                    db.execute(
                        "UPDATE autofigure_tasks SET remote_id=?,message='远端处理中，可关闭页面稍后查看',updated=? WHERE id=?",
                        (remote_id, time.time(), task["id"]),
                    )
                return
            try:
                history = self.request_json(
                    config, "GET", "/api/history/" + task["remote_id"]
                )
            except RemoteError as error:
                if error.status != 404:
                    raise
                history = {}
            if history.get("status") != "complete":
                if time.time() - task["created"] > 3600:
                    raise ValueError(
                        "一小时内未发现完整 SVG；请检查远端日志，完成后可检查结果。未重发推理"
                    )
                with lib.db() as db:
                    db.execute(
                        "UPDATE autofigure_tasks SET message='等待完整 SVG，远端错误请查看服务日志',updated=? WHERE id=?",
                        (time.time(), task["id"]),
                    )
                return
            # Never follow provider-supplied URLs or arbitrary artifact paths.
            svg = self.request(
                config, "GET", "/api/artifacts/" + task["remote_id"] + "/final.svg"
            )
            output = self.save_version(
                task["asset_id"],
                svg,
                "autofigure-" + task["id"],
                kind="autofigure",
                metadata={
                    "remote_id": task["remote_id"],
                    "model": snapshot["svg_model"],
                    "provider": snapshot["provider"],
                    "service": snapshot["base_url"],
                    "input_sha256": task["input_sha256"],
                },
            )
            with lib.work_lock, lib.db() as db:
                db.execute(
                    "UPDATE autofigure_tasks SET status='completed',output_asset_id=?,message='SVG 已保存为新版本',error='',updated=? WHERE id=?",
                    (output, time.time(), task["id"]),
                )
        except Exception as error:
            self.fail(task["id"], error)

    def refresh(self, task_id):
        with self.run_lock:
            task = self.library.one(
                "SELECT * FROM autofigure_tasks WHERE id=?", (task_id,)
            )
            if not task["remote_id"]:
                raise ValueError(
                    "没有可查询的远端任务编号；请检查服务端记录，未重新提交"
                )
            self.process(task, refresh=True)
            return self.task(task_id)

    def save_version(
        self, parent_id, data, request_id, *, kind="svg_edit", metadata=None
    ):
        content = sanitize_svg(data).encode("utf-8")
        digest = hashlib.sha256(content).hexdigest()
        lib = self.library
        with lib.work_lock:
            old = lib.query(
                "SELECT * FROM asset_derivations WHERE request_id=?", (request_id,)
            )
            if old:
                if (
                    old[0]["parent_asset_id"] != parent_id
                    or old[0]["sha256"] != digest
                    or old[0]["kind"] != kind
                ):
                    raise ValueError("保存请求编号已用于不同内容")
                return old[0]["asset_id"]
            parent = lib.one(
                "SELECT a.*,f.project FROM assets a JOIN figures f ON a.figure_id=f.id WHERE a.id=?",
                (parent_id,),
            )
            asset_id = uid()
            destination = lib.data_dir / "originals" / (digest + ".svg")
            if not destination.exists():
                staging = destination.with_name(uid() + ".partial")
                try:
                    staging.write_bytes(content)
                    staging.replace(destination)
                finally:
                    staging.unlink(missing_ok=True)
            info = destination.stat()
            now = time.time()
            name = Path(parent["name"]).stem[:180] + (
                "-editable.svg" if kind == "autofigure" else "-edited.svg"
            )
            provenance = json.dumps(metadata or {}, ensure_ascii=False)
            with lib.db() as db:
                db.execute(
                    "INSERT INTO assets(id,figure_id,root_id,relative_path,name,format,size,mtime,sha256,version_note,created) VALUES (?,?,'managed',?,?,'svg',?,?,?,?,?)",
                    (
                        asset_id,
                        parent["figure_id"],
                        "autofigure/" + asset_id + "/" + name,
                        name,
                        info.st_size,
                        info.st_mtime,
                        digest,
                        "AutoFigure 重建；需核对科学内容"
                        if kind == "autofigure"
                        else "SVG 编辑副本",
                        now,
                    ),
                )
                db.execute(
                    "INSERT INTO asset_derivations(request_id,asset_id,parent_asset_id,kind,sha256,metadata,created) VALUES (?,?,?,?,?,?,?)",
                    (request_id, asset_id, parent_id, kind, digest, provenance, now),
                )
                db.execute(
                    "UPDATE figures SET stage='draft' WHERE id=?",
                    (parent["figure_id"],),
                )
            lib.enqueue("preview", asset_id)
            return asset_id

    def svg(self, asset_id):
        asset = self.library.one("SELECT * FROM assets WHERE id=?", (asset_id,))
        if asset["format"] != "svg":
            raise ValueError("请选择 SVG 文件")
        path = self.library.asset_path(asset)
        if path.stat().st_size > MAX_RESPONSE:
            raise ValueError("SVG 超过 20 MB 限制")
        with path.open("rb") as file:
            return sanitize_svg(file.read(MAX_RESPONSE + 1))

    def start(self):
        def work():
            while not self.library.stop.is_set():
                self.run_one()
                self.library.stop.wait(3)

        thread = threading.Thread(target=work, name="figtrace-autofigure", daemon=True)
        self.library.threads.append(thread)
        thread.start()


def register_autofigure(app, lib, service):
    @app.get("/api/autofigure/config")
    def config():
        return service.public_config()

    @app.put("/api/autofigure/config")
    def save_config(body: ServiceConfig):
        return service.save_config(body)

    @app.post("/api/autofigure/test")
    def test_connection():
        try:
            response = service.request_json(service.config(), "GET", "/healthz")
            if response.get("status") != "ok":
                raise ValueError("服务未返回 AutoFigure 健康状态")
            return {"ok": True}
        except (httpx.HTTPError, json.JSONDecodeError):
            raise ValueError("无法连接 AutoFigure 服务，请检查地址和网络") from None

    @app.post("/api/autofigure/tasks")
    def submit(body: TaskInput):
        return service.enqueue(body)

    @app.get("/api/autofigure/tasks")
    def tasks(asset_id: str | None = None):
        rows = lib.query(
            "SELECT id FROM autofigure_tasks"
            + (" WHERE asset_id=?" if asset_id else "")
            + " ORDER BY created DESC LIMIT 100",
            (asset_id,) if asset_id else (),
        )
        return [service.task(row["id"]) for row in rows]

    @app.post("/api/autofigure/tasks/{task_id}/refresh")
    def refresh(task_id: str):
        return service.refresh(task_id)

    @app.get("/api/autofigure/assets/{asset_id}/svg")
    def svg(asset_id: str):
        return {"svg": service.svg(asset_id)}

    @app.put("/api/autofigure/assets/{asset_id}/svg")
    async def save_svg(asset_id: str, request: Request):
        # Bound streaming request before Pydantic/JSON allocates a large document.
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > MAX_RESPONSE * 2:
                raise HTTPException(413, "SVG 保存请求过大")
        try:
            body = EditInput.model_validate_json(raw)
        except ValueError:
            raise HTTPException(422, "SVG 保存请求格式无效") from None
        def persist():
            service.svg(asset_id)
            return {
                "asset_id": service.save_version(
                    asset_id, body.svg, "edit-" + body.request_id
                )
            }

        return await run_in_threadpool(persist)

    @app.get("/api/autofigure/assets/{asset_id}/png")
    def export_png(asset_id: str):
        content = service.svg(asset_id)
        digest = hashlib.sha256(content.encode()).hexdigest()
        destination = lib.data_dir / "cache" / (digest + ".png")
        if not destination.exists():
            with tempfile.TemporaryDirectory(dir=lib.data_dir / "cache") as folder:
                source, output = Path(folder) / "input.svg", Path(folder) / "output.png"
                source.write_text(content, encoding="utf-8")
                try:
                    result = subprocess.run(
                        [
                            sys.executable,
                            "-m",
                            "figtrace.svg",
                            str(source),
                            str(output),
                        ],
                        capture_output=True,
                        timeout=45,
                    )
                except subprocess.TimeoutExpired:
                    raise ValueError("SVG 转换超时") from None
                if result.returncode or not output.exists():
                    raise ValueError("无法生成 PNG，请检查 SVG 内容")
                output.replace(destination)
        return FileResponse(
            destination,
            media_type="image/png",
            filename="figure-" + asset_id[:8] + ".png",
        )
