from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .library import Library, uid
from .features import AIService, register_features


class Login(BaseModel):
    password: str


class RootInput(BaseModel):
    path: str
    project: str = Field(default="", max_length=200)
    allow_remote: bool = False


class UploadInput(BaseModel):
    relative_path: str
    size: int
    sha256: str
    project: str = Field(default="", max_length=200)
    tags: list[str] = Field(default_factory=list, max_length=100)


class Metadata(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    project: str = Field(default="", max_length=200)
    tags: list[str] = Field(default_factory=list, max_length=100)
    notes: str = Field(default="", max_length=10000)
    version_note: str = Field(default="", max_length=1000)
    stage: Literal["draft", "review", "final"] | None = None


class GroupInput(BaseModel):
    asset_ids: list[str] = Field(min_length=2, max_length=100)


class BulkInput(BaseModel):
    stage: Literal["draft", "review", "final"] | None = None
    asset_ids: list[str] = Field(min_length=1, max_length=100)
    project: str | None = Field(default=None, max_length=200)
    tags: list[str] = Field(default_factory=list, max_length=100)


class LinkInput(BaseModel):
    path: str
    label: str = Field(default="", max_length=300)


class BackupInput(BaseModel):
    enabled: bool = True
    interval_hours: int = Field(default=24, ge=1, le=8760)
    keep: int = Field(default=7, ge=1, le=100)
    directory: str


def default_data_dir():
    if os.getenv("FIGTRACE_DATA_DIR"):
        return Path(os.environ["FIGTRACE_DATA_DIR"])
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "FigTrace"
    if sys.platform == "win32":
        return Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / "FigTrace"
    return (
        Path(os.getenv("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
        / "figtrace"
    )


def create_app(
    data_dir: Path | None = None,
    *,
    start_worker=True,
    password=None,
    allowed_roots=None,
    local_mode=True,
):
    library = Library(data_dir or default_data_dir(), allowed_roots)
    ai_service = AIService(library)
    password = password if password is not None else os.getenv("FIGTRACE_PASSWORD", "")
    if not local_mode and (not password or allowed_roots is None):
        raise ValueError("远程服务必须配置 FIGTRACE_PASSWORD 和 FIGTRACE_ALLOWED_ROOTS")
    session_secret = secrets.token_bytes(32)
    login_attempts: dict[str, list[float]] = {}

    @asynccontextmanager
    async def lifespan(app):
        if start_worker:
            library.start()
            ai_service.start()
        yield
        library.close()

    app = FastAPI(
        title="FigTrace",
        version="0.3.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.library = library
    app.state.ai_service = ai_service
    register_features(app, library, ai_service)

    @app.exception_handler(ValueError)
    async def value_error(_, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(KeyError)
    async def missing(_, exc):
        return JSONResponse({"detail": str(exc).strip("'")}, status_code=404)

    @app.exception_handler(OSError)
    async def filesystem_error(_, exc):
        return JSONResponse({"detail": f"文件操作失败：{exc}"}, status_code=400)

    @app.exception_handler(sqlite3.IntegrityError)
    async def conflict(_, exc):
        return JSONResponse(
            {"detail": "记录已存在或关联冲突，请刷新后重试"}, status_code=409
        )

    def valid_session(token):
        try:
            expires, signature = token.split(".")
            expected = hmac.new(
                session_secret, expires.encode(), hashlib.sha256
            ).hexdigest()
            return int(expires) > time.time() and hmac.compare_digest(
                signature, expected
            )
        except (ValueError, AttributeError):
            return False

    @app.middleware("http")
    async def access(request: Request, call_next):
        if request.url.path.startswith("/api/"):
            origin = request.headers.get("origin")
            if origin and urlsplit(origin).netloc != request.headers.get("host"):
                return JSONResponse({"detail": "拒绝跨站请求"}, status_code=403)
            if (
                request.method not in ("GET", "HEAD", "OPTIONS")
                and request.headers.get("x-figtrace-request") != "1"
            ):
                return JSONResponse({"detail": "请求缺少同源校验标识"}, status_code=403)
            if (
                password
                and request.url.path not in ("/api/session", "/api/login")
                and not valid_session(request.cookies.get("figtrace_session", ""))
            ):
                return JSONResponse({"detail": "请先登录"}, status_code=401)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    hosts = os.getenv("FIGTRACE_HOSTS", "localhost,127.0.0.1,[::1],testserver").split(
        ","
    )
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=[host.strip() for host in hosts]
    )

    @app.get("/api/session")
    def session(request: Request):
        return {
            "authenticated": not password
            or valid_session(request.cookies.get("figtrace_session", "")),
            "password_required": bool(password),
            "local_mode": local_mode,
        }

    @app.post("/api/login")
    def login(body: Login, request: Request):
        host = request.client.host if request.client else "unknown"
        recent = [
            stamp for stamp in login_attempts.get(host, []) if stamp > time.time() - 60
        ]
        if len(recent) >= 10:
            raise HTTPException(429, "尝试次数过多，请一分钟后重试")
        if not password or not hmac.compare_digest(
            body.password.encode(), password.encode()
        ):
            login_attempts[host] = recent + [time.time()]
            raise HTTPException(401, "密码不正确")
        login_attempts.pop(host, None)
        expiry = str(int(time.time()) + 43200)
        token = (
            expiry
            + "."
            + hmac.new(session_secret, expiry.encode(), hashlib.sha256).hexdigest()
        )
        response = JSONResponse({"ok": True})
        response.set_cookie(
            "figtrace_session",
            token,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
            max_age=43200,
        )
        return response

    @app.post("/api/logout")
    def logout():
        response = JSONResponse({"ok": True})
        response.delete_cookie("figtrace_session")
        return response

    @app.get("/api/overview")
    def overview():
        return {
            "assets": library.one("SELECT COUNT(*) AS count FROM assets")["count"],
            "figures": library.one("SELECT COUNT(*) AS count FROM figures")["count"],
            "pending": library.one(
                "SELECT COUNT(*) AS count FROM figures WHERE project='' AND tags='[]'"
            )["count"],
            "projects": [
                r["name"]
                for r in library.query(
                    "SELECT name FROM projects WHERE archived=0 ORDER BY name"
                )
            ],
            "roots": library.query("SELECT * FROM roots ORDER BY kind,name"),
            "active_jobs": library.one(
                "SELECT COUNT(*) AS count FROM jobs WHERE status IN ('queued','running')"
            )["count"],
            "backup": library.setting("backup"),
            "local_mode": local_mode,
            "data_dir": str(library.data_dir),
        }

    @app.get("/api/assets")
    def assets(
        q: str = "",
        project: str | None = None,
        root_id: str | None = None,
        pending: bool = False,
        stage: Literal["draft", "review", "final"] | None = None,
        grouped: bool = True,
        page: int = Query(1, ge=1),
        limit: int = Query(48, ge=1, le=100),
    ):
        params, conditions = [], []
        if q:
            pattern = (
                "%"
                + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                + "%"
            )
            conditions.append(
                "(f.title LIKE ? ESCAPE '\\' OR f.tags LIKE ? ESCAPE '\\' OR f.notes LIKE ? ESCAPE '\\' OR f.project LIKE ? ESCAPE '\\' OR EXISTS(SELECT 1 FROM assets s WHERE s.figure_id=f.id AND (s.name LIKE ? ESCAPE '\\' OR s.relative_path LIKE ? ESCAPE '\\')))"
            )
            params += [pattern] * 6
        if project is not None:
            conditions.append("f.project=?")
            params.append(project)
        if root_id:
            conditions.append(
                "EXISTS(SELECT 1 FROM assets r WHERE r.figure_id=f.id AND r.root_id=?)"
            )
            params.append(root_id)
        if stage is not None:
            conditions.append("f.stage=?")
            params.append(stage)
        if pending:
            conditions.append("f.project='' AND f.tags='[]'")
        if grouped:
            conditions.append(
                "a.id=COALESCE(f.preferred,(SELECT id FROM assets x WHERE x.figure_id=f.id ORDER BY created LIMIT 1))"
            )
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        source = " FROM assets a JOIN figures f ON f.id=a.figure_id"
        count = library.one("SELECT COUNT(*) AS count" + source + where, params)[
            "count"
        ]
        rows = library.query(
            "SELECT a.*,f.title,f.project,f.tags,f.notes,f.stage,f.preferred,(SELECT COUNT(*) FROM assets v WHERE v.figure_id=f.id) AS versions"
            + source
            + where
            + " ORDER BY f.created DESC,a.created DESC LIMIT ? OFFSET ?",
            params + [limit, (page - 1) * limit],
        )
        for row in rows:
            row["tags"] = json.loads(row["tags"])
        return {"items": rows, "total": count, "page": page, "limit": limit}

    @app.get("/api/assets/{asset_id}")
    def detail(asset_id: str):
        asset = library.one(
            "SELECT a.*,f.title,f.project,f.tags,f.notes,f.stage,f.preferred FROM assets a JOIN figures f ON f.id=a.figure_id WHERE a.id=?",
            (asset_id,),
        )
        asset["tags"] = json.loads(asset["tags"])
        asset["versions_list"] = library.query(
            "SELECT * FROM assets WHERE figure_id=? ORDER BY created DESC",
            (asset["figure_id"],),
        )
        asset["links"] = library.query(
            "SELECT * FROM links WHERE asset_id=?", (asset_id,)
        )
        asset["locations"] = library.query(
            "SELECT old_path,new_path,created FROM asset_locations WHERE asset_id=? ORDER BY created DESC LIMIT 50",
            (asset_id,),
        )
        asset["path"] = str(library.asset_path(asset))
        asset["source_path"] = (
            asset["relative_path"].split("/", 1)[1]
            if asset["root_id"] == "managed"
            else asset["relative_path"]
        )
        return asset

    @app.put("/api/assets/{asset_id}")
    def metadata(asset_id: str, body: Metadata):
        with library.work_lock, library.db() as db:
            body.project = library.ensure_project(db, body.project)
            row = db.execute(
                "SELECT figure_id FROM assets WHERE id=?", (asset_id,)
            ).fetchone()
            if not row:
                raise KeyError("图片不存在")
            db.execute(
                "UPDATE figures SET title=?,project=?,tags=?,notes=?,stage=COALESCE(?,stage) WHERE id=?",
                (
                    body.title.strip(),
                    body.project.strip(),
                    json.dumps(
                        list(
                            dict.fromkeys(
                                t.strip()[:100] for t in body.tags if t.strip()
                            )
                        ),
                        ensure_ascii=False,
                    ),
                    body.notes,
                    body.stage,
                    row["figure_id"],
                ),
            )
            db.execute(
                "UPDATE assets SET version_note=? WHERE id=?",
                (body.version_note, asset_id),
            )
        return detail(asset_id)

    @app.post("/api/assets/{asset_id}/preferred")
    def preferred(asset_id: str):
        with library.work_lock, library.db() as db:
            asset = db.execute(
                "SELECT figure_id FROM assets WHERE id=?", (asset_id,)
            ).fetchone()
            if not asset:
                raise KeyError("图片不存在")
            db.execute(
                "UPDATE figures SET preferred=? WHERE id=?",
                (asset_id, asset["figure_id"]),
            )
        return {"ok": True}

    @app.post("/api/group")
    def group(body: GroupInput):
        with library.work_lock, library.db() as db:
            sources = [
                db.execute(
                    "SELECT f.* FROM figures f JOIN assets a ON a.figure_id=f.id WHERE a.id=?",
                    (asset_id,),
                ).fetchone()
                for asset_id in body.asset_ids
            ]
            if any(source is None for source in sources):
                raise ValueError("部分选中图片已不存在")
            figures = {source["id"]: dict(source) for source in sources}
            target = next(iter(figures.values()))
            tags = list(
                dict.fromkeys(
                    tag for fig in figures.values() for tag in json.loads(fig["tags"])
                )
            )
            notes = "\n\n".join(
                f"{fig['title']}\n{fig['notes']}"
                for fig in figures.values()
                if fig["notes"]
            )
            for figure_id in figures:
                if figure_id != target["id"]:
                    db.execute(
                        "UPDATE assets SET figure_id=? WHERE figure_id=?",
                        (target["id"], figure_id),
                    )
                    db.execute("DELETE FROM figures WHERE id=?", (figure_id,))
            db.execute(
                "UPDATE figures SET tags=?,notes=? WHERE id=?",
                (json.dumps(tags, ensure_ascii=False), notes, target["id"]),
            )
        return {"asset_id": body.asset_ids[0]}

    @app.post("/api/bulk")
    def bulk(body: BulkInput):
        with library.work_lock, library.db() as db:
            if body.project is not None:
                body.project = library.ensure_project(db, body.project)
            figures = {}
            for asset_id in body.asset_ids:
                row = db.execute(
                    "SELECT f.* FROM figures f JOIN assets a ON a.figure_id=f.id WHERE a.id=?",
                    (asset_id,),
                ).fetchone()
                if not row:
                    raise ValueError("部分素材已不存在，请刷新")
                figures[row["id"]] = row
            for figure in figures.values():
                tags = list(
                    dict.fromkeys(
                        json.loads(figure["tags"])
                        + [tag.strip()[:100] for tag in body.tags if tag.strip()]
                    )
                )
                project = (
                    body.project.strip()
                    if body.project is not None
                    else figure["project"]
                )
                db.execute(
                    "UPDATE figures SET tags=?,project=?,stage=COALESCE(?,stage) WHERE id=?",
                    (
                        json.dumps(tags, ensure_ascii=False),
                        project,
                        body.stage,
                        figure["id"],
                    ),
                )
        return {"updated": len(figures)}

    @app.post("/api/assets/{asset_id}/ungroup")
    def ungroup(asset_id: str):
        with library.work_lock, library.db() as db:
            asset = db.execute(
                "SELECT a.*,f.title,f.project,f.tags,f.notes,f.stage FROM assets a JOIN figures f ON f.id=a.figure_id WHERE a.id=?",
                (asset_id,),
            ).fetchone()
            if not asset:
                raise KeyError("图片不存在")
            remaining = db.execute(
                "SELECT id FROM assets WHERE figure_id=? AND id<>? ORDER BY created",
                (asset["figure_id"], asset_id),
            ).fetchall()
            if not remaining:
                return {"ok": True}
            new_id = uid()
            db.execute(
                "INSERT INTO figures(id,title,project,tags,notes,preferred,created,stage) VALUES (?,?,?,?,?,?,?,?)",
                (
                    new_id,
                    Path(asset["name"]).stem,
                    asset["project"],
                    asset["tags"],
                    asset["notes"],
                    asset_id,
                    time.time(),
                    asset["stage"],
                ),
            )
            db.execute("UPDATE assets SET figure_id=? WHERE id=?", (new_id, asset_id))
            db.execute(
                "UPDATE figures SET preferred=? WHERE id=? AND preferred=?",
                (remaining[0]["id"], asset["figure_id"], asset_id),
            )
        return {"ok": True}

    @app.get("/api/assets/{asset_id}/preview")
    def image(asset_id: str, page: int = Query(0, ge=0)):
        asset = library.one("SELECT * FROM assets WHERE id=?", (asset_id,))
        if page >= asset["pages"]:
            raise HTTPException(400, "页码超出范围")
        output = (
            library.data_dir
            / "cache"
            / (asset_id + (f"-p{page}" if page else "") + ".jpg")
        )
        if page and not output.exists():
            with library.work_lock:
                process = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "figtrace.preview",
                        str(library.asset_path(asset)),
                        str(output),
                        str(page),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=45,
                )
                result = (
                    json.loads(process.stdout)
                    if process.returncode == 0
                    else {"status": "failed"}
                )
                if result["status"] != "ready":
                    raise HTTPException(422, result.get("error", "分页预览生成失败"))
        if not output.exists() or asset["preview"] not in ("ready", "missing"):
            raise HTTPException(404, "预览尚未就绪")
        return FileResponse(output, media_type="image/jpeg")

    @app.get("/api/assets/{asset_id}/download")
    def download(asset_id: str):
        asset = library.one("SELECT * FROM assets WHERE id=?", (asset_id,))
        path = library.asset_path(asset)
        if not path.is_file():
            raise HTTPException(404, "原文件当前不可访问")
        return FileResponse(
            path, filename=asset["name"], media_type="application/octet-stream"
        )

    @app.post("/api/assets/{asset_id}/retry")
    def retry(asset_id: str):
        library.one("SELECT id FROM assets WHERE id=?", (asset_id,))
        with library.db() as db:
            db.execute(
                "UPDATE assets SET preview='queued',error='' WHERE id=?", (asset_id,)
            )
        return library.enqueue("preview", asset_id)

    @app.post("/api/assets/{asset_id}/links")
    def add_link(asset_id: str, body: LinkInput):
        library.one("SELECT id FROM assets WHERE id=?", (asset_id,))
        path = library.permitted(Path(body.path))
        if not path.is_file():
            raise ValueError("关联文件不存在；请输入后端可访问的完整路径")
        with library.db() as db:
            db.execute(
                "INSERT INTO links VALUES (?,?,?,?)",
                (uid(), asset_id, str(path), body.label.strip() or path.name),
            )
        return {"ok": True}

    @app.get("/api/links/{link_id}/download")
    def download_link(link_id: str):
        link = library.one("SELECT * FROM links WHERE id=?", (link_id,))
        path = library.permitted(Path(link["path"]))
        if not path.is_file():
            raise HTTPException(404, "关联文件当前不可访问")
        return FileResponse(
            path, filename=path.name, media_type="application/octet-stream"
        )

    @app.delete("/api/links/{link_id}")
    def unlink(link_id: str):
        with library.db() as db:
            db.execute("DELETE FROM links WHERE id=?", (link_id,))
        return {"ok": True}

    @app.get("/api/directories")
    def directories(path: str | None = None):
        if not path:
            choices = (
                library.allowed_roots
                if library.allowed_roots is not None
                else [Path.home()]
            )
            return {
                "path": "",
                "parent": None,
                "directories": [{"name": str(p), "path": str(p)} for p in choices],
            }
        base = library.permitted(Path(path))
        if not base.is_dir():
            raise ValueError("目录不存在")
        values = []
        for child in base.iterdir():
            if child.name.startswith(".") or child.is_symlink():
                continue
            try:
                if child.is_dir() and not library.excluded(child.resolve()):
                    values.append({"name": child.name, "path": str(child)})
            except OSError:
                continue
        parent = str(base.parent) if base != base.parent else None
        if library.allowed_roots is not None and not any(
            base.parent.is_relative_to(p) for p in library.allowed_roots
        ):
            parent = None
        return {
            "path": str(base),
            "parent": parent,
            "directories": sorted(values, key=lambda d: d["name"].lower()),
        }

    @app.post("/api/roots")
    def root(body: RootInput):
        return library.add_root(body.path, body.project, body.allow_remote)

    @app.post("/api/roots/{root_id}/scan")
    def scan_root(root_id: str):
        library.one("SELECT id FROM roots WHERE id=? AND kind='linked'", (root_id,))
        return library.enqueue("scan", root_id)

    @app.put("/api/roots/{root_id}")
    def relocate_root(root_id: str, body: RootInput):
        previous = library.one(
            "SELECT path FROM roots WHERE id=? AND kind='linked'", (root_id,)
        )
        path = library.permitted(Path(body.path))
        if not path.is_dir() or library.excluded(path):
            raise ValueError("请选择可访问的素材目录")
        with library.work_lock, library.db() as db:
            if db.execute(
                "SELECT 1 FROM jobs WHERE status='running' AND kind IN ('scan','preview')"
            ).fetchone():
                raise ValueError("扫描或预览正在运行，请稍后重新定位目录")
            previous_path = Path(previous["path"])
            for link in db.execute("SELECT id,path FROM links").fetchall():
                source_path = Path(link["path"])
                if source_path.is_relative_to(previous_path):
                    db.execute(
                        "UPDATE links SET path=? WHERE id=?",
                        (
                            str(path / source_path.relative_to(previous_path)),
                            link["id"],
                        ),
                    )
            db.execute(
                "UPDATE roots SET path=?,name=?,allow_remote=?,last_scan=0 WHERE id=?",
                (str(path), path.name, int(body.allow_remote), root_id),
            )
        return library.enqueue("scan", root_id)

    @app.post("/api/uploads")
    def upload_create(body: UploadInput):
        return library.create_upload(**body.model_dump())

    @app.get("/api/uploads/{upload_id}")
    def upload_status(upload_id: str):
        return library.one("SELECT * FROM uploads WHERE id=?", (upload_id,))

    @app.put("/api/uploads/{upload_id}")
    async def upload_chunk(upload_id: str, request: Request, offset: int = Query(ge=0)):
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > 4 * 1024**2:
                raise HTTPException(413, "单个分片不得超过 4 MB")
            data.extend(chunk)
        return library.append_upload(upload_id, offset, data)

    @app.post("/api/uploads/{upload_id}/complete")
    def upload_complete(upload_id: str):
        return library.complete_upload(upload_id)

    @app.delete("/api/uploads/{upload_id}")
    def upload_cancel(upload_id: str):
        with library.work_lock, library.db() as db:
            row = db.execute(
                "SELECT asset_id FROM uploads WHERE id=?", (upload_id,)
            ).fetchone()
            if row and row["asset_id"]:
                raise ValueError("素材已入库，不能取消已完成的上传")
            (library.data_dir / "uploads" / upload_id).unlink(missing_ok=True)
            db.execute("DELETE FROM uploads WHERE id=?", (upload_id,))
        return {"ok": True}

    @app.get("/api/jobs")
    def jobs():
        return library.query(
            "SELECT * FROM jobs ORDER BY CASE WHEN status IN ('queued','running') THEN 0 ELSE 1 END,created DESC LIMIT 50"
        )

    @app.put("/api/settings/backup")
    def backup_settings(body: BackupInput):
        directory = Path(body.directory).expanduser().resolve()
        if not local_mode:
            # A remote browser may adjust timing, but cannot redirect backup writes arbitrarily.
            configured = (
                Path(library.setting("backup")["directory"]).expanduser().resolve()
            )
            if directory != configured:
                raise ValueError("远程备份目标由服务器配置管理")
        if directory in (
            library.data_dir,
            library.data_dir / "cache",
            library.data_dir / "originals",
            library.data_dir / "uploads",
        ) or directory.is_relative_to(library.data_dir / "originals"):
            raise ValueError("备份目录不能与素材或缓存目录重叠")
        directory.mkdir(parents=True, exist_ok=True)
        config = library.setting("backup")
        config.update(body.model_dump())
        config["directory"] = str(directory)
        library.set_setting("backup", config)
        return config

    @app.get("/api/backups")
    def backups():
        directory = Path(library.setting("backup")["directory"]).expanduser().resolve()
        return [
            {"name": p.name, "size": p.stat().st_size, "created": p.stat().st_mtime}
            for p in sorted(
                directory.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True
            )
            if p.name.startswith(("figtrace-", "before-restore-"))
        ]

    @app.post("/api/backups")
    def backup_now():
        return library.enqueue("backup", "manual")

    @app.get("/api/backups/{name}/download")
    def download_backup(name: str):
        if (
            Path(name).name != name
            or not name.startswith(("figtrace-", "before-restore-"))
            or not name.endswith(".zip")
        ):
            raise ValueError("备份名称无效")
        path = (
            Path(library.setting("backup")["directory"]).expanduser().resolve() / name
        )
        if not path.is_file():
            raise HTTPException(404, "备份不存在")
        return FileResponse(path, filename=name, media_type="application/zip")

    @app.post("/api/backups/{name}/restore")
    def restore(name: str):
        return library.restore(name)

    frontend = Path(
        os.getenv(
            "FIGTRACE_FRONTEND",
            str(Path(__file__).resolve().parent.parent / "frontend" / "dist"),
        )
    )
    if frontend.is_dir():
        app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
    else:

        @app.get("/")
        def missing_frontend():
            return JSONResponse(
                {
                    "message": "请先在 frontend 目录运行 npm install 和 npm run build，再重启服务。"
                }
            )

    return app
