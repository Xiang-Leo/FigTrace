import base64
import io
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest
from PIL import Image

from figtrace.preview import render
from figtrace.svg import MAX_SVG_BYTES, SVG_NS, render_svg, sanitize_svg


def svg(body="", **attributes):
    attrs = {"width": "320", "height": "180", **attributes}
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" '
        + " ".join(f'{key}="{value}"' for key, value in attrs.items())
        + ">"
        + body
        + "</svg>"
    )


def image_uri(size=(8, 8)):
    buffer = io.BytesIO()
    Image.new("RGB", size, "red").save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def test_scientific_diagram_keeps_text_gradients_styles_and_embedded_icons():
    source = svg(f"""
      <defs>
        <linearGradient id="shading"><stop offset="0" stop-color="#123456"/>
          <stop offset="1" stop-color="#ffffff"/></linearGradient>
        <marker id="arrow" markerWidth="8" markerHeight="8" refX="4" refY="4">
          <path d="M0,0 L8,4 L0,8 Z"/></marker>
        <clipPath id="clip"><rect width="320" height="180"/></clipPath>
      </defs>
      <style>.label {{font-family:sans-serif;font-size:18px;fill:#123456}}</style>
      <g clip-path="url(#clip)">
        <rect x="2" y="2" width="316" height="176" fill="url(#shading)"/>
        <image href="{image_uri()}" x="10" y="10" width="24" height="24"/>
        <text class="label" x="50" y="40">RNA → 蛋白<tspan dx="4">α</tspan></text>
        <path d="M50 100H240" fill="none" stroke="#123456" marker-end="url(#arrow)"/>
      </g>
    """)
    normalized = sanitize_svg(source)
    assert sanitize_svg(normalized) == normalized
    assert "RNA → 蛋白" in normalized
    image = Image.open(io.BytesIO(render_svg(normalized)))
    assert image.size == (320, 180)
    assert image.getpixel((20, 20))[:3] == (255, 0, 0)
    assert image.getpixel((200, 70))[:3] != (255, 0, 0)


@pytest.mark.parametrize(
    "body",
    [
        "<script>alert(1)</script>",
        '<foreignObject><p xmlns="http://www.w3.org/1999/xhtml">x</p></foreignObject>',
        '<rect onload="alert(1)"/>',
        '<animate attributeName="href" values="javascript:alert(1)"/>',
        '<set attributeName="onload" to="alert(1)"/>',
        '<a href="https://example.com"><rect/></a>',
        '<image href="file:///etc/passwd"/>',
        '<image href="https://example.com/image.png"/>',
        '<image href="data:image/svg+xml;base64,PHN2Zy8+"/>',
        '<use href="https://example.com/image.svg#icon"/>',
        '<rect style="fill:url(https://example.com/image)"/>',
        '<rect fill="u&#114;l(&#104;ttps://example.com/image)"/>',
        '<rect fill="url(#missing)"/>',
        '<style>@import "https://example.com/style.css";</style>',
        "<style>rect {fill:u\\72l(https://example.com/image)}</style>",
        "<style>rect {background-image:url(https://example.com/image)}</style>",
        "<style>rect {--payload:red;fill:var(--payload)}</style>",
        "<style>rect:hover {fill:red}</style>",
        '<filter id="blur"><feGaussianBlur stdDeviation="99999"/></filter>',
        '<pattern id="tiles" width=".000001" height=".000001"/>',
        '<rect xmlns="https://other.example/svg"/>',
        '<image xmlns:xlink="http://www.w3.org/1999/xlink" href="#x" xlink:href="#y"/>',
        '<rect xml:base="file:///tmp/"/>',
    ],
)
def test_rejects_active_external_or_unsupported_svg(body):
    with pytest.raises(ValueError, match="SVG 无法安全处理"):
        sanitize_svg(svg(body))


