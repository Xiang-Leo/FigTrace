"""Resource-bounded image conversion in a disposable subprocess."""

import json
import shutil
import subprocess
import sys
import warnings
from pathlib import Path


def render(source: Path, destination: Path, page=0):
    from PIL import Image, ImageOps

    Image.MAX_IMAGE_PIXELS = 80_000_000
    warnings.simplefilter("error", Image.DecompressionBombWarning)
    ext = source.suffix.lower()
    if ext in {".pdf", ".ai"}:
        import pypdfium2 as pdfium

        with pdfium.PdfDocument(source) as document:
            count = len(document)
            if not 0 <= page < count:
                raise ValueError("页码超出范围")
            sheet = document[page]
            width, height = sheet.get_size()
            bitmap = sheet.render(scale=min(2, 1600 / max(width, height)))
            image = bitmap.to_pil().copy()
            bitmap.close()
            sheet.close()
            meta = {
                "pages": count,
                "width": round(width),
                "height": round(height),
                "mode": "PDF 页面（pt）",
            }
    elif ext == ".svg":
        return {
            "status": "unsupported",
            "error": "SVG 已入库；此版本暂未启用 SVG 预览转换器",
        }
    elif ext == ".eps":
        gs = shutil.which("gs") or shutil.which("gswin64c")
        if not gs:
            return {
                "status": "unsupported",
                "error": "EPS 预览需要安装 Ghostscript；原文件已保留",
            }
        result = subprocess.run(
            [
                gs,
                "-dSAFER",
                "-dBATCH",
                "-dNOPAUSE",
                "-dEPSCrop",
                "-sDEVICE=jpeg",
                "-r72",
                "-dDownScaleFactor=2",
                f"-sOutputFile={destination}",
                str(source),
            ],
            capture_output=True,
            timeout=30,
        )
        if result.returncode or not destination.is_file():
            raise ValueError("EPS 转换失败，可关联导出的 PNG 版本")
        image = Image.open(destination)
        meta = {
            "width": image.width,
            "height": image.height,
            "pages": 1,
            "mode": image.mode,
        }
    else:
        image = Image.open(source)
        count = getattr(image, "n_frames", 1)
        if not 0 <= page < count:
            raise ValueError("页码超出范围")
        image.seek(page)
        meta = {
            "width": image.width,
            "height": image.height,
            "pages": count,
            "mode": image.mode,
        }
        image = ImageOps.exif_transpose(image)
        if image.mode in ("I", "F") or image.mode.startswith("I;16"):
            low, high = image.getextrema()
            image = (
                image.convert("F")
                .point(lambda v: (v - low) * (255 / (high - low or 1)))
                .convert("L")
            )
            meta["mode"] += f"；预览线性映射 {low:g}–{high:g}，原值未改动"
    image.thumbnail((1600, 1600))
    if image.mode == "RGBA" or "transparency" in image.info:
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, "white")
        background.paste(rgba, mask=rgba.getchannel("A"))
        image = background
    else:
        image = image.convert("RGB")
    staging = destination.with_suffix(".tmp.jpg")
    image.save(staging, "JPEG", quality=86)
    staging.replace(destination)
    return {"status": "ready", **meta}


if __name__ == "__main__":
    try:
        # Unix supports address-space limits; Windows still has pixel and timeout limits.
        if sys.platform == "linux":
            import resource

            resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
        print(
            json.dumps(render(Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3])))
        )
    except Exception as error:
        print(json.dumps({"status": "failed", "error": f"无法生成预览：{error}"}))
