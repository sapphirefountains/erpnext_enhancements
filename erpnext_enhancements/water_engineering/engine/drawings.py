"""Pure-Python SVG builders for the submittal packet's schematic sheets.

The print sandbox runs no JavaScript, so the client design-canvas
(``fountain_canvas.js``) can't produce the artifact a PDF/print needs. These
functions build a self-contained inline ``<svg>`` string from a plain state dict
(no frappe, unit-testable) that wkhtmltopdf renders reliably — literal colors (no
CSS variables, since print has no theme), no external refs, no script.

Four drawings:

* :func:`circulation_schematic_svg` — the equipment train (basin -> pump ->
  filter -> heater -> return) as a left-to-right rail of labeled boxes.
* :func:`electrical_oneline_svg` — the panel one-line: a service bus with the
  main breaker and a branch tap per load, plus the control transformer.
* :func:`power_schematic_svg` — the control panel's power circuit: service,
  disconnect, a column per pump with its breaker and starter, drive or
  drive-with-bypass contactor arrangement down to the motor, and the
  transformers (phase L, ``engine/controls``).
* :func:`control_ladder_svg` — the control ladder: two rails and a rung per
  control circuit (E-stop, HOA, run, fault, bypass, wind stages, level, lights).

They render whatever nodes/branches they are given; ``packet.py`` builds the
state from a Water Feature Design + its linked Control Panel Design.

Two more serve the Fountain Designer page, whose ``chart.js`` draws server-made
SVG only through an allow-list (no ``style``, no unknown attribute, no ``url()``
except ``url(#id)``) and colors it from the page theme:

* :func:`pump_duty_svg` — the duty-point chart: pump curves (with VFD speeds by
  the affinity laws), the system curve, the duty point, the BEP window and the
  NPSH margin, drawn in ``currentColor`` with the page's ``fd-*`` classes.
* :func:`page_svg` — an adapter that rewrites one of the print drawings above
  for the page: no root ``style``/size, no white background, no attribute off
  the allow-list, and the fixed print inks mapped to ``currentColor``. The print
  drawings themselves are unchanged; the packet uses them as they are.
"""

from __future__ import annotations

import math
import re
import zlib
from typing import Any

# Literal mid-tone palette (print has no theme; keep contrast high on white).
_INK = "#222222"
_BOX = "#f4f6f8"
_BORDER = "#4a5568"
_ACCENT = "#2b6cb0"
_MUTED = "#718096"


# Characters XML 1.0 does not allow at all (escaping cannot carry them): one in a label makes the
# whole drawing not well-formed, and the page's DOMParser then refuses it. They are dropped.
_XML_ILLEGAL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")


