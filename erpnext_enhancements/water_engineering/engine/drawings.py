"""Pure-Python SVG builders for the submittal packet's schematic sheets.

The print sandbox runs no JavaScript, so the client design-canvas
(``fountain_canvas.js``) can't produce the artifact a PDF/print needs. These
functions build a self-contained inline ``<svg>`` string from a plain state dict
(no frappe, unit-testable) that wkhtmltopdf renders reliably — literal colors (no
CSS variables, since print has no theme), no external refs, no script.

Two drawings:

* :func:`circulation_schematic_svg` — the equipment train (basin -> pump ->
  filter -> heater -> return) as a left-to-right rail of labeled boxes.
* :func:`electrical_oneline_svg` — the panel one-line: a service bus with the
  main breaker and a branch tap per load, plus the control transformer.

They render whatever nodes/branches they are given; ``packet.py`` builds the
state from a Water Feature Design + its linked Control Panel Design.
"""

from __future__ import annotations

from typing import Any

# Literal mid-tone palette (print has no theme; keep contrast high on white).
_INK = "#222222"
_BOX = "#f4f6f8"
_BORDER = "#4a5568"
_ACCENT = "#2b6cb0"
_MUTED = "#718096"


def _esc(text: Any) -> str:
    """Minimal XML text escaping for labels."""
    return (
        str(text if text is not None else "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _wrap(width: int, height: int, body: str, title: str = "") -> str:
    header = (
        f'<text x="12" y="22" font-size="15" font-weight="bold" fill="{_INK}" '
        f'font-family="Helvetica,Arial,sans-serif">{_esc(title)}</text>'
        if title
        else ""
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="100%" style="max-width:{width}px;font-family:Helvetica,Arial,sans-serif">'
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="#ffffff"/>'
        f"{header}{body}</svg>"
    )


def _box(x: int, y: int, w: int, h: int, lines: list[str], fill: str = _BOX) -> str:
    """A labeled rounded box; ``lines`` stack centered."""
    out = [
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" ry="6" '
        f'fill="{fill}" stroke="{_BORDER}" stroke-width="1.5"/>'
    ]
    n = len(lines) or 1
    line_h = 15
    start = y + h / 2 - (n - 1) * line_h / 2 + 4
    for i, ln in enumerate(lines):
        weight = "bold" if i == 0 else "normal"
        color = _INK if i == 0 else _MUTED
        size = 12 if i == 0 else 10
        out.append(
            f'<text x="{x + w / 2:.0f}" y="{start + i * line_h:.0f}" text-anchor="middle" '
            f'font-size="{size}" font-weight="{weight}" fill="{color}">{_esc(ln)}</text>'
        )
    return "".join(out)


def _arrow(x1: int, y1: int, x2: int, y2: int, label: str = "") -> str:
    out = [
        f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{_ACCENT}" '
        f'stroke-width="2" marker-end="url(#arrow)"/>'
    ]
    if label:
        mx = (x1 + x2) / 2
        out.append(
            f'<text x="{mx:.0f}" y="{y1 - 6}" text-anchor="middle" font-size="9" '
            f'fill="{_MUTED}">{_esc(label)}</text>'
        )
    return "".join(out)


_ARROW_DEF = (
    '<defs><marker id="arrow" markerWidth="9" markerHeight="9" refX="7" refY="3" '
    f'orient="auto" markerUnits="strokeWidth"><path d="M0,0 L7,3 L0,6 Z" fill="{_ACCENT}"/>'
    "</marker></defs>"
)


def circulation_schematic_svg(state: dict[str, Any] | None = None) -> str:
    """Left-to-right equipment train from ``state['nodes']`` (list of
    ``{label, sub}`` or plain strings), joined by flow arrows."""
    state = state or {}
    raw = state.get("nodes") or []
    nodes = []
    for n in raw:
        if isinstance(n, str):
            nodes.append({"label": n, "sub": ""})
        else:
            nodes.append({"label": n.get("label", ""), "sub": n.get("sub", "")})
    title = state.get("title") or "Circulation Equipment Schematic"

    if not nodes:
        return _wrap(680, 90, f'<text x="12" y="60" font-size="11" fill="{_MUTED}">'
                     "No equipment to diagram.</text>", title)

    box_w, box_h, gap, pad_x, top = 118, 60, 46, 12, 44
    width = pad_x * 2 + len(nodes) * box_w + (len(nodes) - 1) * gap
    height = top + box_h + 30
    parts = [_ARROW_DEF]
    x = pad_x
    cy = top + box_h // 2
    for i, node in enumerate(nodes):
        lines = [node["label"]] + ([node["sub"]] if node["sub"] else [])
        parts.append(_box(x, top, box_w, box_h, lines))
        if i < len(nodes) - 1:
            parts.append(_arrow(x + box_w, cy, x + box_w + gap, cy))
        x += box_w + gap
    return _wrap(width, height, "".join(parts), title)


def electrical_oneline_svg(state: dict[str, Any] | None = None) -> str:
    """Panel one-line: the service at top left, an optional feeder to the panel,
    a vertical panel bus, and a branch tap per load.

    ``state`` = ``{service:{label, main_breaker, sub}, feeder:{label, sub},
    panel:{label}, branches:[{label, breaker, sub, wire, vfd}],
    transformers:[{label, sub}], transformer_va}``. Only ``service`` and
    ``branches`` are needed; ``feeder``, ``panel``, a branch's ``wire`` (the
    conductor note under its tap) and ``vfd`` (a drive box in line with the load)
    and ``transformers`` come from ``engine/electrical`` (phase L) and are drawn
    when present. ``transformer_va`` is the older single control-transformer box.
    """
    state = state or {}
    service = state.get("service") or {}
    feeder = state.get("feeder") or {}
    panel = state.get("panel") or {}
    branches = state.get("branches") or []
    xfmrs = list(state.get("transformers") or [])
    xfmr_va = state.get("transformer_va")
    if xfmr_va:
        xfmrs.append({"label": f"Control Transformer {xfmr_va} VA"})
    title = state.get("title") or "Electrical One-Line"

    left = 40
    feeder_len = 150 if feeder else 0
    bus_x = 150 + feeder_len
    top = 50
    has_wire = any(br.get("wire") for br in branches)
    row_h = 56 if has_wire else 46
    has_vfd = any(br.get("vfd") for br in branches)
    vfd_w = 90
    branch_x = bus_x + 100 + (vfd_w + 20 if has_vfd else 0)
    box_w, box_h = 200, 34
    n = len(branches) + len(xfmrs)
    height = top + max(n, 1) * row_h + 40 + (16 if panel else 0)
    width = branch_x + box_w + 30

    parts = [_ARROW_DEF]
    # service source + main breaker
    main_label = service.get("label") or "Service"
    main_breaker = service.get("main_breaker")
    svc_lines = [main_label] + ([f"Main: {main_breaker} A"] if main_breaker else [])
    if service.get("sub"):
        svc_lines.append(service["sub"])
    parts.append(_box(left, top - 24, 150, 48 + (12 if service.get("sub") else 0), svc_lines))
    if feeder:
        parts.append(f'<text x="{left + 150 + feeder_len / 2:.0f}" y="{top - 6}" text-anchor="middle" '
                     f'font-size="9" fill="{_MUTED}">{_esc(feeder.get("label", "Feeder"))}</text>')
        if feeder.get("sub"):
            parts.append(f'<text x="{left + 150 + feeder_len / 2:.0f}" y="{top + 14}" text-anchor="middle" '
                         f'font-size="9" fill="{_MUTED}">{_esc(feeder["sub"])}</text>')
    if panel:
        parts.append(f'<text x="{bus_x + 6}" y="{top + 30}" font-size="10" font-weight="bold" '
                     f'fill="{_INK}">{_esc(panel.get("label", "Panel"))}</text>')
    bus_top = top + 30 + (16 if panel else 0)
    bus_bottom = bus_top - 30 + max(n, 1) * row_h
    parts.append(f'<line x1="{bus_x}" y1="{top + 24 if not feeder else top}" x2="{bus_x}" y2="{bus_bottom}" '
                 f'stroke="{_BORDER}" stroke-width="3"/>')
    parts.append(f'<line x1="{left + 150}" y1="{top}" x2="{bus_x}" y2="{top}" '
                 f'stroke="{_BORDER}" stroke-width="3"/>')

    y = bus_top
    for br in branches:
        lines = [br.get("label", "")]
        sub = br.get("sub", "")
        if sub:
            lines.append(sub)
        tap_end = bus_x + 100
        if br.get("vfd"):
            parts.append(f'<line x1="{bus_x}" y1="{y}" x2="{tap_end}" y2="{y}" '
                         f'stroke="{_ACCENT}" stroke-width="2"/>')
            parts.append(_box(tap_end, int(y - 14), vfd_w, 28, [str(br["vfd"])], fill="#fff7e6"))
            parts.append(f'<line x1="{tap_end + vfd_w}" y1="{y}" x2="{branch_x}" y2="{y}" '
                         f'stroke="{_ACCENT}" stroke-width="2" marker-end="url(#arrow)"/>')
        else:
            parts.append(f'<line x1="{bus_x}" y1="{y}" x2="{branch_x}" y2="{y}" '
                         f'stroke="{_ACCENT}" stroke-width="2" marker-end="url(#arrow)"/>')
        bk = br.get("breaker")
        if bk:
            bk_text = bk if isinstance(bk, str) else f"{bk} A"
            parts.append(f'<text x="{(bus_x + tap_end) / 2:.0f}" y="{y - 5}" text-anchor="middle" '
                         f'font-size="9" fill="{_MUTED}">{_esc(bk_text)}</text>')
        if br.get("wire"):
            parts.append(f'<text x="{(bus_x + tap_end) / 2:.0f}" y="{y + 12}" text-anchor="middle" '
                         f'font-size="8" fill="{_MUTED}">{_esc(br["wire"])}</text>')
        parts.append(_box(branch_x, int(y - box_h / 2), box_w, box_h, lines))
        y += row_h
    for xf in xfmrs:
        parts.append(f'<line x1="{bus_x}" y1="{y}" x2="{branch_x}" y2="{y}" '
                     f'stroke="{_ACCENT}" stroke-width="2" marker-end="url(#arrow)"/>')
        lines = [xf.get("label", "Transformer")] + ([xf["sub"]] if xf.get("sub") else [])
        parts.append(_box(branch_x, int(y - box_h / 2), box_w, box_h, lines, fill="#eef6ff"))
        y += row_h
    return _wrap(width, height, "".join(parts), title)