@pytest.mark.parametrize(
    "source",
    [
        '<!DOCTYPE svg [<!ENTITY x "expanded">]><svg>&x;</svg>',
        '<!DOCTYPE svg SYSTEM "file:///etc/passwd"><svg/>',
        '<?xml-stylesheet href="https://example.com/style.css"?><svg/>',
        "<svg><?payload command?></svg>",
        "<svg>",
        "<html/>",
        '<svg width="0" height="10"/>',
        '<svg width="100001" height="10"/>',
        '<svg viewBox="0 0 NaN 1"/>',
        '<svg viewBox="0 0 1e100 1"/>',
        '<svg width="1e1000000000"/>',
        '<svg><path d="M1e309 0"/></svg>',
        '<svg><g id="same"/><g id="same"/></svg>',
        "<svg>\ud800</svg>",
        b"\xff\xfe<\x00s\x00v\x00g\x00/\x00>\x00",
    ],
)
def test_rejects_invalid_xml_entities_and_dimensions(source):
    with pytest.raises(ValueError, match="SVG 无法安全处理"):
        sanitize_svg(source)


def test_normalizes_utf8_declaration_xlink_and_physical_size():
    source = """<?xml version="1.0" encoding="UTF-8"?>
      <svg xmlns:xlink="http://www.w3.org/1999/xlink" width="1in" height="2in">
      <defs><path id="icon" d="M0 0h10v10H0z"/></defs>
      <use xlink:href="#icon"/><text xml:space="preserve"> a &amp; b </text></svg>"""
    root = ET.fromstring(sanitize_svg(source.encode()))
    assert root.tag == f"{{{SVG_NS}}}svg"
    assert (root.get("width"), root.get("height")) == ("96", "192")
    assert root.find(f"{{{SVG_NS}}}use").get("href") == "#icon"
    assert Image.open(io.BytesIO(render_svg(source))).size == (96, 192)


def test_bounds_preview_before_raster_allocation():
    image = Image.open(
        io.BytesIO(
            render_svg(
                svg(
                    '<rect width="100000" height="50000" fill="red"/>',
                    width="100000",
                    height="50000",
                )
            )
        )
    )
    assert image.size == (1600, 800)
    assert image.getpixel((1599, 799))[:3] == (255, 0, 0)


def test_rejects_xml_and_reference_expansion_bombs():
    with pytest.raises(ValueError, match="元素数量"):
        sanitize_svg(svg("<rect/>" * 10_000))
    with pytest.raises(ValueError, match="层级"):
        sanitize_svg(svg("<g>" * 65 + "</g>" * 65))
    with pytest.raises(ValueError, match="循环"):
        sanitize_svg(
            svg('<g id="a"><use href="#b"/></g><g id="b"><use href="#a"/></g>')
        )
    groups = ['<g id="g0"><rect width="1" height="1"/></g>']
    for index in range(1, 18):
        groups.append(
            f'<g id="g{index}"><use href="#g{index - 1}"/><use href="#g{index - 1}"/></g>'
        )
    with pytest.raises(ValueError, match="展开"):
        sanitize_svg(svg("".join(groups)))
    with pytest.raises(ValueError, match="12 MiB"):
        sanitize_svg(b" " * (MAX_SVG_BYTES + 1))


def test_rejects_false_image_mime_and_raster_pixel_bombs(monkeypatch):
    uri = image_uri()
    with pytest.raises(ValueError, match="类型与内容"):
        sanitize_svg(svg(f'<image href="{uri.replace("image/png", "image/jpeg")}"/>'))
    with pytest.raises(ValueError, match="编码无效"):
        sanitize_svg(svg('<image href="data:image/png;base64,abc"/>'))
    monkeypatch.setattr("figtrace.svg.MAX_IMAGE_PIXELS", 32)
    with pytest.raises(ValueError, match="尺寸过大"):
        sanitize_svg(svg(f'<image href="{uri}"/>'))


def test_repeated_embedded_images_have_an_expansion_budget(monkeypatch):
    monkeypatch.setattr("figtrace.svg.MAX_EXPANDED_IMAGE_PIXELS", 100)
    uri = image_uri()
    with pytest.raises(ValueError, match="展开"):
        sanitize_svg(
            svg(f'<defs><image id="icon" href="{uri}"/></defs><use href="#icon"/>')
        )


