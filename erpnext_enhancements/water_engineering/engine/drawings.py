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
