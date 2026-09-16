from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

FORMATS = {
    "jpg",
    "jpeg",
    "png",
    "tif",
    "tiff",
    "pdf",
    "ai",
    "eps",
    "psd",
    "webp",
    "bmp",
    "gif",
    "svg",
}
SCHEMA = """
CREATE TABLE IF NOT EXISTS roots(id TEXT PRIMARY KEY,path TEXT NOT NULL UNIQUE,name TEXT NOT NULL,kind TEXT NOT NULL DEFAULT 'linked',project TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'ready',last_scan REAL NOT NULL DEFAULT 0,allow_remote INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS figures(id TEXT PRIMARY KEY,title TEXT NOT NULL,project TEXT NOT NULL DEFAULT '',tags TEXT NOT NULL DEFAULT '[]',notes TEXT NOT NULL DEFAULT '',preferred TEXT,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS assets(id TEXT PRIMARY KEY,figure_id TEXT NOT NULL REFERENCES figures(id),root_id TEXT NOT NULL REFERENCES roots(id),relative_path TEXT NOT NULL,name TEXT NOT NULL,format TEXT NOT NULL,size INTEGER NOT NULL,mtime REAL NOT NULL,sha256 TEXT,preview TEXT NOT NULL DEFAULT 'queued',error TEXT NOT NULL DEFAULT '',width INTEGER,height INTEGER,pages INTEGER NOT NULL DEFAULT 1,mode TEXT NOT NULL DEFAULT '',version_note TEXT NOT NULL DEFAULT '',created REAL NOT NULL,UNIQUE(root_id,relative_path));
CREATE INDEX IF NOT EXISTS assets_figure ON assets(figure_id);
CREATE INDEX IF NOT EXISTS assets_hash ON assets(sha256);
CREATE INDEX IF NOT EXISTS figure_project ON figures(project);
CREATE TABLE IF NOT EXISTS links(id TEXT PRIMARY KEY,asset_id TEXT NOT NULL REFERENCES assets(id),path TEXT NOT NULL,label TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS uploads(id TEXT PRIMARY KEY,relative_path TEXT NOT NULL,size INTEGER NOT NULL,sha256 TEXT NOT NULL,project TEXT NOT NULL,tags TEXT NOT NULL,offset INTEGER NOT NULL DEFAULT 0,asset_id TEXT,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,kind TEXT NOT NULL,target TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'queued',message TEXT NOT NULL DEFAULT '',created REAL NOT NULL,updated REAL NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS unique_active_job ON jobs(kind,target) WHERE status IN ('queued','running');
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,name TEXT NOT NULL UNIQUE,description TEXT NOT NULL DEFAULT '',color TEXT NOT NULL DEFAULT '#6b7c61',archived INTEGER NOT NULL DEFAULT 0,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS ai_tasks(id TEXT PRIMARY KEY,kind TEXT NOT NULL,asset_id TEXT,project_id TEXT,prompt TEXT NOT NULL DEFAULT '',model TEXT NOT NULL,base_url TEXT NOT NULL,config_revision TEXT NOT NULL,options TEXT NOT NULL DEFAULT '{}',status TEXT NOT NULL DEFAULT 'queued',result TEXT NOT NULL DEFAULT '{}',error TEXT NOT NULL DEFAULT '',created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS ai_tasks_asset ON ai_tasks(asset_id);
CREATE TABLE IF NOT EXISTS asset_locations(id TEXT PRIMARY KEY,asset_id TEXT NOT NULL REFERENCES assets(id),old_path TEXT NOT NULL,new_path TEXT NOT NULL,created REAL NOT NULL);
"""


def uid():
    return uuid.uuid4().hex


def fingerprint(path: Path):
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_relative(value: str):
    value = value.replace("\\", "/")
    path = PurePosixPath(value)
    if (
        not value
        or len(value) > 1500
        or path.is_absolute()
        or any(p in (".", "..") or ":" in p or "\x00" in p for p in value.split("/"))
    ):
        raise ValueError("文件相对路径无效")
    return str(path)