@pytest.mark.parametrize(
    "declaration",
    [
        '<path d="M0 0L10 10" marker-mid="url(#arrow)"/>',
        '<path d="M0 0L10 10" style="marker-mid:url(#arrow)"/>',
        '<g marker-end="url(#arrow)"><path d="M0 0L10 10"/></g>',
        '<g style="marker-start:url(#arrow)"><path d="M0 0L10 10"/></g>',
        '<style>path {marker-end:url(#arrow)}</style><path d="M0 0L10 10"/>',
    ],
)
def test_rejects_implicitly_repeated_markers(declaration):
    with pytest.raises(ValueError, match="箭头"):
        sanitize_svg(
            svg(
                '<defs><marker id="arrow"><path d="M0 0L2 1L0 2Z"/></marker></defs>'
                + declaration
            )
        )


def test_counts_markers_for_every_compound_subpath(monkeypatch):
    monkeypatch.setattr("figtrace.svg.MAX_EXPANDED_NODES", 20)
    marker = '<defs><marker id="arrow"><path d="M0 0L2 1L0 2Z"/></marker></defs>'
    with pytest.raises(ValueError, match="展开"):
        sanitize_svg(
            svg(
                marker
                + '<path d="'
                + "M0 0L10 10 " * 100
                + '" marker-end="url(#arrow)"/>'
            )
        )


@pytest.mark.parametrize("indirect", [False, True])
def test_markers_cannot_contain_or_reference_images(indirect):
    image = f'<image id="icon" href="{image_uri()}"/>'
    contents = '<use href="#icon"/>' if indirect else image
    definitions = (
        (image if indirect else "") + '<marker id="arrow">' + contents + "</marker>"
    )
    with pytest.raises(ValueError, match="不能包含或引用位图"):
        sanitize_svg(
            svg(
                "<defs>"
                + definitions
                + '</defs><path d="M0 0L10 10" marker-end="url(#arrow)"/>'
            )
        )


def test_markers_restrict_expanded_vector_complexity():
    with pytest.raises(ValueError, match="64 个元素"):
        sanitize_svg(
            svg(
                '<defs><g id="many">'
                + '<path d="M0 0L1 1"/>' * 64
                + '</g><marker id="arrow"><use href="#many"/></marker></defs>'
            )
        )


def test_css_gradient_budget_counts_all_possible_matching_elements(monkeypatch):
    source = svg(
        '<defs><linearGradient id="paint"><stop offset="0" stop-color="red"/><stop offset="1" stop-color="blue"/></linearGradient></defs><style>rect {fill:url(#paint)}</style>'
        + '<rect width="10" height="10"/>' * 10
    )
    # A normal small diagram keeps its CSS gradient, but every potential match
    # and both fill/stroke uses must contribute to a stricter expansion budget.
    assert "fill:url(#paint)" in sanitize_svg(source)
    assert Image.open(io.BytesIO(render_svg(source))).size == (320, 180)
    monkeypatch.setattr("figtrace.svg.MAX_EXPANDED_NODES", 100)
    with pytest.raises(ValueError, match="样式表引用展开"):
        sanitize_svg(source)


def test_css_cannot_multiply_masks_or_other_reference_types():
    with pytest.raises(ValueError, match="样式表中的内部引用仅支持"):
        sanitize_svg(
            svg(
                '<defs><mask id="mask"><rect width="10" height="10"/></mask></defs><style>rect {mask:url(#mask)}</style><rect width="10" height="10"/>'
            )
        )


def test_preview_and_export_commands_use_safe_renderer(tmp_path):
    source = tmp_path / "sample.svg"
    source.write_text(
        svg('<rect width="80" height="80" fill="red"/>'), encoding="utf-8"
    )
    preview = tmp_path / "preview.jpg"
    metadata = render(source, preview)
    assert metadata["status"] == "ready"
    assert metadata["mode"] == "SVG 安全预览"
    assert Image.open(preview).format == "JPEG"
    export = tmp_path / "export.png"
    result = subprocess.run(
        [sys.executable, "-m", "figtrace.svg", str(source), str(export)],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert Image.open(export).size == (320, 180)
    source.write_text(svg("<script>alert(1)</script>"), encoding="utf-8")
    refused = tmp_path / "refused.png"
    result = subprocess.run(
        [sys.executable, "-m", "figtrace.svg", str(source), str(refused)],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode != 0
    assert not refused.exists()
