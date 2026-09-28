"""Copy verified managed originals before switching the library's active root."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from .library import FORMATS, fingerprint, uid


def storage_info(lib):
    return {
        "directory": str(lib.managed_directory()),
        "default_directory": str(lib.data_dir / "originals"),
        "data_directory": str(lib.data_dir),
        "cache_directory": str(lib.data_dir / "cache"),
        "managed_assets": lib.one(
            "SELECT COUNT(*) AS n FROM assets WHERE root_id='managed'"
        )["n"],
    }


def overlaps(first, second):
    return first.is_relative_to(second) or second.is_relative_to(first)


def blob_digest(path):
    digest, separator, extension = path.name.partition(".")
    if (
        not separator
        or extension not in FORMATS
        or len(digest) != 64
        or any(c not in "0123456789abcdef" for c in digest)
    ):
        return None
    return digest


def change_directory(lib, value):
    candidate = Path(value.strip()).expanduser()
    if not value.strip() or not candidate.is_absolute():
        raise ValueError("请输入图片存储目录的完整绝对路径")
    destination = lib.permitted(candidate)
    with lib.work_lock:
        source = lib.managed_directory()
        if destination == source:
            return {**storage_info(lib), "copied_files": 0, "retained_directory": ""}
        if destination != lib.data_dir / "originals" and overlaps(
            destination, lib.data_dir
        ):
            raise ValueError("图片目录不能与数据库、缓存或上传临时目录重叠")
        backup = Path(lib.setting("backup")["directory"]).expanduser().resolve()
        linked = [
            Path(row["path"]).resolve()
            for row in lib.query("SELECT path FROM roots WHERE kind='linked'")
        ]
        if (
            overlaps(destination, source)
            or overlaps(destination, backup)
            or any(overlaps(destination, path) for path in linked)
        ):
            raise ValueError("图片目录不能与当前存储、关联素材或备份目录重叠")
        if not source.is_dir():
            raise ValueError("当前图片存储目录不可访问，请先连接磁盘或网盘目录")

        # Include unreferenced blobs too: older metadata backups may need them.
        originals = []
        for path in source.iterdir():
            digest = blob_digest(path)
            if not digest:
                # Upload staging files and OS metadata are not managed originals.
                continue
            if path.is_symlink() or not path.is_file() or fingerprint(path) != digest:
                raise ValueError("原图缺失或内容校验失败，存储位置未改变")
            originals.append(path)
        names = {path.name for path in originals}
        for asset in lib.query(
            "SELECT sha256,format FROM assets WHERE root_id='managed'"
        ):
            if f"{asset['sha256']}.{asset['format']}" not in names:
                raise ValueError("原图缺失，无法完整复制；存储位置未改变")

        destination.mkdir(parents=True, exist_ok=True)
        for path in destination.iterdir():
            if path.name in (".DS_Store", "Thumbs.db", "desktop.ini"):
                continue
            digest = blob_digest(path)
            if not digest or path.is_symlink() or not path.is_file():
                raise ValueError("请选择空目录或已有的 FigTrace 原图目录")
            if fingerprint(path) != digest:
                raise ValueError("目标目录有冲突文件，未覆盖文件或改变存储位置")

        probe = destination / (uid() + ".partial")
        try:
            with probe.open("xb") as file:
                file.write(b"FigTrace storage check")
                file.flush()
                os.fsync(file.fileno())
        finally:
            probe.unlink(missing_ok=True)

        copied = 0
        for path in originals:
            target = destination / path.name
            original_stat = path.stat()
            if not target.exists():
                staging = destination / (uid() + ".partial")
                try:
                    shutil.copy2(path, staging)
                    if fingerprint(staging) != blob_digest(path):
                        raise ValueError("复制后内容校验失败，存储位置未改变")
                    if target.exists():
                        raise ValueError("目标目录在复制期间发生变化，请重试")
                    staging.replace(target)
                    copied += 1
                finally:
                    staging.unlink(missing_ok=True)
            # Identical retained copies may predate a newer import. Preserve the
            # current timestamp used by previews and classification provenance.
            os.utime(target, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
            if target.stat().st_mtime != original_stat.st_mtime:
                raise ValueError("目标磁盘无法保留原图修改时间，存储位置未改变")
        # Verify paths immediately before switching. Read-only downloads already
        # in progress remain valid because the source copies are retained.
        if lib.permitted(candidate) != destination:
            raise ValueError("目标目录在复制期间发生变化，存储位置未改变")
        with lib.db() as db:
            db.execute(
                "UPDATE roots SET path=?,status='ready' WHERE id='managed'",
                (str(destination),),
            )
        return {
            **storage_info(lib),
            "copied_files": copied,
            "retained_directory": str(source),
        }