def _esc(text: Any) -> str:
    """Minimal XML text escaping for labels, with the characters XML forbids dropped."""
    return (
        _XML_ILLEGAL.sub("", str(text if text is not None else ""))
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


def _fit(text: Any, width: float, size: int, bold: bool = False) -> str:
    """``text`` cut with an ellipsis to what fits ``width`` at ``size`` px (an average Helvetica glyph is
    about half the size, 0.56 bold): a box label never spills over its border."""
    s = str(text if text is not None else "")
    room = max(3, int(width / (size * (0.56 if bold else 0.5))))
    return s if len(s) <= room else s[: room - 1].rstrip() + "…"


def _box(x: int, y: int, w: int, h: int, lines: list[str], fill: str = _BOX) -> str:
    """A labeled rounded box; ``lines`` stack centered, each cut to the box's width."""
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
            f'font-size="{size}" font-weight="{weight}" fill="{color}">{_esc(_fit(ln, w - 6, size, i == 0))}</text>'
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


def _seg(x1, y1, x2, y2, color=_BORDER, width=2):
    return f'<line x1="{x1:.0f}" y1="{y1:.0f}" x2="{x2:.0f}" y2="{y2:.0f}" stroke="{color}" stroke-width="{width}"/>'


def _small_box(cx, y, label, w=96, h=26, fill=_BOX):
    return _box(int(cx - w / 2), int(y), w, h, [label], fill=fill)


def _motor(cx, cy, label):
    return (
        f'<circle cx="{cx:.0f}" cy="{cy:.0f}" r="18" fill="{_BOX}" stroke="{_BORDER}" stroke-width="1.5"/>'
        f'<text x="{cx:.0f}" y="{cy + 4:.0f}" text-anchor="middle" font-size="12" font-weight="bold" fill="{_INK}">M</text>'
        f'<text x="{cx:.0f}" y="{cy + 34:.0f}" text-anchor="middle" font-size="10" fill="{_INK}">{_esc(label)}</text>'
    )


def power_schematic_svg(state: dict[str, Any] | None = None) -> str:
    """Control-panel power schematic. ``state`` = ``{title, service:{label},
    disconnect:{label}, spd, pumps:[{tag, label, topology, breaker, contactor,
    overload, vfd, sub}], transformers:[{label}]}``. ``topology`` is one of
    across_the_line / soft_starter / vfd / vfd_bypass; a bypass column draws the
    line (K1) and output (K2) contactors around the drive and the bypass
    contactor (K3) with its overload beside it, rejoining at the motor."""
    state = state or {}
    pumps = state.get("pumps") or []
    xfmrs = state.get("transformers") or []
    title = state.get("title") or "Power Schematic"
    col_w = 150
    bypass_extra = 110
    left = 30
    widths = [col_w + (bypass_extra if p.get("topology") == "vfd_bypass" else 0) for p in pumps]
    width = max(680, left + 190 + sum(widths) + len(xfmrs) * col_w + 30)
    height = 470
    bus_y = 110
    parts = []
    svc = state.get("service") or {}
    parts.append(_box(left, 40, 150, 34, [svc.get("label") or "Service"]))
    disc = (state.get("disconnect") or {}).get("label")
    parts.append(_seg(left + 75, 74, left + 75, 84))
    parts.append(_box(left, 84, 150, 22 if not disc else 22, [disc or "Main disconnect"], fill="#fff7e6"))
    parts.append(_seg(left + 150, bus_y - 15, width - 20, bus_y - 15, _BORDER, 3))
    parts.append(f'<text x="{left + 160}" y="{bus_y - 20}" font-size="9" fill="{_MUTED}">Line bus</text>')
    if state.get("spd"):
        parts.append(_small_box(left + 75, 130, "SPD Type 2", 90, 22, fill="#eef6ff"))
        parts.append(_seg(left + 75, 106, left + 75, 130, _MUTED, 1))
    x = left + 190
    for p, w in zip(pumps, widths, strict=True):
        cx = x + col_w / 2
        parts.append(_seg(cx, bus_y - 15, cx, bus_y + 5))
        parts.append(_small_box(cx, bus_y + 5, str(p.get("breaker") or "Breaker")))
        y = bus_y + 31
        topo = p.get("topology") or "across_the_line"
        if topo == "vfd_bypass":
            bx = cx + bypass_extra
            parts.append(_seg(cx, y, cx, y + 14))
            parts.append(_seg(cx, y + 7, bx, y + 7))
            parts.append(_seg(bx, y + 7, bx, y + 20))
            parts.append(_small_box(cx, y + 14, f"{p.get('tag')}-K1 line", fill="#fff7e6"))
            parts.append(_seg(cx, y + 40, cx, y + 54))
            parts.append(_small_box(cx, y + 54, str(p.get("vfd") or "VFD"), fill="#fff7e6"))
            parts.append(_seg(cx, y + 80, cx, y + 94))
            parts.append(_small_box(cx, y + 94, f"{p.get('tag')}-K2 output", fill="#fff7e6"))
            parts.append(_small_box(bx, y + 20, f"{p.get('tag')}-K3 bypass", fill="#fdecea"))
            parts.append(_seg(bx, y + 46, bx, y + 74))
            parts.append(_small_box(bx, y + 74, str(p.get("overload") or "OL"), fill="#fdecea"))
            parts.append(_seg(bx, y + 100, bx, y + 140))
            parts.append(_seg(cx, y + 120, cx, y + 140))
            parts.append(_seg(cx, y + 140, bx, y + 140))
            parts.append(f'<text x="{(cx + bx) / 2:.0f}" y="{y + 134:.0f}" text-anchor="middle" font-size="8" '
                         f'fill="{_MUTED}">K2 / K3 interlocked</text>')
            parts.append(_seg(cx, y + 140, cx, y + 180))
            y = y + 180
        else:
            parts.append(_seg(cx, y, cx, y + 14))
            if topo in ("vfd",):
                parts.append(_small_box(cx, y + 14, str(p.get("vfd") or "VFD"), fill="#fff7e6"))
                y += 40
            elif topo == "soft_starter":
                parts.append(_small_box(cx, y + 14, str(p.get("contactor") or "Soft starter"), fill="#fff7e6"))
                y += 40
            else:
                parts.append(_small_box(cx, y + 14, str(p.get("contactor") or "Contactor")))
                parts.append(_seg(cx, y + 40, cx, y + 54))
                parts.append(_small_box(cx, y + 54, str(p.get("overload") or "OL")))
                y += 80
            parts.append(_seg(cx, y, cx, y + 40))
            y += 40
        parts.append(_motor(cx, y + 18, f"{p.get('tag')} {p.get('label') or ''}".strip()))
        if p.get("sub"):
            parts.append(f'<text x="{cx:.0f}" y="{y + 64:.0f}" text-anchor="middle" font-size="9" '
                         f'fill="{_MUTED}">{_esc(p["sub"])}</text>')
        x += w
    for xf in xfmrs:
        cx = x + col_w / 2
        parts.append(_seg(cx, bus_y - 15, cx, bus_y + 5))
        parts.append(_small_box(cx, bus_y + 5, "Breaker"))
        parts.append(_seg(cx, bus_y + 31, cx, bus_y + 45))
        label = xf.get("label") or "Transformer"
        # Two lines when the label is longer than the box: "Control transformer" / "350 VA".
        words = label.split()
        lines = [label] if len(label) <= 18 or len(words) < 3 else [" ".join(words[:-2]), " ".join(words[-2:])]
        parts.append(_box(int(cx - 64), bus_y + 45, 128, 40, lines, fill="#eef6ff"))
        x += col_w
    if not pumps and not xfmrs:
        parts.append(f'<text x="{left + 190}" y="{bus_y + 40}" font-size="11" fill="{_MUTED}">No motor circuits.</text>')
    return _wrap(int(width), height, "".join(parts), title)


def _ladder_element(x, y, el):
    kind = el.get("type")
    label = _esc(el.get("label", ""))
    out = []
    if kind in ("no", "nc"):
        # The contact's slot is as wide as its label, so adjacent labels never run together.
        slot = max(12, int(4.6 * len(el.get("label", ""))) + 4)
        bx = x + (slot - 12) / 2
        out.append(_seg(x, y, bx, y, _INK, 1.5))
        out.append(_seg(bx + 12, y, x + slot, y, _INK, 1.5))
        out.append(_seg(bx, y - 9, bx, y + 9, _INK))
        out.append(_seg(bx + 12, y - 9, bx + 12, y + 9, _INK))
        if kind == "nc":
            out.append(_seg(bx - 2, y + 9, bx + 14, y - 9, _INK, 1.5))
        out.append(f'<text x="{x + slot / 2:.0f}" y="{y - 13}" text-anchor="middle" font-size="8" '
                   f'fill="{_MUTED}">{label}</text>')
        return out, slot
    if kind in ("coil", "light"):
        r = 10
        fill = {"green": "#c6f6d5", "red": "#fed7d7"}.get(el.get("color"), "#ffffff")
        out.append(f'<circle cx="{x + r}" cy="{y}" r="{r}" fill="{fill if kind == "light" else "#ffffff"}" '
                   f'stroke="{_INK}" stroke-width="1.5"/>')
        if kind == "light":
            out.append(_seg(x + r - 6, y - 6, x + r + 6, y + 6, _INK, 1))
            out.append(_seg(x + r - 6, y + 6, x + r + 6, y - 6, _INK, 1))
        out.append(f'<text x="{x + r}" y="{y - 14}" text-anchor="middle" font-size="8" fill="{_MUTED}">{label}</text>')
        return out, 2 * r
    # a labeled box for anything else (selector positions, controller outputs)
    w = max(40, 6 * len(el.get("label", "")))
    out.append(f'<rect x="{x}" y="{y - 9}" width="{w}" height="18" rx="3" fill="{_BOX}" stroke="{_BORDER}"/>')
    out.append(f'<text x="{x + w / 2:.0f}" y="{y + 4}" text-anchor="middle" font-size="8" fill="{_INK}">{label}</text>')
    return out, w


def control_ladder_svg(state: dict[str, Any] | None = None) -> str:
    """Control ladder. ``state`` = ``{title, supply, rungs:[{label, elements:[
    {type: no|nc|coil|light|box, label, color}]}]}``. Contacts run left to right
    from the left rail; the last coil or light sits against the right rail."""
    state = state or {}
    rungs = state.get("rungs") or []
    title = state.get("title") or "Control Ladder"
    left, right = 70, 640
    top = 70
    row_h = 44
    height = top + max(len(rungs), 1) * row_h + 40
    parts = [
        f'<text x="{left}" y="{top - 26}" font-size="10" fill="{_MUTED}">{_esc(state.get("supply") or "")}</text>',
        _seg(left, top - 14, left, top + max(len(rungs), 1) * row_h - 20, _INK, 2),
        _seg(right, top - 14, right, top + max(len(rungs), 1) * row_h - 20, _INK, 2),
        f'<text x="{left - 6}" y="{top - 18}" text-anchor="end" font-size="9" fill="{_INK}">X1</text>',
        f'<text x="{right + 6}" y="{top - 18}" font-size="9" fill="{_INK}">X2</text>',
    ]
    y = top
    for i, rung in enumerate(rungs, start=1):
        parts.append(f'<text x="{left - 10}" y="{y + 4}" text-anchor="end" font-size="9" fill="{_MUTED}">{i}</text>')
        els = rung.get("elements") or []
        coils = [e for e in els if e.get("type") in ("coil", "light")]
        contacts = [e for e in els if e.get("type") not in ("coil", "light")]
        x = left + 20
        parts.append(_seg(left, y, x, y, _INK, 1.5))
        for el in contacts:
            svg, w = _ladder_element(x, y, el)
            parts += svg
            nx = x + w + 18
            parts.append(_seg(x + w, y, nx, y, _INK, 1.5))
            x = nx
        end = right - 40
        parts.append(_seg(x, y, end, y, _INK, 1.5))
        for el in coils[-1:]:
            svg, w = _ladder_element(end, y, el)
            parts += svg
            parts.append(_seg(end + w, y, right, y, _INK, 1.5))
        if not coils:
            parts.append(_seg(end, y, right, y, _INK, 1.5))
        if rung.get("label"):
            parts.append(f'<text x="{right + 10}" y="{y + 4}" font-size="9" fill="{_INK}">{_esc(rung["label"])}</text>')
        y += row_h
    if not rungs:
        parts.append(f'<text x="{left + 20}" y="{top + 10}" font-size="11" fill="{_MUTED}">No control circuits.</text>')
    return _wrap(right + 170, height, "".join(parts), title)


# ---------------------------------------------------------------------------
# The Fountain Designer page (public/js/fountain_designer/chart.js)
# ---------------------------------------------------------------------------

# chart.js's ATTRIBUTES allow-list, lower case. Its node fallback refuses the
# whole drawing over one attribute off this list, so page_svg drops the rest;
# tests/test_drawings_duty_chart.py reads the list out of chart.js and checks
# this copy against it.
_PAGE_ATTRIBUTES = frozenset({
    "viewbox", "width", "height", "x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "r", "rx", "ry", "d", "points",
    "fill", "fill-opacity", "fill-rule", "stroke", "stroke-width", "stroke-dasharray", "stroke-linecap",
    "stroke-linejoin", "stroke-opacity", "opacity", "transform", "class", "id", "role", "aria-label",
    "aria-hidden", "focusable", "font-size", "font-weight", "font-family", "text-anchor", "dominant-baseline",
    "dx", "dy", "clip-path", "marker-end", "marker-start", "marker-mid", "markerwidth", "markerheight", "refx",
    "refy", "orient", "offset", "stop-color", "stop-opacity", "gradientunits", "patternunits",
    "preserveaspectratio", "xmlns", "vector-effect", "data-point", "data-series",
})

# The duty chart's frame, in viewBox units: legend row, NPSH caption row, then the plot.
_DUTY_W, _DUTY_H = 480, 300
# Width of one label character in viewBox units, sized for the widest the page could set it (13 px
# bold) so the legend and the duty label still clear their neighbors there.
_CHAR_W = 7.6
_PLOT_L, _PLOT_R, _PLOT_T, _PLOT_B = 52, 464, 40, 256


def _num(value: Any) -> float | None:
    """A finite float, or None for anything else (None, NaN, inf, bool, junk)."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _c(value: float) -> str:
    """A coordinate rounded to 1 decimal, without a trailing ``.0``."""
    text = f"{round(value, 1) + 0.0:.1f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _points(raw: Any) -> list[tuple[float, float]]:
    """``[{flow_gpm, head_ft}]`` as (flow, head) pairs sorted by flow; junk,
    negative and non-finite points are dropped."""
    out = []
    for p in raw if isinstance(raw, (list, tuple)) else []:
        if not isinstance(p, dict):
            continue
        q, h = _num(p.get("flow_gpm")), _num(p.get("head_ft"))
        if q is None or h is None or q < 0 or h < 0:
            continue
        out.append((q, h))
    out.sort()
    return out


def _nice_axis(peak: float) -> tuple[float, float]:
    """(axis max, tick step): about five round ticks covering ``peak`` + 10%."""
    target = max(peak, 0.0) * 1.1
    if target <= 0:
        return 1.0, 0.2
    raw = target / 5
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw * (1 - 1e-12))
    return math.ceil(target / step - 1e-9) * step, step


def _tick(value: float) -> str:
    return f"{round(value, 6):g}"


def _label(value: Any, fallback: str, limit: int = 28) -> str:
    ok = isinstance(value, (str, int, float)) and not isinstance(value, bool)
    text = (_XML_ILLEGAL.sub("", str(value)).strip() if ok else "") or fallback
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _gpm(q: float) -> str:
    return f"{q:.0f}" if q >= 10 else f"{q:.1f}"


def _text(x: float, y: float, body: str, size: int = 11, extra: str = "", cls: str = "fd-label") -> str:
    return (
        f'<text class="{cls}" x="{_c(x)}" y="{_c(y)}"{extra} font-size="{size}" '
        f'fill="currentColor">{_esc(body)}</text>'
    )


def _polyline(points: list[str], cls: str, series: str, width: float, dash: str = "") -> str:
    dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
    return (
        f'<polyline class="{cls}" data-series="{series}" points="{" ".join(points)}" fill="none" '
        f'stroke="currentColor" stroke-width="{width:g}"{dash_attr}/>'
    )


def _empty_duty_svg(title: str) -> str:
    aria = f"{title}: no duty point yet"
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {_DUTY_W} 120" role="img" '
        f'aria-label="{_esc(aria)}"><title>{_esc(aria)}</title>'
        + _text(_DUTY_W / 2, 64, "No duty point yet", 13, ' text-anchor="middle" fill-opacity="0.7"')
        + "</svg>"
    )


def _duty_state(state: Any) -> dict[str, Any]:
    """The duty chart's inputs, cleaned: only finite, non-negative numbers survive."""
    state = state if isinstance(state, dict) else {}

    def part(key):
        value = state.get(key)
        return value if isinstance(value, dict) else {}

    pumps = []
    raw_pumps = state.get("pumps")
    for p in raw_pumps if isinstance(raw_pumps, (list, tuple)) else []:
        if not isinstance(p, dict):
            continue
        curve = _points(p.get("curve"))
        if len(curve) < 2:
            continue
        speeds = set()
        raw_speeds = p.get("speeds")
        for s in raw_speeds if isinstance(raw_speeds, (list, tuple)) else []:
            pct = _num(s)
            if pct is not None and 0 < pct < 100:
                speeds.add(pct)
        pumps.append({"label": p.get("label"), "curve": curve, "speeds": sorted(speeds, reverse=True)[:6]})
    system = _points(state.get("system"))

    duty = part("duty")
    dq, dh = _num(duty.get("flow_gpm")), _num(duty.get("head_ft"))
    bep = part("bep")
    lo, hi = _num(bep.get("flow_lo_gpm")), _num(bep.get("flow_hi_gpm"))
    window = None
    if lo is not None and hi is not None:
        lo, hi = sorted((max(lo, 0.0), max(hi, 0.0)))
        window = (lo, hi) if hi > lo else None
    design_q = _num(part("design").get("flow_gpm"))
    npsh = part("npsh")
    avail, req, margin = (_num(npsh.get(k)) for k in ("available_ft", "required_ft", "margin_ft"))
    if margin is None and avail is not None and req is not None:
        margin = avail - req
    return {
        "title": _label(state.get("title"), "Duty point", 60),
        "pumps": pumps,
        "system": system if len(system) >= 2 else [],
        # Zero flow is the solve finding no operating point (a starved or dead-headed pump), not a duty
        # point: drawn, it would read as a valid one on the head axis.
        "duty": (dq, dh) if dq is not None and dh is not None and dq > 0 and dh >= 0 else None,
        "bep": window,
        "design": design_q if design_q is not None and design_q > 0 else None,
        "npsh": (avail, req, margin),
    }


def _npsh_caption(avail: float | None, req: float | None, margin: float | None) -> str:
    """``NPSH margin 12.3 ft (available 18.0, required 5.7)``, with what is known."""
    bits = []
    if avail is not None:
        bits.append(f"available {avail:.1f}")
    if req is not None:
        bits.append(f"required {req:.1f}")
    if margin is not None:
        return f"NPSH margin {margin:.1f} ft" + (f" ({', '.join(bits)})" if bits else "")
    return f"NPSH {', '.join(bits)} ft" if bits else ""


def pump_duty_svg(state: dict[str, Any] | None = None) -> str:
    """The duty-point chart for the Fountain Designer page. US units throughout.

    ``state`` = ``{title, pumps:[{label, curve:[{flow_gpm, head_ft}], speeds:[%]}],
    system:[{flow_gpm, head_ft}], duty:{flow_gpm, head_ft},
    bep:{flow_lo_gpm, flow_hi_gpm}, npsh:{available_ft, required_ft, margin_ft},
    design:{flow_gpm}}``; every key is optional. A pump's ``curve`` is its
    full-speed curve in any order; each of its ``speeds`` (percent, below 100)
    adds a dotted curve by the affinity laws (Q*s, H*s^2). Junk, None, NaN and
    inf read as absent, so the function never raises; with no curve and no duty
    point it returns a small chart that says "No duty point yet". The NPSH
    margin is ``available - required`` when not given.

    Drawn in ``currentColor`` with the page's classes (``fd-curve-pump``,
    ``fd-curve-system``, ``fd-point``, ``fd-bep``, ``fd-grid``), sized by
    ``viewBox`` alone, with only the elements and attributes ``chart.js`` allows.
    """
    s = _duty_state(state)
    pumps, system, duty, bep, design_q = s["pumps"], s["system"], s["duty"], s["bep"], s["design"]
    if not pumps and not system and duty is None:
        return _empty_duty_svg(s["title"])

    # -- scale: every curve, the duty point, the BEP window and the design flow
    flows = [q for p in pumps for q, _ in p["curve"]] + [q for q, _ in system]
    heads = [h for p in pumps for _, h in p["curve"]] + [h for _, h in system]
    if duty:
        flows.append(duty[0])
        heads.append(duty[1])
    if bep:
        flows.append(bep[1])
    if design_q is not None:
        flows.append(design_q)
    x_max, x_step = _nice_axis(max(flows))
    y_max, y_step = _nice_axis(max(heads))

    def sx(q: float) -> float:
        return _PLOT_L + q / x_max * (_PLOT_R - _PLOT_L)

    def sy(h: float) -> float:
        return _PLOT_B - h / y_max * (_PLOT_B - _PLOT_T)

    def pts(curve):
        return [f"{_c(sx(q))},{_c(sy(h))}" for q, h in curve]

    parts = []
    # -- BEP window, grid and ticks, axes ------------------------------------
    if bep:
        x0, x1 = sx(bep[0]), sx(bep[1])
        parts.append(
            f'<rect class="fd-bep" x="{_c(x0)}" y="{_PLOT_T}" width="{_c(x1 - x0)}" '
            f'height="{_PLOT_B - _PLOT_T}" fill="currentColor" fill-opacity="0.08"/>'
        )
        # On the side of the window away from the design-flow line, which usually runs near its centre.
        xd = sx(design_q) if design_q is not None else None
        if xd is not None and x0 - 2 <= xd <= x1 + 2:
            left = xd > (x0 + x1) / 2
            parts.append(_text(x0 + 3 if left else x1 - 3, _PLOT_B - 6, "BEP", 10,
                               ('' if left else ' text-anchor="end"') + ' fill-opacity="0.7"'))
        else:
            parts.append(_text((x0 + x1) / 2, _PLOT_B - 6, "BEP", 10, ' text-anchor="middle" fill-opacity="0.7"'))
    for i in range(round(y_max / y_step) + 1):
        y = _c(sy(i * y_step))
        parts.append(
            f'<line class="fd-grid" x1="{_PLOT_L}" y1="{y}" x2="{_PLOT_R}" y2="{y}" '
            f'stroke="currentColor" stroke-opacity="0.15"/>'
        )
        parts.append(_text(_PLOT_L - 6, sy(i * y_step), _tick(i * y_step), 11,
                           ' text-anchor="end" dominant-baseline="middle"'))
    for i in range(round(x_max / x_step) + 1):
        x = sx(i * x_step)
        if i:
            parts.append(
                f'<line class="fd-grid" x1="{_c(x)}" y1="{_PLOT_T}" x2="{_c(x)}" y2="{_PLOT_B}" '
                f'stroke="currentColor" stroke-opacity="0.08"/>'
            )
        parts.append(_text(x, _PLOT_B + 16, _tick(i * x_step), 11, ' text-anchor="middle"'))
    parts.append(
        f'<line class="fd-axis" x1="{_PLOT_L}" y1="{_PLOT_T}" x2="{_PLOT_L}" y2="{_PLOT_B}" stroke="currentColor"/>'
        f'<line class="fd-axis" x1="{_PLOT_L}" y1="{_PLOT_B}" x2="{_PLOT_R}" y2="{_PLOT_B}" stroke="currentColor"/>'
    )
    mid_y = _c((_PLOT_T + _PLOT_B) / 2)
    parts.append(_text((_PLOT_L + _PLOT_R) / 2, _DUTY_H - 8, "Flow (gpm)", 12, ' text-anchor="middle"'))
    parts.append(_text(14, (_PLOT_T + _PLOT_B) / 2, "Head (ft)", 12,
                       f' text-anchor="middle" transform="rotate(-90 14 {mid_y})"'))

    # -- design marker, curves, duty point -----------------------------------
    if design_q is not None:
        x = sx(design_q)
        parts.append(
            f'<line class="fd-design" x1="{_c(x)}" y1="{_PLOT_T}" x2="{_c(x)}" y2="{_PLOT_B}" '
            f'stroke="currentColor" stroke-width="1.5" stroke-dasharray="3 3" stroke-opacity="0.6"/>'
        )
        # At the top of the plot, where the reduced-speed tails never reach, and on the side of the line
        # away from the BEP window's label.
        go_left = x > _PLOT_R - 90 or (bep is not None and sx((bep[0] + bep[1]) / 2) > x and x - 90 > _PLOT_L)
        parts.append(_text(x - 4 if go_left else x + 4, _PLOT_T + 12, f"Design {_gpm(design_q)} gpm", 10,
                           (' text-anchor="end"' if go_left else "") + ' fill-opacity="0.8"'))
    if system:
        parts.append(_polyline(pts(system), "fd-curve-system", "system", 2, "6 4"))
    for i, p in enumerate(pumps, start=1):
        parts.append(_polyline(pts(p["curve"]), "fd-curve-pump", f"pump-{i}", 2.5))
        for pct in p["speeds"]:
            k = pct / 100
            scaled = [(q * k, h * k * k) for q, h in p["curve"]]
            parts.append(_polyline(pts(scaled), "fd-curve-pump", f"pump-{i}-{pct:g}", 1.5, "2 3"))
            q0, h0 = scaled[0]
            parts.append(_text(sx(q0) + 4, sy(h0) - 4, f"{pct:g}%", 10, ' fill-opacity="0.8"'))
    if duty:
        cx, cy = sx(duty[0]), sy(duty[1])
        parts.append(
            f'<circle class="fd-point" data-point="duty" cx="{_c(cx)}" cy="{_c(cy)}" r="6" fill="currentColor"/>'
        )
        text = f"{_gpm(duty[0])} gpm @ {duty[1]:.1f} ft"
        # Level with the point: the falling pump curve and the rising system curve open apart on both
        # sides of it, so a label set beside the point sits between them rather than across one. Left
        # when it fits, else right.
        width = len(text) * _CHAR_W
        left = cx - 12 - width > _PLOT_L + 4
        parts.append(_text(cx - 12 if left else cx + 12, cy, text, 12,
                           (' text-anchor="end"' if left else "") + ' dominant-baseline="middle" font-weight="700"',
                           "fd-label fd-point-label"))
    elif pumps or system:
        parts.append(_text((_PLOT_L + _PLOT_R) / 2, (_PLOT_T + _PLOT_B) / 2, "No duty point yet", 13,
                           ' text-anchor="middle" dominant-baseline="middle" fill-opacity="0.7"'))

    # -- legend (top row, right-aligned) and NPSH caption (second row) -------
    entries = [
        ("fd-curve-pump", 2.5, "", _label(p["label"], "Pump", 22 if len(pumps) == 1 else 14)) for p in pumps[:3]
    ]
    if any(p["speeds"] for p in pumps):
        entries.append(("fd-curve-pump", 1.5, "2 3", "VFD speed"))
    if system:
        entries.append(("fd-curve-system", 2, "6 4", "System"))
    if len(pumps) > 3:
        entries.insert(3, ("", 0, "", f"+{len(pumps) - 3} more"))

    def width(e):
        return (24 if e[0] else 0) + len(e[3]) * _CHAR_W + 14

    while sum(width(e) for e in entries) - 14 > _PLOT_R - _PLOT_L:
        drawn = [i for i, e in enumerate(entries) if e[0] == "fd-curve-pump" and not e[2]]
        if len(drawn) <= 1:
            break
        # fold the last drawn pump into the "+N more" entry rather than drop it unannounced
        entries.pop(drawn[-1])
        more = next((i for i, e in enumerate(entries) if not e[0]), None)
        hidden = len(pumps) - len([e for e in entries if e[0] == "fd-curve-pump" and not e[2]])
        if more is None:
            entries.insert(drawn[-1], ("", 0, "", f"+{hidden} more"))
        else:
            entries[more] = ("", 0, "", f"+{hidden} more")
    widths = [width(e) for e in entries]
    x = max(_PLOT_L, _PLOT_R - sum(widths) + 14)
    for (cls, stroke_w, dash, text), w in zip(entries, widths, strict=True):
        if not cls:  # the "+N more" entry: words only
            parts.append(_text(x, 12, text, 11, ' dominant-baseline="middle" fill-opacity="0.8"'))
            x += w
            continue
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        parts.append(
            f'<line class="{cls}" x1="{_c(x)}" y1="12" x2="{_c(x + 20)}" y2="12" stroke="currentColor" '
            f'stroke-width="{stroke_w:g}"{dash_attr}/>'
        )
        parts.append(_text(x + 24, 12, text, 11, ' dominant-baseline="middle"'))
        x += w
    caption = _npsh_caption(*s["npsh"])
    if caption:
        parts.append(_text(_PLOT_L, 30, caption, 11, ' dominant-baseline="middle"', "fd-label fd-npsh"))

    if duty:
        aria = f"{s['title']}: {_gpm(duty[0])} gpm at {duty[1]:.1f} ft"
    else:
        aria = f"{s['title']}: pump and system curves, no duty point"
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {_DUTY_W} {_DUTY_H}" role="img" '
        f'aria-label="{_esc(aria)}"><title>{_esc(aria)}</title>{"".join(parts)}</svg>'
    )


