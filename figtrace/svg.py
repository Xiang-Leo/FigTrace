"""A deliberately small, self-contained SVG subset for previews and editing.

Only this normalized output may be handed to a browser or the renderer. Unknown
features fail closed instead of silently changing a scientific illustration.
"""

import base64
import binascii
import io
import math
import re
import sys
import warnings
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
XML_NS = "http://www.w3.org/XML/1998/namespace"
MAX_SVG_BYTES = 12 * 1024 * 1024
MAX_NODES = 10_000
MAX_DEPTH = 64
MAX_EXPANDED_NODES = 100_000
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
MAX_EXPANDED_IMAGE_PIXELS = 80_000_000
MAX_MARKER_NODES = 64
MAX_DIMENSION = 100_000
MAX_GEOMETRY_NUMBERS = 200_000
PREVIEW_SIZE = 1600

_ELEMENTS = {
    "svg",
    "g",
    "defs",
    "title",
    "desc",
    "style",
    "symbol",
    "use",
    "image",
    "path",
    "rect",
    "circle",
    "ellipse",
    "line",
    "polyline",
    "polygon",
    "text",
    "tspan",
    "textPath",
    "linearGradient",
    "radialGradient",
    "stop",
    "clipPath",
    "mask",
    "marker",
}
_PRESENTATION = {
    "fill",
    "fill-opacity",
    "fill-rule",
    "stroke",
    "stroke-width",
    "stroke-opacity",
    "stroke-linecap",
    "stroke-linejoin",
    "stroke-miterlimit",
    "stroke-dasharray",
    "stroke-dashoffset",
    "opacity",
    "color",
    "display",
    "visibility",
    "font-family",
    "font-size",
    "font-style",
    "font-weight",
    "font-variant",
    "font-stretch",
    "text-anchor",
    "text-decoration",
    "dominant-baseline",
    "alignment-baseline",
    "baseline-shift",
    "letter-spacing",
    "word-spacing",
    "white-space",
    "writing-mode",
    "direction",
    "unicode-bidi",
    "clip-path",
    "clip-rule",
    "mask",
    "marker-start",
    "marker-mid",
    "marker-end",
    "stop-color",
    "stop-opacity",
    "vector-effect",
    "paint-order",
    "shape-rendering",
    "text-rendering",
    "image-rendering",
    "overflow",
}
_ATTRIBUTES = _PRESENTATION | {
    "id",
    "class",
    "style",
    "version",
    "viewBox",
    "preserveAspectRatio",
    "x",
    "y",
    "x1",
    "x2",
    "y1",
    "y2",
    "dx",
    "dy",
    "width",
    "height",
    "cx",
    "cy",
    "r",
    "rx",
    "ry",
    "d",
    "points",
    "transform",
    "href",
    "offset",
    "gradientUnits",
    "gradientTransform",
    "spreadMethod",
    "fx",
    "fy",
    "fr",
    "clipPathUnits",
    "maskUnits",
    "maskContentUnits",
    "markerUnits",
    "markerWidth",
    "markerHeight",
    "orient",
    "refX",
    "refY",
    "textLength",
    "lengthAdjust",
    "rotate",
    "startOffset",
    "method",
    "spacing",
    "type",
    "role",
    "aria-label",
}
_GEOMETRY = {
    "viewBox",
    "x",
    "y",
    "x1",
    "x2",
    "y1",
    "y2",
    "dx",
    "dy",
    "width",
    "height",
    "cx",
    "cy",
    "r",
    "rx",
    "ry",
    "d",
    "points",
    "transform",
    "gradientTransform",
    "fx",
    "fy",
    "fr",
    "markerWidth",
    "markerHeight",
    "refX",
    "refY",
    "textLength",
    "rotate",
    "startOffset",
    "stroke-width",
    "stroke-miterlimit",
    "stroke-dasharray",
    "stroke-dashoffset",
    "font-size",
}
_ID = re.compile(r"[A-Za-z_][A-Za-z0-9_.:-]{0,127}\Z")
_NUMBER = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?")
_LOCAL_URL = re.compile(
    r"url\(\s*(['\"]?)#([A-Za-z_][A-Za-z0-9_.:-]{0,127})\1\s*\)", re.IGNORECASE
)
_UNSAFE_VALUE = re.compile(
    r"[\\\x00-\x08\x0b\x0c\x0e-\x1f<>@]|(?:javascript|vbscript|expression)\s*:",
    re.IGNORECASE,
)
_SELECTOR_TOKEN = re.compile(r"(?:[A-Za-z][\w-]*)?(?:[.#][A-Za-z_][\w-]*)*\Z")
_LENGTH = re.compile(
    r"\s*(\d+(?:\.\d*)?|\.\d+)(?:[eE]([-+]?\d+))?\s*(px|pt|pc|mm|cm|in|%)?\s*\Z"
)


