"""Local filing hints and separately opted-in, bounded automatic image analysis.

Classification never writes the user's figure metadata. Paid attempts are recorded
before dispatch, including failures, and content-identical inputs reuse one result.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Literal

from pydantic import BaseModel, Field

from .library import uid

CATEGORIES = [
    "统计图表",
    "流程与示意图",
    "显微与实验图",
    "照片与截图",
    "文档",
    "设计源文件",
    "其他图片",
]
DEFAULT_CONFIG = {
    "rules_enabled": True,
    "auto_ai": False,
    "daily_limit": 20,
    "revision": "",
    "auto_revision": "",
}


class ClassificationConfig(BaseModel):
    rules_enabled: bool = True
    auto_ai: bool = False
    daily_limit: int = Field(default=20, ge=1, le=200)


class ClassificationBatch(BaseModel):
    asset_ids: list[str] = Field(default_factory=list, max_length=100)
    method: Literal["rules", "ai"]
    project: str | None = Field(default=None, max_length=200)


class ClassificationEdit(BaseModel):
    category: str = Field(min_length=1, max_length=100)
    tags: list[str] = Field(default_factory=list, max_length=30)
    description: str = Field(default="", max_length=2000)


def source_fingerprint(asset):
    return json.dumps([asset["size"], asset["mtime"]], separators=(",", ":"))


def current_sql(classification="c", asset="a"):
    return (
        f"{classification}.status='active' AND {classification}.source_size={asset}.size "
        f"AND {classification}.source_mtime={asset}.mtime"
    )


def decode(row):
    return {
        **dict(row),
        "tags": json.loads(row["tags"]),
        "manual_override": bool(row["manual_override"]),
    }


def local_rules(asset):
    # Match words, not fragments such as 'bar' in 'Barbara'. A managed path starts
    # with an internal content hash; its user-selected relative path follows it.
    path = asset["relative_path"]
    if asset["root_id"] == "managed":
        path = path.split("/", 1)[-1]
    hints = path.casefold().replace("_", " ").replace("-", " ")
    tests = [
        (
            "统计图表",
            r"\b(chart|plot|scatter|heatmap|volcano|histogram|boxplot|barplot|lineplot|pca|umap|tsne|graph)\b|统计图|散点图|热图|火山图|柱状图|折线图|箱线图|直方图",
        ),
        (
            "流程与示意图",
            r"\b(diagram|schematic|flowchart|workflow|pathway|architecture)\b|示意图|流程图|机制图|通路图|架构图",
        ),
        (
            "显微与实验图",
            r"\b(microscopy|microscope|confocal|western|blot|gel|immunofluorescence)\b|显微|免疫荧光|电镜|凝胶|蛋白印迹",
        ),
        (
            "照片与截图",
            r"\b(photo|photograph|screenshot|screen shot)\b|照片|截图|屏幕快照|截屏",
        ),
    ]
    category = next((name for name, pattern in tests if re.search(pattern, hints)), "")
    if category:
        description = "根据文件名或目录关键词推断，未识别图片内容；可修改或撤销。"
    elif asset["format"] == "pdf":
        category, description = "文档", "根据 PDF 文件格式归类，未识别文档内容。"
    elif asset["format"] in ("ai", "psd", "eps"):
        category, description = "设计源文件", "根据文件格式归类，未识别图层或图片内容。"
    else:
        category, description = "其他图片", "仅识别文件格式；文件名未提供明确分类线索。"
    return {
        "category": category,
        "tags": [asset["format"].upper()],
        "description": description,
    }


def normalize_category(value):
    if value in CATEGORIES:
        return value
    # Backwards-compatible providers may return their own category vocabulary.
    mapped = local_rules(
        {"relative_path": str(value), "root_id": "linked", "format": ""}
    )["category"]
    return mapped


class Classifier:
    def __init__(self, library, ai):
        self.library, self.ai = library, ai
        library.classifier = self
        ai.classifier = self
        with library.db() as db:
            db.execute(
                "INSERT OR IGNORE INTO settings VALUES ('classification',?)",
                (json.dumps(DEFAULT_CONFIG),),
            )

    def config(self, db=None):
        if db is None:
            return {**DEFAULT_CONFIG, **self.library.setting("classification")}
        row = db.execute(
            "SELECT value FROM settings WHERE key='classification'"
        ).fetchone()
        return {**DEFAULT_CONFIG, **(json.loads(row[0]) if row else {})}

    def public_config(self):
        config = self.config()
        analysis = self.ai.config()["analysis"]
        used = self.library.one(
            "SELECT COUNT(*) AS n FROM classification_attempts WHERE created>?",
            (time.time() - 86400,),
        )["n"]
        return {
            **{key: config[key] for key in ("rules_enabled", "auto_ai", "daily_limit")},
            "used_today": used,
            "remaining_today": max(0, config["daily_limit"] - used),
            "analysis_ready": bool(analysis["model"]),
            "analysis_base_url": analysis["base_url"],
            "analysis_model": analysis["model"],
            "categories": CATEGORIES,
            "pending": self.library.one(
                "SELECT COUNT(*) AS n FROM classification_queue WHERE state IN ('waiting','preview')"
            )["n"],
        }

    def save_config(self, body):
        with self.library.work_lock, self.library.db() as db:
            previous = self.config(db)
            config = body.model_dump()
            config["revision"] = (
                self.ai.ready_config("analysis")["revision"] if body.auto_ai else ""
            )
            config["auto_revision"] = (
                previous["auto_revision"]
                if body.auto_ai and previous["auto_ai"]
                else uid()
                if body.auto_ai
                else ""
            )
            db.execute(
                "INSERT OR REPLACE INTO settings VALUES ('classification',?)",
                (json.dumps(config),),
            )
            if previous["auto_ai"] and (
                not body.auto_ai or previous["revision"] != config["revision"]
            ):
                self._cancel_automatic(db)
        return self.public_config()

    def _cancel_automatic(self, db):
        db.execute(
            "UPDATE classification_queue SET state='skipped' WHERE state IN ('waiting','preview')"
        )
        # Running requests cannot be unsent. Their result is checked again before
        # publication; queued requests are stopped before contacting a provider.
        db.execute(
            "UPDATE ai_tasks SET status='dismissed',error='自动 AI 分类已关闭，未提交此排队请求' WHERE status='queued' AND json_extract(options,'$.classification')=1 AND json_extract(options,'$.automatic')=1"
        )

    def disable_automatic(self):
        with self.library.work_lock, self.library.db() as db:
            config = self.config(db)
            config.update(auto_ai=False, revision="", auto_revision="")
            db.execute(
                "INSERT OR REPLACE INTO settings VALUES ('classification',?)",
                (json.dumps(config),),
            )
            self._cancel_automatic(db)

    def after_restore(self):
        self.disable_automatic()

    def on_index(self, db, asset_id, *, is_new):
        asset = dict(
            db.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
        )
        config = self.config(db)
        if config["rules_enabled"]:
            self._store(db, asset, "rules", local_rules(asset))
        if is_new and config["auto_ai"]:
            db.execute(
                "INSERT OR IGNORE INTO classification_queue(asset_id,source_fingerprint,config_revision,state,created) VALUES (?,?,?,'preview',?)",
                (
                    asset_id,
                    source_fingerprint(asset),
                    config["auto_revision"],
                    time.time(),
                ),
            )

    def preview_ready(self, asset_id):
        # Move only the indexed queue entry for this completed preview. Keeping
        # unfinished conversions out of the ready queue avoids rescanning a large
        # import's pending previews on every AI worker tick.
        with self.library.db() as db:
            db.execute(
                "UPDATE classification_queue SET state=CASE WHEN (SELECT preview FROM assets WHERE id=?)='ready' THEN 'waiting' ELSE 'skipped' END WHERE asset_id=? AND state='preview'",
                (asset_id, asset_id),
            )

    def _store(self, db, asset, origin, result):
        old = db.execute(
            "SELECT * FROM classifications WHERE asset_id=? AND origin=?",
            (asset["id"], origin),
        ).fetchone()
        # Explicit corrections and dismissals are durable user decisions. They
        # never get overwritten by the next scan, batch click or duplicate result.
        if old and (old["manual_override"] or old["status"] == "dismissed"):
            return False
        now = time.time()
        values = (
            result["category"],
            json.dumps(result.get("tags", []), ensure_ascii=False),
            result.get("description", ""),
            source_fingerprint(asset),
            asset["sha256"] or "",
            asset["size"],
            asset["mtime"],
            now,
        )
        if old:
            db.execute(
                "UPDATE classifications SET category=?,tags=?,description=?,source_fingerprint=?,source_sha256=?,source_size=?,source_mtime=?,updated=? WHERE id=?",
                (*values, old["id"]),
            )
        else:
            db.execute(
                "INSERT INTO classifications(category,tags,description,source_fingerprint,source_sha256,source_size,source_mtime,updated,id,asset_id,origin,created) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (*values, uid(), asset["id"], origin, now),
            )
        return True

    def records(self, asset_id, *, active_only=False):
        condition = (
            " AND " + current_sql()
            if active_only
            else " AND c.source_size=a.size AND c.source_mtime=a.mtime"
        )
        return [
            decode(row)
            for row in self.library.query(
                "SELECT c.* FROM classifications c JOIN assets a ON a.id=c.asset_id WHERE c.asset_id=?"
                + condition
                + " ORDER BY c.origin,c.created",
                (asset_id,),
            )
        ]

    def _input(self, asset):
        if asset["preview"] != "ready" or not asset["sha256"]:
            raise ValueError("预览尚未就绪，完成预览后再分类")
        path = self.library.asset_path(asset)
        info = path.stat()
        if info.st_size != asset["size"] or info.st_mtime != asset["mtime"]:
            raise ValueError("原文件已变化，请重新扫描后分类")
        preview = self.library.data_dir / "cache" / (asset["id"] + ".jpg")
        return hashlib.sha256(preview.read_bytes()).hexdigest()

    def _enqueue_ai(self, db, asset, config, *, automatic):
        old = db.execute(
            "SELECT * FROM classifications WHERE asset_id=? AND origin='ai'",
            (asset["id"],),
        ).fetchone()
        if old and (
            old["manual_override"]
            or old["status"] == "dismissed"
            or old["source_fingerprint"] == source_fingerprint(asset)
        ):
            return "skipped", None
        preview_sha = self._input(asset)
        attempt = db.execute(
            "SELECT t.* FROM classification_attempts c JOIN ai_tasks t ON t.id=c.task_id WHERE c.input_sha256=?",
            (asset["sha256"],),
        ).fetchone()
        if attempt:
            if attempt["status"] in ("completed", "applied"):
                result = json.loads(attempt["result"])
                result["category"] = normalize_category(result.get("category", ""))
                self._store(db, asset, "ai", result)
                return "processed", None
            if attempt["status"] in ("queued", "running"):
                return "waiting", attempt["id"]
            return "skipped", None
        # Do not charge for the same image while a user's manual analysis is
        # already pending. A completed manual result may also be reused.
        for task in db.execute(
            "SELECT * FROM ai_tasks WHERE asset_id=? AND kind='analysis' ORDER BY created DESC LIMIT 100",
            (asset["id"],),
        ).fetchall():
            if task["status"] in ("queued", "running"):
                return "waiting", task["id"]
            if task["status"] in ("completed", "applied"):
                result = json.loads(task["result"])
                if result.get("preview_sha256") == preview_sha:
                    result["category"] = normalize_category(result.get("category", ""))
                    self._store(db, asset, "ai", result)
                    db.execute(
                        "INSERT INTO classification_attempts VALUES (?,?,0)",
                        (asset["sha256"], task["id"]),
                    )
                    return "processed", None
        used = db.execute(
            "SELECT COUNT(*) FROM classification_attempts WHERE created>?",
            (time.time() - 86400,),
        ).fetchone()[0]
        if used >= self.config(db)["daily_limit"]:
            return "quota", None
        options = {
            "classification": True,
            "automatic": automatic,
            "source_fingerprint": source_fingerprint(asset),
            "input_sha256": asset["sha256"],
            "preview_sha256": preview_sha,
            "auto_revision": self.config(db)["auto_revision"] if automatic else "",
        }
        task_id = self.ai.enqueue(
            db, "analysis", config, asset_id=asset["id"], options=options
        )
        db.execute(
            "INSERT INTO classification_attempts VALUES (?,?,?)",
            (asset["sha256"], task_id, time.time()),
        )
        return "queued", task_id

    def run_one(self):
        """Consume a bounded, indexed queue of new imports; never scan the gallery."""
        lib = self.library
        with lib.work_lock, lib.db() as db:
            config = self.config(db)
            if not config["auto_ai"]:
                return False
            provider = self.ai.config()["analysis"]
            if provider["revision"] != config["revision"]:
                # Credentials can change outside the app, too.
                config.update(auto_ai=False, revision="", auto_revision="")
                db.execute(
                    "UPDATE settings SET value=? WHERE key='classification'",
                    (json.dumps(config),),
                )
                self._cancel_automatic(db)
                return False
            rows = db.execute(
                "SELECT a.*,q.source_fingerprint AS queued_fingerprint,q.config_revision AS queued_revision FROM classification_queue q JOIN assets a ON a.id=q.asset_id WHERE q.state='waiting' ORDER BY q.created LIMIT 20"
            ).fetchall()
            progressed = False
            for row in rows:
                asset = dict(row)
                state = "skipped"
                if (
                    asset["queued_fingerprint"] == source_fingerprint(asset)
                    and asset["queued_revision"] == config["auto_revision"]
                    and asset["preview"] == "ready"
                ):
                    try:
                        state, _ = self._enqueue_ai(db, asset, provider, automatic=True)
                    except (ValueError, OSError):
                        state = "skipped"
                if state == "quota":
                    break
                if state == "waiting":
                    continue
                db.execute(
                    "UPDATE classification_queue SET state=? WHERE asset_id=?",
                    (
                        "done" if state in ("queued", "processed") else "skipped",
                        asset["id"],
                    ),
                )
                progressed = True
            return progressed

    def preflight(self, task):
        options = json.loads(task["options"])
        if not options.get("classification"):
            return
        with self.library.work_lock:
            if options.get("automatic"):
                config = self.config()
                if (
                    not config["auto_ai"]
                    or config["revision"] != task["config_revision"]
                    or config["auto_revision"] != options["auto_revision"]
                ):
                    raise ValueError("自动 AI 分类已关闭或设置已改变，未采用此结果")
            asset = self.library.one(
                "SELECT * FROM assets WHERE id=?", (task["asset_id"],)
            )
            if (
                source_fingerprint(asset) != options["source_fingerprint"]
                or asset["sha256"] != options["input_sha256"]
                or self._input(asset) != options["preview_sha256"]
            ):
                raise ValueError("图片内容或预览已变化，未采用过期分类")

    def finish(self, db, task, result):
        # preflight uses its own connections; invoke it before entering the write
        # transaction and again under work_lock in AIService.run_one.
        options = json.loads(task["options"])
        if not options.get("classification"):
            return
        if result.get("preview_sha256") != options["preview_sha256"]:
            raise ValueError("分析预览发生变化，未采用过期分类")
        asset = dict(
            db.execute(
                "SELECT * FROM assets WHERE id=?", (task["asset_id"],)
            ).fetchone()
        )
        result["category"] = normalize_category(result.get("category", ""))
        self._store(db, asset, "ai", result)

    def batch(self, body):
        lib = self.library
        outcome = {"processed": 0, "queued": 0, "skipped": 0, "task_ids": []}
        with lib.work_lock, lib.db() as db:
            config = self.ai.ready_config("analysis") if body.method == "ai" else None
            if body.asset_ids:
                ids = list(dict.fromkeys(body.asset_ids))
                marks = ",".join("?" for _ in ids)
                rows = db.execute(
                    f"SELECT a.* FROM assets a WHERE id IN ({marks}) ORDER BY created",
                    ids,
                ).fetchall()
                if len(rows) != len(ids):
                    raise ValueError("部分素材不存在，请刷新图库")
            else:
                conditions = [
                    "NOT EXISTS(SELECT 1 FROM classifications c WHERE c.asset_id=a.id AND c.origin=? AND (c.manual_override=1 OR c.status='dismissed' OR (c.source_size=a.size AND c.source_mtime=a.mtime)))"
                ]
                params = [body.method]
                if body.project is not None:
                    conditions.append("f.project=?")
                    params.append(body.project)
                if body.method == "ai":
                    conditions.append(
                        "a.preview='ready' AND NOT EXISTS(SELECT 1 FROM classification_attempts ca JOIN ai_tasks t ON t.id=ca.task_id WHERE ca.input_sha256=a.sha256 AND t.status NOT IN ('completed','applied'))"
                    )
                rows = db.execute(
                    "SELECT a.* FROM assets a JOIN figures f ON f.id=a.figure_id WHERE "
                    + " AND ".join(conditions)
                    + " ORDER BY a.created LIMIT 100",
                    params,
                ).fetchall()
            for row in rows:
                asset = dict(row)
                if body.method == "rules":
                    state, task_id = (
                        (
                            "processed"
                            if self._store(db, asset, "rules", local_rules(asset))
                            else "skipped"
                        ),
                        None,
                    )
                else:
                    try:
                        state, task_id = self._enqueue_ai(
                            db, asset, config, automatic=False
                        )
                    except (ValueError, OSError):
                        state, task_id = "skipped", None
                    # Explicit batches skip inputs awaiting previews/other tasks;
                    # they never authorize new requests on future days.
                    if state in ("waiting", "quota"):
                        state = "skipped"
                outcome[state] += 1
                if task_id and state == "queued":
                    outcome["task_ids"].append(task_id)
        return outcome


def register_classification(app, lib, classifier):
    @app.get("/api/classification/config")
    def config():
        return classifier.public_config()

    @app.put("/api/classification/config")
    def save_config(body: ClassificationConfig):
        return classifier.save_config(body)

    @app.post("/api/classification/batch")
    def batch(body: ClassificationBatch):
        return classifier.batch(body)

    @app.put("/api/classification/{record_id}")
    def edit(record_id: str, body: ClassificationEdit):
        if body.category not in CATEGORIES:
            raise ValueError("请选择已有分类")
        with lib.work_lock, lib.db() as db:
            old = db.execute(
                "SELECT c.*,a.size,a.mtime FROM classifications c JOIN assets a ON a.id=c.asset_id WHERE c.id=?",
                (record_id,),
            ).fetchone()
            if not old:
                raise KeyError("分类不存在")
            tags = list(dict.fromkeys(t.strip()[:100] for t in body.tags if t.strip()))
            db.execute(
                "UPDATE classifications SET category=?,tags=?,description=?,manual_override=1,status='active',source_size=?,source_mtime=?,source_fingerprint=?,updated=? WHERE id=?",
                (
                    body.category,
                    json.dumps(tags, ensure_ascii=False),
                    body.description.strip(),
                    old["size"],
                    old["mtime"],
                    source_fingerprint(old),
                    time.time(),
                    record_id,
                ),
            )
        return {"ok": True}

    @app.post("/api/classification/{record_id}/dismiss")
    def dismiss(record_id: str):
        with lib.work_lock, lib.db() as db:
            if not db.execute(
                "SELECT 1 FROM classifications WHERE id=?", (record_id,)
            ).fetchone():
                raise KeyError("分类不存在")
            db.execute(
                "UPDATE classifications SET status='dismissed',updated=? WHERE id=?",
                (time.time(), record_id),
            )
        return {"ok": True}