class Library:
    def __init__(self, data_dir: Path, allowed_roots: list[Path] | None = None):
        self.data_dir = data_dir.expanduser().resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "library.sqlite3"
        for directory in ("originals", "cache", "uploads", "backups"):
            (self.data_dir / directory).mkdir(exist_ok=True)
        self.allowed_roots = (
            [p.expanduser().resolve() for p in allowed_roots]
            if allowed_roots is not None
            else None
        )
        self.lock = threading.RLock()
        self.work_lock = threading.RLock()
        self.job_lock = threading.Lock()
        self.stop = threading.Event()
        self.threads: list[threading.Thread] = []
        with self.db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)
            self.migrate_projects(db)
            db.execute(
                "UPDATE ai_tasks SET status='failed',error='服务已重启；为避免重复计费，请检查服务商记录后重新提交' WHERE status IN ('queued','running')"
            )
            db.execute("UPDATE jobs SET status='queued' WHERE status='running'")
            db.execute(
                "INSERT OR IGNORE INTO roots(id,path,name,kind) VALUES ('managed',?,'上传素材','managed')",
                (str(self.data_dir / "originals"),),
            )
            db.execute(
                "INSERT OR IGNORE INTO settings VALUES ('backup',?)",
                (
                    json.dumps(
                        {
                            "enabled": True,
                            "interval_hours": 24,
                            "keep": 7,
                            "directory": str(self.data_dir / "backups"),
                            "last_success": time.time(),
                            "last_error": "",
                            "has_backup": False,
                        }
                    ),
                ),
            )

    def migrate_projects(self, db):
        columns = {row[1] for row in db.execute("PRAGMA table_info(figures)")}
        if "stage" not in columns:
            db.execute(
                "ALTER TABLE figures ADD COLUMN stage TEXT NOT NULL DEFAULT 'draft'"
            )
        db.execute("CREATE INDEX IF NOT EXISTS figure_stage ON figures(stage)")
        db.execute(
            "INSERT OR IGNORE INTO projects(id,name,created) SELECT lower(hex(randomblob(16))),project,? FROM (SELECT project FROM figures UNION SELECT project FROM roots UNION SELECT project FROM uploads) WHERE project<>''",
            (time.time(),),
        )

    def ensure_project(self, db, name):
        name = name.strip()
        if name:
            db.execute(
                "INSERT OR IGNORE INTO projects(id,name,created) VALUES (?,?,?)",
                (uid(), name, time.time()),
            )
        return name

    @contextmanager
    def db(self):
        with self.lock:
            db = sqlite3.connect(self.db_path, timeout=30)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()

    def query(self, sql, params=()):
        with self.db() as db:
            return [dict(row) for row in db.execute(sql, params)]

    def one(self, sql, params=()):
        rows = self.query(sql, params)
        if not rows:
            raise KeyError("记录不存在")
        return rows[0]

    def setting(self, key):
        return json.loads(
            self.one("SELECT value FROM settings WHERE key=?", (key,))["value"]
        )

    def set_setting(self, key, value):
        with self.db() as db:
            db.execute(
                "INSERT OR REPLACE INTO settings VALUES (?,?)",
                (key, json.dumps(value, ensure_ascii=False)),
            )

    def permitted(self, path: Path):
        path = path.expanduser().resolve()
        if (
            self.allowed_roots is not None
            and not any(path.is_relative_to(root) for root in self.allowed_roots)
            and not path.is_relative_to(self.data_dir / "originals")
        ):
            raise ValueError("该目录不在服务器允许访问的素材目录内")
        return path

    def excluded(self, path: Path):
        config = self.setting("backup")
        return path.is_relative_to(self.data_dir) or path.is_relative_to(
            Path(config["directory"]).expanduser().resolve()
        )

    def enqueue(self, kind, target):
        with self.db() as db:
            job_id = uid()
            db.execute(
                "INSERT OR IGNORE INTO jobs(id,kind,target,created,updated) VALUES (?,?,?,?,?)",
                (job_id, kind, target, time.time(), time.time()),
            )
            return dict(
                db.execute(
                    "SELECT * FROM jobs WHERE kind=? AND target=? AND status IN ('queued','running')",
                    (kind, target),
                ).fetchone()
            )

    def add_root(self, path, project="", allow_remote=False):
        resolved = self.permitted(Path(path))
        if not resolved.is_dir() or self.excluded(resolved):
            raise ValueError("请选择可访问的素材目录，不能选择 FigTrace 数据或备份目录")
        with self.db() as db:
            project = self.ensure_project(db, project)
            existing = db.execute(
                "SELECT * FROM roots WHERE path=?", (str(resolved),)
            ).fetchone()
            if existing:
                root_id = existing["id"]
            else:
                root_id = uid()
                db.execute(
                    "INSERT INTO roots(id,path,name,project,allow_remote) VALUES (?,?,?,?,?)",
                    (
                        root_id,
                        str(resolved),
                        resolved.name or str(resolved),
                        project,
                        int(allow_remote),
                    ),
                )
        return self.enqueue("scan", root_id)

    def asset_path(self, asset):
        root = self.one("SELECT * FROM roots WHERE id=?", (asset["root_id"],))
        base = Path(root["path"]).resolve()
        # Managed uploads use generated blob names; the original relative path is metadata.
        path = base / (
            asset["sha256"] + "." + asset["format"]
            if root["kind"] == "managed"
            else asset["relative_path"]
        )
        path = path.resolve()
        if not path.is_relative_to(base):
            raise ValueError("文件已移出登记目录，请重新定位")
        self.permitted(path)
        return path

    def index_file(self, root_id, relative_path, project="", tags=None, sha=None):
        with self.work_lock:
            return self._index_file(root_id, relative_path, project, tags, sha)

    def _index_file(self, root_id, relative_path, project="", tags=None, sha=None):
        root = self.one("SELECT * FROM roots WHERE id=?", (root_id,))
        ext = Path(relative_path).suffix.lstrip(".").lower()
        path = Path(root["path"]) / (
            sha + "." + ext if root["kind"] == "managed" else relative_path
        )
        if (
            not self.permitted(path).is_relative_to(Path(root["path"]).resolve())
            or path.is_symlink()
        ):
            raise ValueError("素材路径已变化或超出登记目录")
        info = path.stat()
        with self.db() as db:
            project = self.ensure_project(db, project)
            old = db.execute(
                "SELECT * FROM assets WHERE root_id=? AND relative_path=?",
                (root_id, relative_path),
            ).fetchone()
            if (
                old
                and old["size"] == info.st_size
                and old["mtime"] == info.st_mtime
                and old["preview"] != "missing"
            ):
                return old["id"]
            if old:
                asset_id = old["id"]
                for cache in (self.data_dir / "cache").glob(asset_id + "*.jpg"):
                    cache.unlink(missing_ok=True)
                db.execute(
                    "UPDATE assets SET size=?,mtime=?,sha256=?,preview='queued',error='' WHERE id=?",
                    (info.st_size, info.st_mtime, sha, asset_id),
                )
            else:
                asset_id, figure_id = uid(), uid()
                db.execute(
                    "INSERT INTO figures(id,title,project,tags,preferred,created) VALUES (?,?,?,?,?,?)",
                    (
                        figure_id,
                        Path(relative_path).stem,
                        project,
                        json.dumps(tags or [], ensure_ascii=False),
                        asset_id,
                        time.time(),
                    ),
                )
                db.execute(
                    "INSERT INTO assets(id,figure_id,root_id,relative_path,name,format,size,mtime,sha256,created) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        asset_id,
                        figure_id,
                        root_id,
                        relative_path,
                        Path(relative_path).name,
                        ext,
                        info.st_size,
                        info.st_mtime,
                        sha,
                        time.time(),
                    ),
                )
        self.enqueue("preview", asset_id)
        return asset_id

    def scan(self, root_id):
        root = self.one("SELECT * FROM roots WHERE id=? AND kind='linked'", (root_id,))
        base = self.permitted(Path(root["path"]))
        if not base.is_dir():
            with self.db() as db:
                db.execute(
                    "UPDATE roots SET status='offline',last_scan=? WHERE id=?",
                    (time.time(), root_id),
                )
            raise ValueError("目录不可访问，已保留现有记录")
        seen, skipped, errors = set(), 0, []
        discovered = []
        backup_dir = Path(self.setting("backup")["directory"]).expanduser().resolve()

        def scan_error(error):
            errors.append(str(error))

        for directory, dirs, files in os.walk(
            base, followlinks=False, onerror=scan_error
        ):
            dirs[:] = [
                name
                for name in dirs
                if not name.startswith(".")
                and not (Path(directory) / name).is_symlink()
                and not (Path(directory) / name).resolve().is_relative_to(self.data_dir)
                and not (Path(directory) / name).resolve().is_relative_to(backup_dir)
            ]
            for name in files:
                if self.stop.is_set():
                    raise ValueError("扫描因服务关闭而中断，可重新扫描")
                path = Path(directory) / name
                if path.suffix.lstrip(".").lower() not in FORMATS or path.is_symlink():
                    continue
                relative = path.relative_to(base).as_posix()
                seen.add(relative)
                try:
                    info = path.stat()
                    offline = (
                        getattr(info, "st_flags", 0)
                        & getattr(stat, "SF_DATALESS", 0x40000000)
                    ) or (
                        getattr(info, "st_file_attributes", 0)
                        & (0x1000 | 0x40000 | 0x400000)
                    )
                    if offline and not root["allow_remote"]:
                        skipped += 1
                        continue
                    discovered.append(relative)
                except (OSError, ValueError) as error:
                    errors.append(f"{relative}: {error}")
        moved = self.reconcile_moves(root, discovered) if not errors else 0
        for relative in discovered:
            if self.stop.is_set():
                raise ValueError("扫描因服务关闭而中断，可重新扫描")
            try:
                # Fetch the current project: it may have been renamed during traversal.
                with self.work_lock:
                    project = self.one(
                        "SELECT project FROM roots WHERE id=?", (root_id,)
                    )["project"]
                    self.index_file(root_id, relative, project)
            except (OSError, ValueError) as error:
                errors.append(f"{relative}: {error}")
        with self.work_lock, self.db() as db:
            # Incomplete traversal cannot prove that a file has disappeared.
            if not errors:
                for row in db.execute(
                    "SELECT id,relative_path FROM assets WHERE root_id=?", (root_id,)
                ).fetchall():
                    if row["relative_path"] not in seen:
                        db.execute(
                            "UPDATE assets SET preview='missing',error='原文件未找到，可重新绑定目录' WHERE id=?",
                            (row["id"],),
                        )
            db.execute(
                "UPDATE roots SET status=?,last_scan=? WHERE id=?",
                ("partial" if errors or skipped else "ready", time.time(), root_id),
            )
        return (
            f"扫描 {len(seen)} 个素材；找回 {moved} 个移动或改名文件；跳过 {skipped} 个未下载文件；{len(errors)} 个读取错误"
            + (f"。{errors[0]}" if errors else "")
        )

    def reconcile_moves(self, root, discovered):
        """Only reconnect unique content matches whose old location is provably absent."""
        existing = {
            r["relative_path"]
            for r in self.query(
                "SELECT relative_path FROM assets WHERE root_id=?", (root["id"],)
            )
        }
        discovered = [relative for relative in discovered if relative not in existing]
        if not discovered:
            return 0
        missing = {}
        for asset in self.query(
            "SELECT a.*,r.path AS root_path FROM assets a JOIN roots r ON r.id=a.root_id WHERE r.kind='linked' AND a.sha256 IS NOT NULL"
        ):
            try:
                base = self.permitted(Path(asset["root_path"]))
                old = self.asset_path(asset)
                # An offline directory is not evidence of a move. Permission errors are
                # likewise not treated as absence.
                if not base.is_dir():
                    continue
                try:
                    old.stat()
                    continue
                except FileNotFoundError:
                    pass
                missing.setdefault(
                    (asset["size"], asset["format"], asset["sha256"]), []
                ).append(asset)
            except (OSError, ValueError):
                continue
        sizes = {(key[0], key[1]) for key in missing}
        matches = {}
        for relative in discovered:
            if relative in existing:
                continue
            path = Path(root["path"]) / relative
            try:
                if path.is_symlink() or not self.permitted(path).is_relative_to(
                    Path(root["path"]).resolve()
                ):
                    continue
                info = path.stat()
                ext = path.suffix.lstrip(".").lower()
                if (info.st_size, ext) not in sizes:
                    continue
                digest = fingerprint(path)
                after = path.stat()
                if (info.st_size, info.st_mtime_ns) != (
                    after.st_size,
                    after.st_mtime_ns,
                ):
                    continue
                key = (info.st_size, ext, digest)
                if key in missing:
                    matches.setdefault(key, []).append((relative, info))
            except (OSError, ValueError):
                continue
        moved = 0
        for key, candidates in matches.items():
            if len(candidates) != 1 or len(missing[key]) != 1:
                continue
            old = missing[key][0]
            relative, info = candidates[0]
            old_path = Path(old["root_path"]) / old["relative_path"]
            new_path = Path(root["path"]) / relative
            with self.work_lock, self.db() as db:
                current_root = db.execute(
                    "SELECT path FROM roots WHERE id=?", (root["id"],)
                ).fetchone()
                current = db.execute(
                    "SELECT * FROM assets WHERE id=?", (old["id"],)
                ).fetchone()
                if (
                    not current_root
                    or current_root["path"] != root["path"]
                    or not current
                    or current["relative_path"] != old["relative_path"]
                    or current["root_id"] != old["root_id"]
                ):
                    continue
                if not Path(old["root_path"]).is_dir():
                    continue
                try:
                    old_path.stat()
                    continue
                except FileNotFoundError:
                    pass
                except OSError:
                    continue
                try:
                    latest = new_path.stat()
                except OSError:
                    continue
                if (
                    latest.st_size != info.st_size
                    or latest.st_mtime_ns != info.st_mtime_ns
                ):
                    continue
                if db.execute(
                    "SELECT 1 FROM assets WHERE root_id=? AND relative_path=?",
                    (root["id"], relative),
                ).fetchone():
                    continue
                # Preserve Figure identity, annotations, versions and source links.
                db.execute(
                    "UPDATE assets SET root_id=?,relative_path=?,name=?,mtime=?,preview='queued',error='' WHERE id=?",
                    (root["id"], relative, new_path.name, info.st_mtime, old["id"]),
                )
                db.execute(
                    "INSERT INTO asset_locations VALUES (?,?,?,?,?)",
                    (uid(), old["id"], str(old_path), str(new_path), time.time()),
                )
                moved += 1
            self.enqueue("preview", old["id"])
        return moved

    def preview(self, asset_id):
        asset = self.one("SELECT * FROM assets WHERE id=?", (asset_id,))
        path = self.asset_path(asset)
        if not path.is_file():
            with self.db() as db:
                db.execute(
                    "UPDATE assets SET preview='missing',error='原文件不可访问' WHERE id=?",
                    (asset_id,),
                )
            return "原文件不可访问"
        output = self.data_dir / "cache" / (asset_id + ".jpg")
        staging = output.with_name(asset_id + "-" + uid() + ".partial.jpg")
        try:
            if asset["root_id"] != "managed":
                digest = fingerprint(path)
                current = path.stat()
                if (
                    current.st_size != asset["size"]
                    or current.st_mtime != asset["mtime"]
                ):
                    raise ValueError("文件在处理期间发生变化，请重新扫描目录")
                with self.db() as db:
                    db.execute(
                        "UPDATE assets SET sha256=? WHERE id=?", (digest, asset_id)
                    )
                duplicates = self.query(
                    "SELECT * FROM assets WHERE sha256=? AND id<>? AND preview='ready' LIMIT 1",
                    (digest, asset_id),
                )
                if duplicates:
                    other = duplicates[0]
                    cached = self.data_dir / "cache" / (other["id"] + ".jpg")
                    if cached.is_file():
                        shutil.copyfile(cached, staging)
                        with self.work_lock, self.db() as db:
                            staging.replace(output)
                            db.execute(
                                "UPDATE assets SET preview='ready',error='',width=?,height=?,pages=?,mode=? WHERE id=?",
                                (
                                    other["width"],
                                    other["height"],
                                    other["pages"],
                                    other["mode"],
                                    asset_id,
                                ),
                            )
                        return "内容相同，已复用预览；原路径保留"
            command = [
                sys.executable,
                "-m",
                "figtrace.preview",
                str(path),
                str(staging),
                "0",
            ]
            process = subprocess.run(
                command, capture_output=True, text=True, timeout=45
            )
            if process.returncode:
                raise ValueError(process.stderr[-800:] or "预览转换失败")
            result = json.loads(process.stdout)
            current = path.stat()
            if current.st_size != asset["size"] or current.st_mtime != asset["mtime"]:
                raise ValueError("预览期间原文件发生变化，请重新扫描")
            with self.work_lock, self.db() as db:
                if result["status"] == "ready":
                    staging.replace(output)
                db.execute(
                    "UPDATE assets SET preview=?,error=?,width=?,height=?,pages=?,mode=? WHERE id=?",
                    (
                        result["status"],
                        result.get("error", ""),
                        result.get("width"),
                        result.get("height"),
                        result.get("pages", 1),
                        result.get("mode", ""),
                        asset_id,
                    ),
                )
            return result.get("error") or "缩略图已生成"
        except Exception as error:
            with self.db() as db:
                db.execute(
                    "UPDATE assets SET preview='failed',error=? WHERE id=?",
                    (str(error)[:800], asset_id),
                )
            return str(error)[:800]
        finally:
            staging.unlink(missing_ok=True)

    def create_upload(self, relative_path, size, sha256, project, tags):
        relative_path = safe_relative(relative_path)
        ext = Path(relative_path).suffix.lstrip(".").lower()
        if ext not in FORMATS:
            raise ValueError("此文件类型暂不支持上传，可先通过目录关联源文件")
        if size <= 0 or size > 20 * 1024**3:
            raise ValueError("单文件须大于 0 且不超过 20 GB")
        if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
            raise ValueError("缺少有效的文件内容校验值")
        with self.db() as db:
            project = self.ensure_project(db, project)
            upload_id = uid()
            db.execute(
                "INSERT INTO uploads(id,relative_path,size,sha256,project,tags,created) VALUES (?,?,?,?,?,?,?)",
                (
                    upload_id,
                    relative_path,
                    size,
                    sha256,
                    project,
                    json.dumps(tags, ensure_ascii=False),
                    time.time(),
                ),
            )
        (self.data_dir / "uploads" / upload_id).touch()
        return self.one("SELECT * FROM uploads WHERE id=?", (upload_id,))

    def append_upload(self, upload_id, offset, data):
        with self.work_lock, self.db() as db:
            row = db.execute(
                "SELECT * FROM uploads WHERE id=?", (upload_id,)
            ).fetchone()
            if not row:
                raise KeyError("上传任务不存在，请重新选择文件")
            if row["asset_id"]:
                return {"offset": row["size"], "asset_id": row["asset_id"]}
            if offset != row["offset"]:
                raise ValueError(
                    f"上传偏移不一致，请刷新进度；服务端已接收 {row['offset']} 字节"
                )
            if not data or len(data) > 4 * 1024**2 or offset + len(data) > row["size"]:
                raise ValueError("上传分片大小无效")
            path = self.data_dir / "uploads" / upload_id
            if not path.exists() or path.stat().st_size < offset:
                raise ValueError("临时上传文件缺失，请重新创建上传任务")
            with path.open("r+b") as file:
                file.seek(offset)
                file.write(data)
                file.truncate()
                file.flush()
                os.fsync(file.fileno())
            db.execute(
                "UPDATE uploads SET offset=? WHERE id=?",
                (offset + len(data), upload_id),
            )
            return {"offset": offset + len(data)}

    def complete_upload(self, upload_id):
        with self.work_lock:
            row = self.one("SELECT * FROM uploads WHERE id=?", (upload_id,))
            if row["asset_id"]:
                return {"asset_id": row["asset_id"], "duplicate": True}
            if row["offset"] != row["size"]:
                raise ValueError("文件尚未上传完整")
            temp = self.data_dir / "uploads" / upload_id
            if fingerprint(temp) != row["sha256"]:
                raise ValueError("文件内容校验失败，请重新上传原文件")
            ext = Path(row["relative_path"]).suffix.lstrip(".").lower()
            destination = self.data_dir / "originals" / (row["sha256"] + "." + ext)
            duplicate = (
                destination.exists() and fingerprint(destination) == row["sha256"]
            )
            if not duplicate:
                staging = destination.with_name(upload_id + ".partial")
                try:
                    shutil.copyfile(temp, staging)
                    staging.replace(destination)
                finally:
                    staging.unlink(missing_ok=True)
            # Separate import namespaces preserve both same-name files and each source path.
            relative = upload_id + "/" + row["relative_path"]
            asset_id = self.index_file(
                "managed",
                relative,
                row["project"],
                json.loads(row["tags"]),
                row["sha256"],
            )
            with self.db() as db:
                db.execute(
                    "UPDATE uploads SET asset_id=? WHERE id=?", (asset_id, upload_id)
                )
            temp.unlink(missing_ok=True)
            return {"asset_id": asset_id, "duplicate": duplicate}

    def backup(self, prefix="figtrace"):
        with self.work_lock:
            config = self.setting("backup")
            target_dir = Path(config["directory"]).expanduser().resolve()
            target_dir.mkdir(parents=True, exist_ok=True)
            name = f"{prefix}-{time.strftime('%Y%m%d-%H%M%S')}-{uid()[:8]}.zip"
            destination = target_dir / name
            with tempfile.TemporaryDirectory(dir=self.data_dir) as temp_dir:
                snapshot = Path(temp_dir) / "library.sqlite3"
                with self.db() as source:
                    copy = sqlite3.connect(snapshot)
                    try:
                        source.backup(copy)
                        copy.execute("DELETE FROM jobs")
                        copy.execute("DELETE FROM uploads")
                        copy.commit()
                        if copy.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                            raise ValueError("备份数据库校验失败")
                    finally:
                        copy.close()
                manifest = {
                    "version": 1,
                    "created": time.time(),
                    "includes_originals": False,
                    "sha256": fingerprint(snapshot),
                }
                staging = destination.with_suffix(".partial")
                try:
                    with zipfile.ZipFile(staging, "w", zipfile.ZIP_DEFLATED) as archive:
                        archive.write(snapshot, "library.sqlite3")
                        archive.writestr("manifest.json", json.dumps(manifest))
                    with zipfile.ZipFile(staging) as archive:
                        if archive.testzip():
                            raise ValueError("备份文件校验失败")
                    staging.replace(destination)
                finally:
                    staging.unlink(missing_ok=True)
            config.update(last_success=time.time(), last_error="", has_backup=True)
            self.set_setting("backup", config)
            if prefix == "figtrace":
                for old in sorted(
                    target_dir.glob("figtrace-*.zip"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )[config["keep"] :]:
                    old.unlink()
            return name

    def restore(self, name):
        if Path(name).name != name or not name.endswith(".zip"):
            raise ValueError("备份名称无效")
        with self.work_lock, self.lock:
            if self.query(
                "SELECT id FROM jobs WHERE status='running' AND kind IN ('scan','preview')"
            ):
                raise ValueError("扫描或预览正在运行，请完成后再恢复备份")
            if self.query("SELECT id FROM ai_tasks WHERE status='running'"):
                raise ValueError("AI 任务运行中，请完成后再恢复备份")
            current_config = self.setting("backup")
            source = Path(current_config["directory"]).expanduser().resolve() / name
            with (
                zipfile.ZipFile(source) as archive,
                tempfile.TemporaryDirectory(dir=self.data_dir) as directory,
            ):
                if set(archive.namelist()) != {
                    "library.sqlite3",
                    "manifest.json",
                } or any(info.file_size > 1024**3 for info in archive.infolist()):
                    raise ValueError("备份结构无效或过大")
                manifest = json.loads(archive.read("manifest.json"))
                if manifest.get("version") != 1:
                    raise ValueError("不支持此备份版本")
                snapshot = Path(directory) / "library.sqlite3"
                snapshot.write_bytes(archive.read("library.sqlite3"))
                if fingerprint(snapshot) != manifest.get("sha256"):
                    raise ValueError("备份校验值不一致")
                source_db = sqlite3.connect(snapshot)
                try:
                    tables = {
                        row[0]
                        for row in source_db.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'"
                        )
                    }
                    if source_db.execute("PRAGMA integrity_check").fetchone()[
                        0
                    ] != "ok" or not {
                        "assets",
                        "figures",
                        "roots",
                        "settings",
                        "jobs",
                        "uploads",
                        "links",
                    }.issubset(tables):
                        raise ValueError("数据库结构或完整性校验失败")
                    safety_copy = self.backup("before-restore")
                    with self.db() as target:
                        source_db.backup(target)
                finally:
                    source_db.close()
            with self.db() as db:
                db.executescript(SCHEMA)
                self.migrate_projects(db)
                db.execute(
                    "UPDATE ai_tasks SET status='failed',error='备份恢复后不会自动重发 AI 请求' WHERE status IN ('queued','running')"
                )
                db.execute(
                    "UPDATE roots SET path=? WHERE id='managed'",
                    (str(self.data_dir / "originals"),),
                )
                db.execute("DELETE FROM jobs")
                db.execute("DELETE FROM uploads")
                db.execute("UPDATE assets SET preview='queued',error='' ")
            for cached in (self.data_dir / "cache").glob("*.jpg"):
                cached.unlink(missing_ok=True)
            # Restore the user's current backup destination, not a potentially obsolete one.
            self.set_setting("backup", current_config)
            for asset in self.query("SELECT id FROM assets"):
                self.enqueue("preview", asset["id"])
            return {"safety_backup": safety_copy}

    def run_one(self):
        # Serialize background converters without blocking interactive upload writes.
        with self.job_lock:
            with self.work_lock, self.db() as db:
                row = db.execute(
                    "SELECT * FROM jobs WHERE status='queued' ORDER BY created LIMIT 1"
                ).fetchone()
                if not row:
                    return False
                job = dict(row)
                db.execute(
                    "UPDATE jobs SET status='running',updated=? WHERE id=?",
                    (time.time(), job["id"]),
                )
            try:
                if job["kind"] == "scan":
                    message = self.scan(job["target"])
                elif job["kind"] == "backup":
                    message = self.backup()
                else:
                    message = self.preview(job["target"])
                    if (
                        self.one(
                            "SELECT preview FROM assets WHERE id=?", (job["target"],)
                        )["preview"]
                        == "failed"
                    ):
                        raise ValueError(message)
                status = "done"
            except Exception as error:
                status, message = "failed", str(error)[:1000]
                if job["kind"] == "backup":
                    config = self.setting("backup")
                    config["last_error"] = message
                    self.set_setting("backup", config)
            with self.db() as db:
                db.execute(
                    "UPDATE jobs SET status=?,message=?,updated=? WHERE id=?",
                    (status, message, time.time(), job["id"]),
                )
            return True

    def start(self):
        def work():
            schedule_at = 0.0
            while not self.stop.is_set():
                if time.time() >= schedule_at:
                    schedule_at = time.time() + 60
                    config = self.setting("backup")
                    if (
                        config["enabled"]
                        and time.time() - config["last_success"]
                        >= config["interval_hours"] * 3600
                    ):
                        self.enqueue("backup", "scheduled")
                    for root in self.query(
                        "SELECT id FROM roots WHERE kind='linked' AND last_scan<?",
                        (time.time() - 3600,),
                    ):
                        self.enqueue("scan", root["id"])
                    with self.work_lock, self.db() as db:
                        for expired in db.execute(
                            "SELECT id FROM uploads WHERE asset_id IS NULL AND created<?",
                            (time.time() - 7 * 86400,),
                        ).fetchall():
                            (self.data_dir / "uploads" / expired["id"]).unlink(
                                missing_ok=True
                            )
                            db.execute(
                                "DELETE FROM uploads WHERE id=?", (expired["id"],)
                            )
                if not self.run_one():
                    self.stop.wait(1)

        thread = threading.Thread(target=work, name="figtrace-worker", daemon=True)
        self.threads.append(thread)
        thread.start()

    def close(self):
        self.stop.set()
        for thread in self.threads:
            thread.join(timeout=50)