# The fixed print inks and tints page_svg maps onto the page's theme color.
_INKS = {_INK, _BORDER, _ACCENT, "#000", "#000000", "black"}
_TINTS = {_BOX, "#fff7e6", "#eef6ff", "#fdecea"}  # box fills: a faint wash so boxes read in dark mode
_LAMPS = {"#c6f6d5", "#fed7d7"}  # the ladder's green and red pilot lights
_WHITES = {"#ffffff", "#fff", "white"}

_TAG_RE = re.compile(r'<([A-Za-z][\w:-]*)((?:\s+[^\s=/>]+\s*=\s*"[^"]*")*)\s*(/?)>')
_ATTR_RE = re.compile(r'([^\s=]+)\s*=\s*"([^"]*)"')


def _page_attrs(attrs: list[tuple[str, str]], root: bool) -> list[tuple[str, str]]:
    """The attributes chart.js keeps, with print colors mapped to ``currentColor``."""
    out = []
    extra = {}
    for key, value in attrs:
        low = key.lower()
        if low not in _PAGE_ATTRIBUTES or (root and low in ("width", "height")):
            continue
        if low in ("fill", "stroke"):
            color = value.strip().lower()
            if color in _INKS:
                value = "currentColor"
            elif color == _MUTED:
                value, extra[f"{low}-opacity"] = "currentColor", "0.7"
            elif color in _TINTS:
                value, extra[f"{low}-opacity"] = "currentColor", "0.06"
            elif color in _LAMPS:
                value, extra[f"{low}-opacity"] = "currentColor", "0.25"
            elif color in _WHITES:
                value = "none"
        out.append((key, value))
    present = {k.lower() for k, _ in out}
    return out + [(k, v) for k, v in extra.items() if k not in present]