def _error(message: str) -> ValueError:
    return ValueError(f"SVG 无法安全处理：{message}")


def _name(value: str) -> tuple[str, str]:
    if value.startswith("{"):
        namespace, name = value[1:].split("}", 1)
        return namespace, name
    return "", value


def _value(value: str, references: set[str]) -> str:
    if (
        len(value) > 2_000_000
        or _UNSAFE_VALUE.search(value)
        or "/*" in value
        or "*/" in value
    ):
        raise _error("属性含不支持的内容")
    for match in _LOCAL_URL.finditer(value):
        references.add(match.group(2))
    remainder = _LOCAL_URL.sub("", value)
    if re.search(r"url\s*\(|(?:https?|file|data|ftp)\s*:", remainder, re.IGNORECASE):
        raise _error("仅允许文档内部引用和嵌入的 PNG/JPEG")
    return value


def _declarations(source: str, references: set[str], *, stylesheet=False) -> str:
    if any(token in source for token in ("/*", "*/", "{", "}", "\\", "@")):
        raise _error("不支持此 CSS 语法")
    declarations = []
    for declaration in source.split(";"):
        if not declaration.strip():
            continue
        key, separator, value = declaration.partition(":")
        key, value = key.strip().lower(), value.strip()
        if not separator or key not in _PRESENTATION or not value:
            raise _error("CSS 仅支持绘图和文本样式")
        if "!" in value:
            raise _error("不支持 CSS 优先级标记")
        if stylesheet and (
            (key.startswith("marker-") and value.lower() != "none")
            or (_LOCAL_URL.search(value) and key not in {"fill", "stroke"})
        ):
            raise _error("样式表中的内部引用仅支持填充和描边渐变；箭头须写在路径属性中")
        declarations.append(f"{key}:{_value(value, references)}")
    return ";".join(declarations)


def _stylesheet(source: str, references: set[str]) -> str:
    if len(source) > 100_000 or any(
        token in source for token in ("/*", "*/", "\\", "@")
    ):
        raise _error("不支持此 CSS 样式表")
    rules = []
    while source.strip():
        selectors, opening, remaining = source.partition("{")
        body, closing, source = remaining.partition("}")
        selectors = selectors.strip()
        if not opening or not closing or not _safe_selectors(selectors):
            raise _error("CSS 仅支持简单的标签、类和 ID 选择器")
        rules.append(
            f"{selectors}{{{_declarations(body, references, stylesheet=True)}}}"
        )
    return "\n".join(rules)


def _safe_selectors(source: str) -> bool:
    selectors = source.split(",")
    if len(selectors) > 128:
        return False
    for selector in selectors:
        tokens = re.split(r"\s*>\s*|\s+", selector.strip())
        if len(tokens) > 16 or any(
            not token or not _SELECTOR_TOKEN.fullmatch(token) for token in tokens
        ):
            return False
    return True


def _marker_references(element: ET.Element, name: str) -> dict[str, int]:
    """Count arrow placements, including each subpath of a compound path.

    Markers cannot be inherited from containers or supplied by stylesheet
    selectors: both would multiply their use outside the explicit graph.
    """
    properties = [
        (key, value)
        for key, value in element.attrib.items()
        if key.startswith("marker-")
    ]
    properties.extend(
        (key, value)
        for declaration in element.get("style", "").split(";")
        for key, _, value in [declaration.partition(":")]
        if key.startswith("marker-")
    )
    references: dict[str, int] = {}
    subpaths = (
        max(1, len(re.findall(r"[Mm]", element.get("d", "")))) if name == "path" else 1
    )
    for key, value in properties:
        if value.strip().lower() == "none":
            continue
        if key == "marker-mid":
            raise _error("暂不支持沿路径重复的 marker-mid 箭头")
        if name not in {"path", "line", "polyline", "polygon"}:
            raise _error("箭头只能直接设置在路径或线条上，不能从分组继承")
        match = _LOCAL_URL.fullmatch(value.strip())
        if not match:
            raise _error("箭头必须引用文档内的 marker 元素")
        reference = match.group(2)
        references[reference] = references.get(reference, 0) + subpaths
    return references


