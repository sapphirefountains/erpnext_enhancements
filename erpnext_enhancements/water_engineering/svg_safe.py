"""A stored drawing, reduced to the SVG this module draws, before anything prints it.

A Water Feature Design keeps its drawings as SVG text: ``results_json`` holds the charts the last derive
made (``charts.duty_svg``, ``schematic_svg``, ``oneline_svg``, ``controls.power_svg``, ``ladder_svg``) and
every Standard Detail holds one in ``svg``. The print formats and the submittal packet place that text
inside their HTML, so it goes through :func:`clean_svg` first, which keeps only what this module's own
drawing code emits (``engine/drawings.py``, which ``engine/electrical`` and ``engine/controls`` draw
through, and the Standard Details ``setup.py`` seeds) and drops everything else:

* **Elements**: :data:`ELEMENTS`. Anything else is removed with everything inside it, and so is a second
  ``svg`` inside the first.
* **Attributes**: :data:`ATTRIBUTES`, matched exactly (SVG attribute names are case-sensitive), plus
  ``style`` on the root only, and only its ``max-width`` and ``font-family`` declarations with plain
  values (no parentheses, no backslash). An attribute in any namespace is dropped, so no ``xlink:href``.
* **Values**: a reference is kept only as a same-drawing ``url(#id)``; a value naming a script or data
  scheme, ``expression``, ``@import`` or a backslash is dropped. An ``id`` must be a plain name and a
  ``class`` plain words.
* **Fails closed**: text that is not one well-formed SVG root (malformed markup, a DOCTYPE or entity
  declaration, anything after the root, too long or too deep) comes back ``""``, never as the original.

Parsed with the standard library only (``xml.etree``): the host installs no packages. Text and attribute
values are written back escaped, so the output is markup this function wrote, not the text it was given.
The Fountain Designer page applies the same kind of list in the browser (``public/js/fountain_designer/
chart.js``). No frappe import: ``we_safe_svg`` is registered in ``hooks.py`` ``jinja.methods`` for the
print formats, and ``prints.drawing`` and the packet helpers call :func:`clean_svg` directly.
``tests/test_water_svg_safe.py`` runs the real generator through it and checks nothing it draws is lost.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any

SVG_NS = "http://www.w3.org/2000/svg"

# Every element engine/drawings.py and the seeded Standard Details emit.
ELEMENTS = frozenset({"svg", "defs", "marker", "path", "rect", "line", "circle", "polyline", "text", "title"})

# Every attribute they emit (besides xmlns, which the parser consumes and the root gets back).
ATTRIBUTES = frozenset({
    "viewBox", "width", "height", "x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "r", "rx", "ry", "d", "points",
    "fill", "fill-opacity", "stroke", "stroke-width", "stroke-dasharray", "stroke-opacity", "transform",
    "font-size", "font-weight", "font-family", "text-anchor", "dominant-baseline",
    "marker-end", "markerWidth", "markerHeight", "markerUnits", "refX", "refY", "orient",
    "id", "class", "role", "aria-label", "data-point", "data-series",
})

# The root's style: the print drawings size themselves with max-width and set their font.
STYLE_PROPERTIES = frozenset({"max-width", "font-family"})

MAX_LENGTH = 2_000_000
MAX_DEPTH = 32
MAX_ELEMENTS = 50_000

_DECLARATIONS = re.compile(r"<!\s*(DOCTYPE|ENTITY)", re.I)
_FRAGMENT_URL = re.compile(r"url\(#[A-Za-z_][\w.-]*\)")
_FORBIDDEN = ("javascript:", "vbscript:", "data:", "expression", "@import", "\\")
_ID = re.compile(r"[A-Za-z_][\w.:-]*")
_CLASS = re.compile(r"[\w\s-]*")
_STYLE_VALUE = re.compile(r"[\w\s#%.,'\"-]+")


class _Refused(Exception):
    pass


def _local(tag: Any) -> str | None:
    """An element's name when it is an SVG element (SVG namespace, or none at all), else None."""
    if not isinstance(tag, str):
        return None  # a comment or processing instruction
    if tag.startswith("{"):
        ns, _, name = tag[1:].partition("}")
        return name if ns == SVG_NS else None
    return tag


def _safe_value(value: str) -> bool:
    folded = re.sub(r"\s+", "", value).lower()
    if any(token in folded for token in _FORBIDDEN):
        return False
    if "url(" in folded and not _FRAGMENT_URL.fullmatch(value.strip()):
        return False
    return True


def _style(value: str) -> str | None:
    """The root's ``style``, reduced to :data:`STYLE_PROPERTIES` with plain values; None when nothing is left."""
    kept = []
    for declaration in value.split(";"):
        prop, sep, val = declaration.partition(":")
        prop, val = prop.strip().lower(), val.strip()
        if sep and prop in STYLE_PROPERTIES and _STYLE_VALUE.fullmatch(val) and _safe_value(val):
            kept.append(f"{prop}:{val}")
    return ";".join(kept) or None


def _attributes(el: ET.Element, root: bool) -> list[tuple[str, str]]:
    out = []
    for name, value in el.attrib.items():
        if name.startswith("{"):
            continue  # namespaced: xlink:href and the like
        if name == "style" and root:
            style = _style(value)
            if style:
                out.append((name, style))
            continue
        if name not in ATTRIBUTES or not _safe_value(value):
            continue
        if name == "id" and not _ID.fullmatch(value):
            continue
        if name == "class" and not _CLASS.fullmatch(value):
            continue
        out.append((name, value))
    return out


def _text(value: str | None) -> str:
    return (value or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _quoted(value: str) -> str:
    return _text(value).replace('"', "&quot;")


def _write(el: ET.Element, name: str, depth: int, parts: list, count: list, xmlns: bool = False) -> None:
    count[0] += 1
    if count[0] > MAX_ELEMENTS:
        raise _Refused
    attrs = "".join(f' {k}="{_quoted(v)}"' for k, v in _attributes(el, depth == 0))
    if xmlns:
        attrs = f' xmlns="{SVG_NS}"' + attrs
    inner: list = [_text(el.text)]
    for child in el:
        child_name = _local(child.tag)
        if child_name in ELEMENTS and child_name != "svg" and depth + 1 < MAX_DEPTH:
            _write(child, child_name, depth + 1, inner, count)
        inner.append(_text(child.tail))  # a dropped element's tail is the parent's text
    body = "".join(inner)
    parts.append(f"<{name}{attrs}>{body}</{name}>" if body else f"<{name}{attrs}/>")


def clean_svg(svg: Any) -> str:
    """``svg`` with every element and attribute off the lists removed, or ``""`` when it is not one
    well-formed SVG drawing. Never raises."""
    if not isinstance(svg, str) or not svg.strip() or len(svg) > MAX_LENGTH or _DECLARATIONS.search(svg):
        return ""
    try:
        root = ET.fromstring(svg.strip())
    except (ET.ParseError, ValueError, RecursionError):
        return ""
    if _local(root.tag) != "svg":
        return ""
    parts: list = []
    try:
        _write(root, "svg", 0, parts, [0], xmlns=root.tag.startswith("{"))
    except (_Refused, RecursionError):
        return ""
    return "".join(parts)


def we_safe_svg(svg: Any) -> str:
    """Jinja method (``hooks.py`` ``jinja.methods``): a stored drawing as the print formats may place it.
    The same as :func:`clean_svg`."""
    return clean_svg(svg)