def page_svg(svg: str | None) -> str | None:
    """One of this module's print drawings, made fit for the Fountain Designer page.

    ``chart.js`` refuses a drawing outright over one ``style`` or unknown
    attribute, and the print colors are fixed for white paper. This drops the
    root ``style``/``width``/``height`` (the page sizes by ``viewBox``), the
    full-size white background, and every attribute off chart.js's list (e.g.
    ``markerUnits``), and maps the print inks on ``fill``/``stroke`` to
    ``currentColor``: box tints become a faint ``currentColor`` wash and white
    fills become ``none``. Text content is untouched. None or empty in, None
    out. Built for this module's own output, not for arbitrary XML.
    """
    if not isinstance(svg, str) or not svg.strip():
        return None
    source = svg.strip()
    view = re.search(r'<svg\b[^>]*\bviewBox="\s*0\s+0\s+([\d.]+)\s+([\d.]+)\s*"', source)
    size = (_num(view.group(1)), _num(view.group(2))) if view else None
    seen_root = False

    def rewrite(m: re.Match) -> str:
        nonlocal seen_root
        name, attrs, close = m.group(1), _ATTR_RE.findall(m.group(2)), m.group(3)
        root = name.lower() == "svg" and not seen_root
        seen_root = seen_root or root
        if name.lower() == "rect" and size:
            a = {k.lower(): v.strip().lower() for k, v in attrs}
            if (
                a.get("fill") in _WHITES
                and (_num(a.get("x") or "0"), _num(a.get("y") or "0")) == (0, 0)
                and (_num(a.get("width")), _num(a.get("height"))) == size
            ):
                return ""
        body = "".join(f' {k}="{v}"' for k, v in _page_attrs(attrs, root))
        return f"<{name}{body}{'/' if close else ''}>"

    out = _TAG_RE.sub(rewrite, source)
    # Every print drawing defines <marker id="arrow">. On one page two of them would share the id, and
    # url(#arrow) resolves to the first in the document (in a hidden tab, or in the other theme), so
    # each drawing's ids get a suffix of its own: the same drawing twice gets the same, harmlessly.
    suffix = f"{zlib.crc32(source.encode('utf-8')) & 0xFFFFFF:06x}"
    for ident in sorted(set(re.findall(r'\bid="([^"]+)"', out)), key=len, reverse=True):
        out = out.replace(f'id="{ident}"', f'id="{ident}-{suffix}"').replace(f"url(#{ident})", f"url(#{ident}-{suffix})")
    return out