def _embedded_image(value: str, budget: dict[str, int]) -> str:
    match = re.fullmatch(r"data:image/(png|jpeg);base64,([A-Za-z0-9+/=\s]+)", value)
    if not match:
        raise _error("图片必须是嵌入的 PNG 或 JPEG")
    try:
        payload = base64.b64decode(re.sub(r"\s+", "", match.group(2)), validate=True)
    except (ValueError, binascii.Error) as error:
        raise _error("嵌入图片编码无效") from error
    budget["bytes"] += len(payload)
    if budget["bytes"] > MAX_IMAGE_BYTES:
        raise _error("嵌入图片超过 8 MiB")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(payload)) as image:
                if image.format != {"png": "PNG", "jpeg": "JPEG"}[match.group(1)]:
                    raise _error("嵌入图片类型与内容不一致")
                budget["pixels"] += image.width * image.height
                if (
                    budget["pixels"] > MAX_IMAGE_PIXELS
                    or getattr(image, "n_frames", 1) != 1
                ):
                    raise _error("嵌入图片尺寸过大或包含动画")
                image.verify()
    except (
        OSError,
        SyntaxError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as error:
        raise _error("嵌入图片无效或尺寸过大") from error
    return f"data:image/{match.group(1)};base64,{base64.b64encode(payload).decode('ascii')}"


def _length(value: str, fallback: float) -> float:
    match = _LENGTH.fullmatch(value)
    if not match:
        raise _error("画布尺寸必须是有效的正数")
    try:
        number = float(match.group(1)) * 10 ** int(match.group(2) or 0)
    except (OverflowError, ValueError) as error:
        raise _error("画布尺寸过大") from error
    unit = match.group(3) or "px"
    number *= {
        "px": 1,
        "pt": 96 / 72,
        "pc": 16,
        "mm": 96 / 25.4,
        "cm": 96 / 2.54,
        "in": 96,
        "%": fallback / 100,
    }[unit]
    if not math.isfinite(number) or not 0 < number <= MAX_DIMENSION:
        raise _error("画布尺寸超出支持范围")
    return number


def _canvas(root: ET.Element) -> tuple[float, float]:
    viewbox = root.get("viewBox")
    if viewbox is not None:
        try:
            box = [float(part) for part in re.split(r"[\s,]+", viewbox.strip())]
        except ValueError as error:
            raise _error("viewBox 无效") from error
        if (
            len(box) != 4
            or not all(math.isfinite(n) and abs(n) <= MAX_DIMENSION for n in box)
            or box[2] <= 0
            or box[3] <= 0
        ):
            raise _error("viewBox 超出支持范围")
        fallback_width, fallback_height = box[2:]
    else:
        fallback_width, fallback_height = 300, 150
    width = _length(root.get("width", str(fallback_width)), fallback_width)
    height = _length(root.get("height", str(fallback_height)), fallback_height)
    root.set("width", f"{width:g}")
    root.set("height", f"{height:g}")
    if viewbox is None:
        root.set("viewBox", f"0 0 {width:g} {height:g}")
    return width, height


def sanitize_svg(data: bytes | str) -> str:
    """Normalize the supported SVG subset, or raise a Chinese ValueError.

    The returned document has no external resources, executable elements, or
    unbounded reference expansion. It is safe to load as an image; applications
    should still isolate any interactive editor from their main document.
    """
    if isinstance(data, bytes):
        if len(data) > MAX_SVG_BYTES:
            raise _error("文件超过 12 MiB")
        try:
            source = data.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise _error("文件必须使用 UTF-8 编码") from error
    elif isinstance(data, str):
        source = data.lstrip("\ufeff")
        try:
            size = len(source.encode("utf-8"))
        except UnicodeEncodeError as error:
            raise _error("文件必须使用 UTF-8 编码") from error
        if size > MAX_SVG_BYTES:
            raise _error("文件超过 12 MiB")
    else:
        raise _error("内容必须是文本或字节")
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)", source, re.IGNORECASE):
        raise _error("禁止 DTD 和实体声明")
    source = re.sub(r"^\s*<\?xml\s+[^?]*\?>", "", source, count=1)
    if "<?" in source:
        raise _error("禁止 XML 处理指令")
    try:
        depth = count = 0
        parser = ET.iterparse(io.StringIO(source), events=("start", "end"))
        for event, _ in parser:
            if event == "start":
                depth += 1
                count += 1
                if depth > MAX_DEPTH or count > MAX_NODES:
                    raise _error("图形层级或元素数量超过限制")
            else:
                depth -= 1
        root = parser.root
    except ET.ParseError as error:
        raise _error("XML 格式无效") from error
    if _name(root.tag)[1] != "svg":
        raise _error("根元素必须是 svg")

    ids: dict[str, ET.Element] = {}
    references: dict[ET.Element, set[str]] = {}
    marker_references: dict[ET.Element, dict[str, int]] = {}
    stylesheet_references: set[str] = set()
    image_pixels: dict[ET.Element, int] = {}
    image_budget = {"bytes": 0, "pixels": 0}
    geometry_numbers = 0
    for element in root.iter():
        namespace, name = _name(element.tag)
        if namespace not in ("", SVG_NS) or name not in _ELEMENTS:
            raise _error("含不支持的元素（脚本、动画、滤镜或外部内容等）")
        element.tag = f"{{{SVG_NS}}}{name}"
        refs: set[str] = set()
        references[element] = refs
        if len(element.attrib) > 64:
            raise _error("元素属性数量超过限制")
        attributes = {}
        for raw_key, value in element.attrib.items():
            attr_namespace, key = _name(raw_key)
            if (
                attr_namespace == XML_NS
                and key == "space"
                and value in ("default", "preserve")
            ):
                attributes[raw_key] = value
                continue
            if attr_namespace not in ("", XLINK_NS) or (
                attr_namespace == XLINK_NS and key != "href"
            ):
                raise _error("不支持此属性命名空间")
            if key not in _ATTRIBUTES or key in attributes:
                raise _error("含不支持的属性（事件或重复引用等）")
            if key == "id":
                if not _ID.fullmatch(value) or value in ids:
                    raise _error("元素 ID 无效或重复")
                ids[value] = element
            elif key == "href":
                if name == "image":
                    previous_pixels = image_budget["pixels"]
                    value = _embedded_image(value, image_budget)
                    image_pixels[element] = image_budget["pixels"] - previous_pixels
                elif name in {"use", "textPath", "linearGradient", "radialGradient"}:
                    if not value.startswith("#") or not _ID.fullmatch(value[1:]):
                        raise _error("禁止外部链接")
                    refs.add(value[1:])
                else:
                    raise _error("此元素不支持链接")
            elif key == "style":
                value = _declarations(value, refs)
            else:
                value = _value(value, refs)
                if key in _GEOMETRY:
                    for match in _NUMBER.finditer(value):
                        geometry_numbers += 1
                        if (
                            geometry_numbers > MAX_GEOMETRY_NUMBERS
                            or abs(float(match.group())) > 1_000_000
                        ):
                            raise _error("图形坐标或复杂度超过限制")
            attributes[key] = value
        element.attrib.clear()
        element.attrib.update(attributes)
        marker_references[element] = _marker_references(element, name)
        if name == "style":
            if len(element):
                raise _error("样式元素不能包含子元素")
            element.text = _stylesheet(element.text or "", refs)
            stylesheet_references.update(refs)
        if (element.text and len(element.text) > 1_000_000) or (
            element.tail and len(element.tail) > 1_000_000
        ):
            raise _error("文本内容超过限制")
    _canvas(root)

    # Child and reference edges form one graph: this catches indirect <use>
    # cycles, cyclic gradients, and exponentially expanding reusable groups.
    costs: dict[ET.Element, tuple[int, int]] = {}
    visiting: set[ET.Element] = set()

    def expansion(element: ET.Element, depth: int = 0) -> tuple[int, int]:
        if element in visiting or depth > MAX_DEPTH:
            raise _error("引用存在循环或层级过深")
        if element in costs:
            return costs[element]
        visiting.add(element)
        total = 1
        pixels = image_pixels.get(element, 0)
        targets = [(child, 1) for child in element]
        for reference in references[element]:
            if reference not in ids:
                raise _error("内部引用的元素不存在")
            placements = marker_references[element].get(reference, 0)
            if placements and _name(ids[reference].tag)[1] != "marker":
                raise _error("箭头必须引用 marker 元素")
            # Keep the base edge as well as marker instances. This deliberately
            # over-counts properties overridden by an inline style.
            targets.append((ids[reference], 1 + placements))
        for target, occurrences in targets:
            target_nodes, target_pixels = expansion(target, depth + 1)
            total += target_nodes * occurrences
            pixels += target_pixels * occurrences
            if total > MAX_EXPANDED_NODES or pixels > MAX_EXPANDED_IMAGE_PIXELS:
                raise _error("引用展开后的图形过于复杂")
        visiting.remove(element)
        costs[element] = (total, pixels)
        return total, pixels

    expanded_nodes, expanded_pixels = expansion(root)
    for element in root.iter():
        if _name(element.tag)[1] == "marker":
            marker_nodes, marker_pixels = costs[element]
            if marker_pixels or marker_nodes > MAX_MARKER_NODES:
                raise _error(
                    "箭头标记须为不超过 64 个元素的矢量图形，不能包含或引用位图"
                )
    # A stylesheet may match every rendered element, including <use> instances.
    # Only gradient paints are allowed here, so they cannot recursively create
    # markers, masks, or clip paths. Charge both fill and stroke conservatively.
    css_nodes = css_pixels = 0
    for reference in stylesheet_references:
        target = ids[reference]
        if _name(target.tag)[1] not in {"linearGradient", "radialGradient"}:
            raise _error("样式表中的内部引用仅支持渐变")
        target_nodes, target_pixels = costs[target]
        css_nodes += target_nodes * expanded_nodes * 2
        css_pixels += target_pixels * expanded_nodes * 2
    if (
        expanded_nodes + css_nodes > MAX_EXPANDED_NODES
        or expanded_pixels + css_pixels > MAX_EXPANDED_IMAGE_PIXELS
    ):
        raise _error("样式表引用展开后的图形过于复杂")
    ET.register_namespace("", SVG_NS)
    output = ET.tostring(root, encoding="unicode")
    if len(output.encode("utf-8")) > MAX_SVG_BYTES:
        raise _error("规范化后的文件超过 12 MiB")
    return output


def render_svg(data: bytes | str) -> bytes:
    """Render a sanitized SVG to PNG, preserving ratio within 1600 × 1600.

    Invoke from the existing disposable preview subprocess for wall-clock and
    process-memory limits. No paths or resource directories reach the renderer.
    """
    import resvg_py

    source = sanitize_svg(data)
    root = ET.fromstring(source)
    width, height = float(root.get("width")), float(root.get("height"))
    scale = min(1, PREVIEW_SIZE / max(width, height))
    try:
        return resvg_py.svg_to_bytes(
            svg_string=source,
            width=max(1, round(width * scale)),
            height=max(1, round(height * scale)),
            dpi=96,
        )
    except (ValueError, RuntimeError) as error:
        raise _error("图形无法渲染，请检查路径和文本格式") from error


if __name__ == "__main__":
    try:
        if sys.platform == "linux":
            import resource

            resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
        if len(sys.argv) != 3:
            raise ValueError("用法：python -m figtrace.svg 输入.svg 输出.png")
        source_path, output_path = map(Path, sys.argv[1:])
        if source_path.stat().st_size > MAX_SVG_BYTES:
            raise _error("文件超过 12 MiB")
        output_path.write_bytes(render_svg(source_path.read_bytes()))
    except (OSError, ValueError, RuntimeError, ImportError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
