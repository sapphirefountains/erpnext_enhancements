"""The paper outputs of a Water Feature Design (Fountain Designer phase G).

Every print format of a schema-2 design, and the submittal packet, reads one context built here:
``build(doc, model, stored, bom)``, where ``model`` is ``model_json``, ``stored`` is
``results_json`` (the last derive: every ``CalcResult``, the per-row values, rollups, charts,
outputs and phase L/S/G's detail under ``electrical``, ``controls``, ``structural`` and
``paper``) and ``bom`` is the ``bom`` table. **Nothing here recomputes the engine**: it picks
numbers out of what the derive stored and formats them. The only arithmetic is presentation:
sums of stored rows (budget subtotals, total weights) and a volume divided by a flow for the
turnover line.

The context is a set of **sheets**, each a list of **blocks** the templates render with one
macro (``setup_print_formats.BLOCK_MACRO``): ``table``, ``facts``, ``drawing``, ``list``,
``text``, ``calc`` and ``note``. Every cell arrives as a finished string with its inline style,
so a template never formats a number, and a table with no rows renders its ``empty`` sentence
instead of an empty table. Text is plain Unicode; the templates escape it (frappe's Jinja has
no autoescape), so an ampersand here prints once.

The ``OUTPUTS`` list (page key, label, Print Format) is what the Fountain Designer's Outputs
step links to: ``derive.SCHEDULES`` and ``api/designer.boot()`` both read it.

No frappe import: ``packet.we_print`` is the frappe-side adapter that loads the record, looks
up the Items and calls :func:`build`. ``tests/test_water_prints.py`` renders every format with
real Jinja over the three demo designs and a regulated spa.
"""

from __future__ import annotations

import math
import re
import zlib
from typing import Any

from erpnext_enhancements import print_style as ps

DOCTYPE = "Water Feature Design"
PILLAR = "design"

PF_RESULTS = "Water Feature Design - Results"
PF_AUDIT = "Water Feature Design - Calculation Audit"
PF_SCHEDULES = "Water Feature Design - Schedules"
PF_ELECTRICAL = "Water Feature Design - Electrical Schedule"
PF_CONTROLS = "Water Feature Design - Controls BOM"
PF_STRUCTURAL = "Water Feature Design - Structural Takeoff"
PF_DRAWINGS = "Water Feature Design - Design Drawings"
PF_DESIGN_CALC = "Water Feature Design - Design Calculations"
PF_OM = "Water Feature Design - Operations and Maintenance"
PF_WINTER = "Water Feature Design - Winterization"
PF_BUDGET = "Water Feature Design - Budget"
PF_PACKET = "Water Feature Design - Submittal Packet"

# Page key, label, Print Format: the Outputs step's list, in the order it shows them.
OUTPUTS = (
    ("equipment", "Equipment schedule", PF_SCHEDULES),
    ("piping", "Piping schedule", PF_SCHEDULES),
    ("fittings", "Fitting schedule and cut list", PF_SCHEDULES),
    ("electrical", "Electrical panel schedule", PF_ELECTRICAL),
    ("controls", "Controls bill of materials", PF_CONTROLS),
    ("structural", "Structural takeoff", PF_STRUCTURAL),
    ("drawings", "Design drawings", PF_DRAWINGS),
    ("calcs", "Design calculations", PF_DESIGN_CALC),
    ("om", "Operations and maintenance per year", PF_OM),
    ("winterization", "Winterization and start-up", PF_WINTER),
    ("budget", "Budget", PF_BUDGET),
)

# Print Format -> (eyebrow, title suffix, sheets). The packet is composed in setup_print_formats.
FORMATS = {
    PF_RESULTS: ("RESULTS", "Design results", ("results",)),
    PF_AUDIT: ("CALCULATION AUDIT", "Calculation audit", ("audit",)),
    PF_SCHEDULES: ("SCHEDULES", "Schedules", ("equipment", "piping", "fittings")),
    PF_ELECTRICAL: ("ELECTRICAL", "Electrical schedule", ("electrical",)),
    PF_CONTROLS: ("CONTROLS", "Controls bill of materials", ("controls",)),
    PF_STRUCTURAL: ("STRUCTURAL", "Structural takeoff", ("structural",)),
    PF_DRAWINGS: ("DRAWINGS", "Design drawings", ("drawings",)),
    PF_DESIGN_CALC: ("PLAN CHECK", "Design calculations", ("calcs",)),
    PF_OM: ("OPERATIONS", "Operations and maintenance", ("om",)),
    PF_WINTER: ("SEASONAL", "Winterization and start-up", ("winterization",)),
    PF_BUDGET: ("BUDGET", "Budget", ("budget",)),
}
# The health-department packet's sheets, after its cover. The budget and the O&M are internal.
PACKET_SHEETS = ("basis", "equipment", "piping", "fittings", "electrical", "structural", "drawings", "calcs", "audit")

DASH = "—"
GREEN = "#2f8557"
MUTED = "#6c7a83"

# ----------------------------------------------------------------------------- formatting


def _num(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def fnum(value: Any, digits: int = 1, unit: str = "") -> str:
    """``1,234.5 gpm``; a dash for nothing. Every number on paper goes through here or :func:`money`."""
    v = _num(value)
    if v is None:
        return DASH
    text = f"{v:,.{digits}f}"
    if text.startswith("-") and float(text.replace(",", "")) == 0:
        text = text[1:]
    return f"{text} {unit}" if unit else text


def fqty(value: Any) -> str:
    """A quantity: whole numbers bare, others to two places with the zeros trimmed."""
    v = _num(value)
    if v is None:
        return DASH
    if abs(v - round(v)) < 1e-9:
        return f"{round(v):,}"
    return f"{v:,.2f}".rstrip("0").rstrip(".")


def money(value: Any, cents: bool = True) -> str:
    v = _num(value)
    if v is None:
        return DASH
    text = f"${abs(v):,.{2 if cents else 0}f}"
    return f"-{text}" if v < 0 and round(abs(v), 2 if cents else 0) else text


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return fnum(value, 2)
    return str(value)


def _label(key: str) -> str:
    return str(key or "").replace("_", " ").strip().capitalize()


def _sanitizer(key: Any) -> str:
    return "No disinfectant" if str(key or "none").lower() == "none" else _label(key)


# ----------------------------------------------------------------------------- blocks

TH = ps.th(PILLAR)
TH_RIGHT = ps.th(PILLAR, right=True)
TONES = {
    "": "",
    "strong": ps.STRONG,
    "pass": f"font-weight:700;color:{GREEN}",
    "fail": ps.FAIL,
    "open": f"font-weight:700;color:{ps.BAHAMA_BLUE}",
    "muted": f"color:{MUTED}",
}


def cell(text: Any, *, num: bool = False, tone: str = "", href: str | None = None) -> dict:
    """One table cell: finished text (a dash for nothing) and its inline style."""
    style = (ps.TD_RIGHT if num else ps.TD) + (";" + TONES[tone] if TONES.get(tone) else "")
    out = {"text": _text(text) or DASH, "style": style}
    if href and re.match(r"^https?://[^\s\"'<>]+$", str(href)):
        out["href"] = str(href)
    return out


def blank(n: int = 1) -> list:
    """Empty cells, for the spacers in a totals row."""
    return [{"text": "", "style": ps.TD} for _ in range(n)]


def table(title: str, columns: list, rows: list, *, empty: str, note: str = "", foot: list | None = None) -> dict:
    """``columns``: ``(label, numeric)`` pairs. ``rows``: lists of :func:`cell` (or ``{"group": label}``)."""
    cols = [{"label": label, "style": TH_RIGHT if numeric else TH} for label, numeric in columns]
    body = []
    for r in rows:
        if isinstance(r, dict) and "group" in r:
            body.append({"group": r["group"], "span": len(cols),
                         "style": f"{ps.TD};{ps.STRONG};padding-top:10px !important;border-bottom:1px solid {ps.DEEP_SEA_BLUE} !important"})
        else:
            body.append({"cells": r})
    return {"kind": "table", "title": title, "columns": cols, "rows": body, "foot": foot or [],
            "empty": empty, "note": note}


def facts(title: str, items: list, *, note: str = "") -> dict:
    """``items``: ``(label, value)`` or ``(label, value, tone)``; a value of None drops the line."""
    out = []
    for it in items:
        label, value = it[0], it[1]
        if value is None or value == "" or value == DASH:
            continue
        tone = it[2] if len(it) > 2 else ""
        out.append({"label": label, "value": str(value), "style": TONES.get(tone, "")})
    return {"kind": "facts", "title": title, "lines": out, "note": note, "empty": "Nothing to show yet."}


def drawing(title: str, svg: Any, key: str, *, empty: str, note: str = "") -> dict:
    return {"kind": "drawing", "title": title, "svg": unique_ids(svg, key), "empty": empty, "note": note}


def bullets(title: str, items: list, *, ordered: bool = False, boxes: bool = False, empty: str = "", note: str = "") -> dict:
    return {"kind": "list", "title": title, "entries": [str(i) for i in items if i], "ordered": ordered, "boxes": boxes,
            "empty": empty or "Nothing to list.", "note": note}


def text_block(title: str, body: str, *, note: str = "") -> dict:
    return {"kind": "text", "title": title, "body": body or "", "note": note, "empty": "Nothing to show yet."}


def note(text: str) -> dict:
    return {"kind": "note", "text": text}


_ID_RE = re.compile(r'\bid="([^"]+)"')


def unique_ids(svg: Any, key: str) -> str | None:
    """Every id in a drawing suffixed with its key and a hash of the drawing, so several drawings share a
    page (the packet, the Design Drawings print) without one's ``url(#arrow)`` finding another's marker.
    ``drawings.page_svg`` (phase E, #1400) suffixes too; a second suffix is harmless."""
    if not isinstance(svg, str) or "<svg" not in svg:
        return None
    suffix = f"{key}{zlib.crc32(svg.encode('utf-8')) & 0xFFFF:04x}"
    out = svg
    for ident in sorted(set(_ID_RE.findall(svg)), key=len, reverse=True):
        out = out.replace(f'id="{ident}"', f'id="{ident}-{suffix}"').replace(f"url(#{ident})", f"url(#{ident}-{suffix})")
        out = out.replace(f'href="#{ident}"', f'href="#{ident}-{suffix}"')
    return out


# ----------------------------------------------------------------------------- reading the stored design


def _d(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _l(value: Any) -> list:
    return value if isinstance(value, list) else []


def _result(stored: dict, prefix: str) -> dict:
    results = _d(stored.get("results"))
    if prefix in results:
        return _d(results[prefix])
    for k, v in results.items():
        if k.split("|")[0].split("#")[0] == prefix:
            return _d(v)
    return {}


def _value(stored: dict, prefix: str) -> Any:
    return _result(stored, prefix).get("value")


def _pump_name(p: dict, index: int, rollups: dict, items: dict) -> str:
    item = p.get("item")
    if item and _d(items.get(item)).get("item_name"):
        return str(items[item]["item_name"])
    if p.get("label"):
        return str(p["label"])
    if index == 0 and rollups.get("selected_pump_label"):
        return str(rollups["selected_pump_label"])
    return str(item or f"{_label(p.get('generic_class') or 'pump')} (generic)")


SLOT_TAGS = {"filter": "F", "uv": "UV", "chlorinator": "CH", "light": "L", "transformer": "T", "autofill": "AF",
             "vfd": "VFD", "bypass": "BV", "surge_arrestor": "SA", "prv": "PRV", "heater": "H", "pump": "P"}
SLOT_LABELS = {"uv": "UV sterilizer", "vfd": "Variable-frequency drive", "prv": "Pressure-reducing valve",
               "surge_arrestor": "Surge arrestor", "autofill": "Autofill"}


class Design:
    """The stored record, read once: the pieces every sheet needs."""

    def __init__(self, doc: dict, model: dict, stored: dict, bom: list, items: dict | None, issues: list | None):
        self.doc = _d(doc)
        self.model = _d(model)
        self.stored = _d(stored)
        self.bom = [r for r in _l(bom) if isinstance(r, dict)]
        self.items = _d(items)
        self.issues = [i for i in _l(issues) if isinstance(i, dict)]
        self.rollups = _d(self.stored.get("rollups"))
        self.values = _d(self.stored.get("values"))
        self.charts = _d(self.stored.get("charts"))
        self.outputs = _d(self.stored.get("outputs"))
        self.electrical = _d(self.stored.get("electrical"))
        self.controls = _d(self.stored.get("controls"))
        self.structural = _d(self.stored.get("structural"))
        self.paper = _d(self.stored.get("paper"))
        self.site = _d(self.model.get("site"))
        self.net = _d(self.model.get("network"))
        self.network = _d(_value(self.stored, "network_operating_point"))
        self.derived = bool(self.stored.get("results"))

    @property
    def name(self) -> str:
        return str(self.doc.get("name") or "")

    @property
    def title(self) -> str:
        return str(self.doc.get("design_title") or self.doc.get("name") or "Water feature design")

    @property
    def regulated(self) -> bool:
        return bool(self.site.get("regulated"))

    def item(self, code: Any) -> dict:
        return _d(self.items.get(code)) if code else {}


# ----------------------------------------------------------------------------- sheets

NOT_DERIVED = "This design has not been worked out yet: save it (or open it in the Fountain Designer) and print again."


def sheet_results(d: Design) -> list:
    r = d.rollups
    verdict = _d(d.stored.get("verdict"))
    blocks = [facts("Key results", [
        ("Verdict", verdict.get("headline"), {"good": "pass", "bad": "fail"}.get(verdict.get("level"), "open")),
        ("Design flow", fnum(r.get("design_flow_gpm"), 1, "gpm")),
        ("Operating flow", fnum(r.get("operating_flow_gpm"), 1, "gpm")),
        ("Total dynamic head", fnum(r.get("tdh_ft"), 1, "ft")),
        ("Selected pump", r.get("selected_pump_label") or r.get("selected_pump")),
        ("Pump power", fnum(r.get("pump_kw"), 2, "kW")),
        ("Water in the basins", fnum(r.get("total_basin_gallons"), 0, "gal")),
        ("Budget (BOM)", money(r.get("budget_total"), cents=False)),
        ("Operations and maintenance", money(r.get("om_cost_per_year"), cents=False) + " a year" if _num(r.get("om_cost_per_year")) else None),
        ("Complete", fnum(r.get("completion_percent"), 0, "%")),
    ], note="" if d.derived else NOT_DERIVED)]
    open_issues = [i for i in d.issues if i.get("severity") in ("blocker", "warning", "info") and not i.get("acknowledged")]
    tone = {"blocker": "fail", "warning": "open", "info": "muted"}
    blocks.append(table("Open issues", [("Severity", False), ("Section", False), ("Issue", False), ("Source", False)], [
        [cell(_label(i.get("severity")), tone=tone.get(i.get("severity"), "")), cell(_label(i.get("section"))),
         cell(str(i.get("title") or "") + (f". Fix: {i['fix_hint']}" if i.get("fix_hint") else "")), cell(i.get("citation") or DASH, tone="muted")]
        for i in open_issues], empty="No open issues."))
    acked = [i for i in d.issues if i.get("acknowledged")]
    if acked:
        blocks.append(table("Signed-off warnings", [("Warning", False), ("By", False), ("On", False), ("Note", False)], [
            [cell(i.get("title")), cell(_d(i.get("acknowledged")).get("by")), cell(_d(i.get("acknowledged")).get("on")),
             cell(_d(i.get("acknowledged")).get("note"))] for i in acked], empty=""))
    return blocks


def _value_text(value: Any, unit: str) -> str:
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if _num(value) is not None:
        v = float(value)
        digits = 0 if abs(v) >= 1000 else (1 if abs(v) >= 10 else (2 if abs(v) >= 0.1 else 4))
        return fnum(v, digits, unit or "")
    if isinstance(value, str):
        return f"{value} {unit}".strip()
    if isinstance(value, list):
        return f"{len(value)} rows"
    if isinstance(value, dict):
        return "see the working"
    return DASH


def sheet_audit(d: Design) -> list:
    blocks = []
    for key, r in _d(d.stored.get("results")).items():
        r = _d(r)
        if not r:
            continue
        name, _, ref = str(key).partition("|")
        inputs = []
        for k, v in _d(r.get("inputs")).items():
            v = _d(v) if isinstance(v, dict) else {"value": v}
            inputs.append([cell(_label(k)), cell(_value_text(v.get("value"), v.get("unit") or ""), num=True),
                           cell(v.get("source") or DASH, tone="muted"), cell(v.get("ref") or DASH, tone="muted")])
        blocks.append({
            "kind": "calc",
            "title": _label(name.split("#")[0]) + (f" ({ref})" if ref else ""),
            "value": _value_text(r.get("value"), r.get("unit") or ""),
            "status": str(r.get("status") or ""),
            "formula": str(r.get("formula") or ""),
            "inputs": table("", [("Input", False), ("Value", True), ("Source", False), ("Reference", False)], inputs, empty=""),
            "steps": [str(s) for s in _l(r.get("steps"))],
            "citations": [str(c) for c in _l(r.get("citations"))],
            "warnings": [str(w) for w in _l(r.get("warnings"))],
        })
    if not blocks:
        blocks.append(note(NOT_DERIVED))
    return blocks


def sheet_equipment(d: Design) -> list:
    rows = []
    circuits = {c.get("key"): c for c in _l(d.electrical.get("circuits")) if isinstance(c, dict)}
    pvals = _d(d.values.get("pumps"))
    for i, p in enumerate(_l(d.net.get("pumps"))):
        p = _d(p)
        v = _d(pvals.get(p.get("id")))
        c = _d(circuits.get(f"pump:{p.get('id')}"))
        duty = f"{fnum(v.get('flow_gpm'), 1, 'gpm')} at {fnum(v.get('head_ft'), 1, 'ft')}" if _num(v.get("flow_gpm")) else DASH
        if str(p.get("control") or "none").lower() not in ("", "none"):
            duty += f", {str(p['control']).upper()}"
        elec = DASH
        if c:
            elec = (f"{fqty(c.get('hp'))} HP, " if _num(c.get("hp")) else "") + \
                f"{fqty(c.get('voltage'))} V {fqty(c.get('phase'))}-phase, {fnum(c.get('load_a'), 1, 'A')}, {fqty(c.get('breaker_a'))} A breaker"
        info = d.item(p.get("item"))
        rows.append([cell(f"P{i + 1}", tone="strong"), cell(_pump_name(p, i, d.rollups, d.items)), cell(p.get("item") or "Pick a model"),
                     cell(info.get("model_no") or DASH), cell("1", num=True), cell(duty), cell(elec),
                     cell("Cut sheet" if info.get("spec_url") else DASH, href=info.get("spec_url"))])
    counts: dict = {}
    fixtures = [x for x in _l(_d(d.model.get("lighting")).get("fixtures")) if isinstance(x, dict)]
    for e in _l(d.model.get("equipment")):
        e = _d(e)
        slot = str(e.get("slot") or "equipment")
        if slot == "pump":
            continue
        prefix = SLOT_TAGS.get(slot, "E")
        counts[prefix] = counts.get(prefix, 0) + 1
        info = d.item(e.get("item"))
        duty = DASH
        if slot == "filter" and _num(d.rollups.get("operating_flow_gpm")):
            duty = f"{fnum(d.rollups['operating_flow_gpm'], 0, 'gpm')} circulation"
        elif slot == "light" and fixtures:
            watts = sum((_num(x.get("watts")) or 0) * (_num(x.get("qty")) or 1) for x in fixtures)
            duty = fnum(watts, 0, "W total") if watts else DASH
        rows.append([cell(f"{prefix}{counts[prefix]}", tone="strong"), cell(info.get("item_name") or SLOT_LABELS.get(slot) or _label(slot)),
                     cell(e.get("item") or "Pick a model"), cell(info.get("model_no") or DASH), cell(fqty(e.get("qty") or 1), num=True),
                     cell(duty), cell(DASH), cell("Cut sheet" if info.get("spec_url") else DASH, href=info.get("spec_url"))])
    fvals = _d(d.values.get("features"))
    for i, f in enumerate(_l(d.model.get("features"))):
        f = _d(f)
        product = _d(f.get("product"))
        info = d.item(product.get("item"))
        v = _d(fvals.get(f.get("id")))
        duty = fnum(v.get("flow_gpm"), 1, "gpm") + (f" at {fnum(v.get('head_ft'), 1, 'ft')}" if _num(v.get("head_ft")) else "")
        rows.append([cell(f"N{i + 1}", tone="strong"), cell(f"{f.get('label') or f.get('id')}: {f.get('type') or _label(f.get('family'))}"),
                     cell(product.get("item") or "Generic, pick a model"), cell(info.get("model_no") or DASH),
                     cell(fqty(f.get("count") or 1), num=True), cell(duty if _num(v.get("flow_gpm")) else DASH), cell(DASH),
                     cell("Cut sheet" if info.get("spec_url") else DASH, href=info.get("spec_url"))])
    return [table("Equipment schedule", [("Tag", False), ("Equipment", False), ("Item", False), ("Model", False), ("Qty", True),
                                          ("Duty", False), ("Electrical", False), ("Cut sheet", False)], rows,
                  empty="No pumps, equipment or features yet.",
                  note="Duty is the solved operating point. A row reading Pick a model is sized from a generic estimate.")]


def sheet_piping(d: Design) -> list:
    segs = _d(d.network.get("segments"))
    nodes = _d(d.network.get("nodes"))
    svals = _d(d.values.get("segments"))
    rows = []
    for s in _l(d.net.get("segments")):
        s = _d(s)
        r = _d(segs.get(s.get("id")))
        v = _d(svals.get(s.get("id")))
        end = _d(nodes.get(s.get("to")) or nodes.get(f"{s.get('to')}:in"))
        status = r.get("velocity_status") or ("Not solved" if not r else DASH)
        rows.append([cell(s.get("label") or s.get("id"), tone="strong"), cell(_label(s.get("line"))),
                     cell(f"{s.get('from')} to {s.get('to')}"), cell(v.get("size") or s.get("nominal_size") or "Unsized"),
                     cell(s.get("material")), cell(fnum(s.get("length_ft"), 1), num=True),
                     cell(fnum(r.get("flow_gpm"), 1), num=True), cell(fnum(r.get("velocity_fps"), 2), num=True),
                     cell(fnum(r.get("loss_ft"), 2), num=True), cell(fnum(end.get("pressure_psi"), 1), num=True),
                     cell(status, tone="pass" if str(status).lower() == "okay" else ("fail" if r else "muted"))])
    return [table("Piping schedule", [("Segment", False), ("Line", False), ("Run", False), ("Size", False), ("Material", False),
                                       ("Length (ft)", True), ("Flow (gpm)", True), ("Velocity (ft/s)", True), ("Head loss (ft)", True),
                                       ("End pressure (psi)", True), ("Status", False)], rows,
                  empty="No pipe runs yet.",
                  note="Darcy-Weisbach network solve at the operating point; status is the velocity against DOC-0049's limits.")]


def sheet_fittings(d: Design) -> list:
    value = _d(_value(d.stored, "bom_rows"))
    fits = [f for f in _l(value.get("fittings")) if isinstance(f, dict)]
    fits.sort(key=lambda f: (str(f.get("label")), str(f.get("material")), str(f.get("nominal_size"))))
    total = sum(_num(f.get("qty")) or 0 for f in fits)
    blocks = [table("Fitting schedule", [("Fitting", False), ("Size", False), ("Material", False), ("Qty", True)],
                    [[cell(f.get("label") or _label(f.get("key"))), cell(f.get("nominal_size")), cell(f.get("material")),
                      cell(fqty(f.get("qty")), num=True)] for f in fits],
                    foot=[[cell("Total fittings", tone="strong"), *blank(2), cell(fqty(total), num=True, tone="strong")]] if fits else [],
                    empty="No fittings yet: size the pipe runs first.",
                    note="Totals per fitting type, size and material over every sized run, branches counted (engine.bom.fitting_counts).")]
    cuts = [c for c in _l(value.get("cut_list")) if isinstance(c, dict)]
    sticks: dict = {}
    for c in cuts:
        k = f"{c.get('nominal_size')} {c.get('material')}"
        sticks[k] = sticks.get(k, 0) + (_num(c.get("sticks")) or 0)
    blocks.append(table("Cut list (20 ft sticks)", [("Segment", False), ("Branch", True), ("Material", False), ("Size", False),
                                                    ("Run (ft)", True), ("Sticks", True), ("Couplings", True), ("Pieces (ft)", False)],
                        [[cell(c.get("segment"), tone="strong"), cell(fqty(c.get("branch")), num=True), cell(c.get("material")),
                          cell(c.get("nominal_size")), cell(fnum(c.get("length_ft"), 1), num=True), cell(fqty(c.get("sticks")), num=True),
                          cell(fqty(c.get("couplings")), num=True), cell(", ".join(fnum(x, 1) for x in _l(c.get("pieces_ft"))))]
                         for c in cuts],
                        foot=[[cell(f"Sticks of {k}", tone="strong"), *blank(4),
                               cell(fqty(n), num=True, tone="strong"), *blank(2)] for k, n in sticks.items()],
                        empty="No sized pipe runs to cut yet.",
                        note="By length only: fitting take-outs are left to the field."))
    return blocks


STRUCT_QUANTITIES = (
    ("concrete_order_yd3", "Concrete to order", "yd³", 1), ("trucks", "Truck loads", "", 0),
    ("concrete_net_yd3", "Concrete, net", "yd³", 2), ("rebar_purchase_lb", "Rebar to buy", "lb", 0),
    ("rebar_ft", "Rebar, total length", "ft", 0), ("gravel_tons", "Gravel base", "tons", 2),
    ("excavation_yd3", "Excavation (bank)", "yd³", 1), ("backfill_yd3", "Backfill", "yd³", 1),
    ("haul_yd3", "Haul-off (loose)", "yd³", 1), ("formwork_sqft", "Formwork", "sq ft", 0),
    ("waterstop_ft", "Waterstop", "ft", 1), ("sealant_ft", "Joint sealant", "ft", 1),
    ("coating_sqft", "Waterproofing", "sq ft", 0), ("curing_sqft", "Curing compound", "sq ft", 0),
    ("chairs", "Rebar chairs", "", 0), ("anchors", "Anchor bolts", "", 0), ("penetrations", "Wall penetrations", "", 0),
    ("steel_lb", "Structural steel", "lb", 0), ("grating_sqft", "Grating", "sq ft", 0), ("pads", "Equipment pads", "", 0),
    ("mooring_blocks", "Mooring blocks", "", 0),
)


def sheet_structural(d: Design) -> list:
    totals = _d(d.structural.get("totals"))
    rows = [[cell(label), cell(fnum(totals.get(k), digits), num=True), cell(unit or "ea", tone="muted")]
            for k, label, unit, digits in STRUCT_QUANTITIES if _num(totals.get(k))]
    bars = [b for b in _l(d.structural.get("rebar_schedule")) if isinstance(b, dict)]
    weight = sum(_num(b.get("weight_lb")) or 0 for b in bars)
    return [
        table("Structural quantities", [("Quantity", False), ("Amount", True), ("Unit", False)], rows,
              empty="No basins to take off yet."),
        table("Rebar schedule", [("Mark", False), ("Element", False), ("Description", False), ("Bar", False), ("Spacing (in)", True),
                                 ("Qty", True), ("Length (ft)", True), ("Total (ft)", True), ("Weight (lb)", True)],
              [[cell(b.get("mark"), tone="strong"), cell(_label(b.get("element"))), cell(b.get("description")), cell(b.get("bar")),
                cell(fnum(b.get("spacing_in"), 0), num=True), cell(fqty(b.get("qty")), num=True), cell(fnum(b.get("length_ft"), 1), num=True),
                cell(fnum(b.get("total_ft"), 1), num=True), cell(fnum(b.get("weight_lb"), 1), num=True)] for b in bars],
              foot=[[cell("Total weight", tone="strong"), *blank(7), cell(fnum(weight, 1), num=True, tone="strong")]] if bars else [],
              empty="No rebar schedule yet.",
              note=str(d.structural.get("disclaimer") or "Engineering aid for quantities, not a stamped design.")),
    ]


def sheet_electrical(d: Design) -> list:
    e = d.electrical
    if not e:
        return [note("No electrical report yet: the design has no loads, or it has not been worked out.")]
    inp, feeder, service = _d(e.get("inputs")), _d(e.get("feeder")), _d(e.get("service"))
    blocks = [facts("Service and feeder", [
        ("Code", e.get("edition")),
        ("Service", f"{fqty(inp.get('service_v'))} V, {fqty(inp.get('phase'))}-phase" if _num(inp.get("service_v")) else None),
        ("Calculated load", fnum(service.get("required_a"), 1, "A")),
        ("Main breaker", fnum(service.get("main_breaker_a"), 0, "A")),
        ("Feeder", (f"#{feeder.get('conductor')} {inp.get('material') or 'Cu'}, #{feeder.get('egc')} EGC, {feeder.get('conduit')} "
                    f"{feeder.get('conduit_type') or ''}, {fqty(feeder.get('ocpd_a'))} A OCPD").replace("  ", " ") if feeder.get("conductor") else None),
        ("Feeder voltage drop", fnum(feeder.get("drop_pct"), 2, "%") if _num(feeder.get("length_ft")) else None),
    ], note=str(e.get("verify") or ""))]
    rows = []
    for r in _l(e.get("panel_rows")):
        r = _d(r)
        rows.append([cell(r.get("tag"), tone="strong"), cell(r.get("description")), cell(fnum(r.get("load_a"), 1), num=True),
                     cell(f"{fqty(r.get('voltage'))} V {fqty(r.get('phase'))}-ph"), cell(fqty(r.get("poles")), num=True),
                     cell(fqty(r.get("breaker_a")), num=True), cell(f"#{r.get('conductor')}" if r.get("conductor") else DASH),
                     cell(f"#{r.get('egc')}" if r.get("egc") else DASH), cell(r.get("conduit")), cell(fnum(r.get("run_ft"), 0), num=True),
                     cell(fnum(r.get("drop_pct"), 2), num=True), cell("GFCI" if r.get("gfci") else DASH, tone="strong" if r.get("gfci") else "muted")])
    blocks.append(table("Panel schedule", [("Tag", False), ("Load", False), ("Amps", True), ("Volts", False), ("Poles", True),
                                           ("Breaker (A)", True), ("Conductor", False), ("EGC", False), ("Conduit", False),
                                           ("Run (ft)", True), ("Drop (%)", True), ("GFCI", False)], rows,
                        empty="No branch circuits yet."))
    circ = [c for c in _l(e.get("circuits")) if isinstance(c, dict)]
    blocks.append(table("Circuit list", [("Tag", False), ("Circuit", False), ("Control", False), ("Breaker basis", False),
                                         ("Conductor minimum (A)", True), ("Ground-fault protection", False)],
                        [[cell(c.get("tag"), tone="strong"), cell(c.get("description")), cell(c.get("control_method")),
                          cell(c.get("breaker_basis")), cell(fnum(c.get("conductor_min_a"), 1), num=True),
                          cell(f"{c.get('gfci_device')} ({c.get('gfci_basis')})" if c.get("gfci_device") else DASH)] for c in circ],
                        empty="No circuits yet."))
    energy = _d(e.get("energy"))
    erows = [r for r in _l(energy.get("rows")) if isinstance(r, dict)]
    blocks.append(table("Energy per year", [("Load", False), ("kW", True), ("Hours a day", True), ("kWh a year", True), ("Cost a year", True)],
                        [[cell(r.get("label")), cell(fnum(r.get("kw"), 2), num=True), cell(fnum(r.get("hours_day"), 1), num=True),
                          cell(fnum(r.get("kwh_yr"), 0), num=True), cell(money(r.get("cost_yr")), num=True)] for r in erows],
                        foot=[[cell("Total", tone="strong"), *blank(2), cell(fnum(energy.get("kwh_per_year"), 0), num=True, tone="strong"),
                               cell(money(energy.get("cost_per_year")), num=True, tone="strong")]] if erows else [],
                        empty="No running loads yet."))
    return blocks


def sheet_controls(d: Design) -> list:
    c = d.controls
    if not c:
        return [note("No controls design yet: the design has no pumps, or it has not been worked out.")]
    parent = _d(_d(c.get("control_panel")).get("parent"))
    panel, ctl, relays, dc = _d(c.get("panel")), _d(c.get("controller")), _d(c.get("relays")), _d(c.get("dc_supply"))
    blocks = [facts("Control panel", [
        ("Controller", " ".join(x for x in (str(parent.get("product_family") or ctl.get("family") or ""), str(ctl.get("part_no") or "")) if x)),
        ("Enclosure", " ".join(x for x in (str(panel.get("nema_type") or parent.get("nema_rating") or ""), str(panel.get("enclosure") or "")) if x)),
        ("Main disconnect", fnum(panel.get("main_disconnect_a"), 0, "A")),
        ("Control transformer", fnum(panel.get("control_transformer_va") or parent.get("control_transformer_va"), 0, "VA")),
        ("24 V DC supply", f"{fqty(dc.get('watts'))} W ({fnum(dc.get('load_w'), 1)} W load)" if _num(dc.get("watts")) else None),
        ("Relays", f"{fqty(relays.get('ssr'))} solid-state, {fqty(relays.get('interposing'))} interposing, {fqty(relays.get('spare'))} spare"
         if _num(relays.get("total")) else None),
        ("Short-circuit rating", fnum(panel.get("sccr_ka"), 0, "kA") if _num(panel.get("sccr_ka")) else None,
         "open" if panel.get("sccr_status") == "warn" else ""),
        ("Surge protection", panel.get("spd")),
    ])]
    starters = []
    for p in _l(c.get("pumps")):
        p = _d(p)
        parts = "; ".join(f"{x.get('tag')} {x.get('device')} {x.get('rating') or ''}".strip() for x in _l(p.get("components")) if isinstance(x, dict))
        starters.append([cell(p.get("tag"), tone="strong"), cell(p.get("label")), cell(p.get("topology_label")), cell(parts or DASH)])
    blocks.append(table("Motor control", [("Tag", False), ("Pump", False), ("Starter", False), ("Components", False)], starters,
                        empty="No pumps to control."))
    lines = [x for x in _l(c.get("bom")) if isinstance(x, dict)]
    blocks.append(table("Controls bill of materials", [("Part no.", False), ("Description", False), ("Qty", True), ("Unit", False)],
                        [[cell(x.get("part_no") or DASH, tone="muted" if not x.get("part_no") else "strong"), cell(x.get("description")),
                          cell(fqty(x.get("qty")), num=True), cell(x.get("uom"))] for x in lines],
                        empty="No controls lines yet."))
    if c.get("theory_of_operation"):
        blocks.append(text_block("Theory of operation (draft)", str(c["theory_of_operation"])))
    return blocks


def sheet_drawings(d: Design) -> list:
    return [
        drawing("Pump duty point", d.charts.get("duty_svg"), "duty",
                empty="No duty-point chart: the pump curve or the network solve is missing."),
        drawing("Circulation schematic", d.charts.get("schematic_svg"), "sch", empty="No circulation schematic: the design has no features yet."),
        drawing("Electrical one-line", d.charts.get("oneline_svg"), "one", empty="No one-line: the design has no electrical loads yet."),
        drawing("Power schematic", d.controls.get("power_svg"), "pwr", empty="No power schematic: the design has no pumps to start."),
        drawing("Control ladder", d.controls.get("ladder_svg"), "lad", empty="No control ladder: the design has no pumps to control.",
                note="Generated from the design data; the plan-specific site and piping drawings are attached separately."),
    ]


def sheet_om(d: Design) -> list:
    up = _d(d.paper.get("upkeep"))
    om, plan, rates = _d(up.get("om")), _d(up.get("water_plan")), _d(up.get("rates"))
    if not om:
        return [note("No operations and maintenance figures yet: the design needs a basin and a worked-out water plan.")]
    rows = [
        [cell("Power"), cell(fnum(om.get("kwh"), 0, "kWh") if _num(om.get("kwh")) else DASH, num=True), cell(money(om.get("power")), num=True)],
        [cell("Water (make-up and refills)"), cell(fnum(om.get("water_gal"), 0, "gal"), num=True), cell(money(om.get("water")), num=True)],
        [cell("Chemicals"), cell(fnum(om.get("chlorine_gal"), 1, "gal chlorine") if _num(om.get("chlorine_gal")) else DASH, num=True),
         cell(money(om.get("chemicals")), num=True)],
        [cell("Parts"), cell(DASH, num=True), cell(money(om.get("parts")), num=True)],
    ]
    blocks = [table("Cost per year", [("Line", False), ("Quantity", True), ("Cost", True)], rows,
                    foot=[[cell("Total", tone="strong"), *blank(1), cell(money(om.get("total")), num=True, tone="strong")]],
                    empty="", note=f"At {fnum(rates.get('kwh_rate'), 4)} $/kWh, "
                                   f"{fnum(rates.get('water_rate_per_ccf'), 2)} $/ccf of water and {fnum(rates.get('chlorine_per_gal'), 2)} $/gal of "
                                   "chlorine, the Water Engineering Settings rates.")]
    wl = [w for w in _l(up.get("water_lines")) if isinstance(w, dict)]
    blocks.append(table("Where the water goes", [("Use", False), ("Gallons a year", True), ("Cost a year", True), ("Note", False)],
                        [[cell(w.get("label")), cell(fnum(w.get("gal"), 0), num=True), cell(money(w.get("cost")), num=True),
                          cell(w.get("note") or DASH, tone="muted")] for w in wl],
                        foot=[[cell("Total", tone="strong"), cell(fnum(om.get("water_gal"), 0), num=True, tone="strong"),
                               cell(money(om.get("water")), num=True, tone="strong"), *blank(1)]] if wl else [],
                        empty="No water figures yet."))
    warm = plan.get("warm")
    blocks.append(facts("Water plan", [
        ("Treatment", _sanitizer(plan.get("treatment"))),
        ("Residual to hold", fnum(plan.get("residual_ppm"), 1, "ppm") if _num(plan.get("residual_ppm")) else None),
        ("Make-up water", fnum(plan.get("makeup_gpd"), 0, "gal a day")),
        ("Drain, clean and refill", f"Every {fqty(plan.get('refresh_days'))} days ({plan.get('refresh_by') or 'hygiene'})"
         if _num(plan.get("refresh_days")) else None),
        ("Cycles of concentration", f"Minerals reach the limit in {fqty(plan.get('mineral_days'))} days"
         if _num(plan.get("mineral_days")) else "Spray loss keeps the minerals under the limit"),
        ("Legionella band (77 to 113 °F)", "Inside the band: hold the residual and clean more often" if warm else "Outside the band",
         "fail" if warm else "pass"),
        ("Chlorinator feed", fnum(plan.get("chlorinator_feed_gph"), 4, "gal/hr") if _num(plan.get("chlorinator_feed_gph")) else None),
        ("Chlorine doses", f"{fnum(plan.get('fill_dose_floz'), 1)} fl oz at fill, then {fnum(plan.get('daily_dose_floz'), 1)} fl oz a day"
         if _num(plan.get("daily_dose_floz")) else None),
    ]))
    ms = [m for m in _l(up.get("maintenance")) if isinstance(m, dict)]
    blocks.append(table("Maintenance schedule", [("Task", False), ("How often", False), ("Note", False)],
                        [[cell(m.get("task"), tone="strong"), cell(m.get("every")), cell(m.get("note") or DASH, tone="muted")] for m in ms],
                        empty="No maintenance schedule yet."))
    return blocks


ROOMS = {"vault": "Pump vault", "room": "Pump room", "submersible": "Submersible pump in the basin"}


def sheet_winterization(d: Design) -> list:
    w = _d(d.paper.get("winterization"))
    startup = [str(s) for s in _l(d.outputs.get("startup"))]
    if not w:
        return [note(NOT_DERIVED)]
    blocks = [facts("Site", [
        ("Winterization needed", "Yes" if w.get("needed") else "No: indoor, or no frost on the site", "open" if w.get("needed") else ""),
        ("Frost depth", fnum(w.get("frost_depth_in"), 0, "in")),
        ("Equipment", ROOMS.get(str(w.get("equipment_room") or ""), _label(w.get("equipment_room")) or None)),
        ("Drain points", ", ".join(str(x) for x in _l(w.get("drain_points"))) or "Not recorded yet: mark each line drained, sloped or buried"),
        ("Lines below the frost depth", ", ".join(str(x) for x in _l(w.get("below_frost"))) or None),
    ])]
    if w.get("needed"):
        blocks.append(bullets("Winterize (before the first freeze)", [i.get("title") + (f": {i['detail']}" if i.get("detail") else "")
                                                                       for i in _l(w.get("winterize")) if isinstance(i, dict) and i.get("title")],
                              boxes=True, empty="No steps."))
        blocks.append(bullets("Spring start-up", [i.get("title") for i in _l(w.get("spring_startup")) if isinstance(i, dict) and i.get("title")],
                              boxes=True, empty="No steps."))
    blocks.append(bullets("First start-up checks", startup, boxes=True, empty="No start-up checks yet."))
    if w.get("checklist_text"):
        blocks.append(text_block("Checklist text for a Maintenance Visit", str(w["checklist_text"]),
                                 note="Copy into the visit's checklist or the maintenance schedule's notes."))
    return blocks


IWF_STATUS = {"pass": ("Pass", "pass"), "blocker": ("Fail", "fail"), "warning": ("Fail", "fail"), "info": ("Open", "open")}
NEC_STATUS = {"pass": ("Pass", "pass"), "fail": ("Fail", "fail"), "missing": ("Missing", "fail"), "verify": ("Verify", "open"),
              "n/a": ("N/A", "muted")}


def _status_cell(table_: dict, status: Any) -> dict:
    label, tone = table_.get(str(status or "").lower(), (_label(status) or "Open", "open"))
    return cell(label, tone=tone)


def sheet_calcs(d: Design) -> list:
    r, site = d.rollups, d.site
    legacy = _d(d.model.get("legacy"))
    blocks = [facts("Venue", [
        ("Venue", site.get("venue_type")),
        ("Regulated", "Yes: health-department plan check" if d.regulated else "No", "open" if d.regulated else ""),
        ("Governing code", site.get("governing_code") or d.doc.get("governing_code")),
        ("Jurisdiction", site.get("jurisdiction")),
        ("Vessel", " ".join(str(x) for x in (legacy.get("vessel_model"), legacy.get("vessel_construction")) if x) or None),
        ("Vessel listing", legacy.get("vessel_listing")),
    ])]
    bvals = _d(d.values.get("basins"))
    brows = []
    for b in _l(d.model.get("basins")):
        b = _d(b)
        v = _d(bvals.get(b.get("id")))
        dims = (f"{fnum(b.get('diameter_in'), 0)} in dia" if _num(b.get("diameter_in")) else
                f"{fnum(b.get('length_in'), 0)} x {fnum(b.get('width_in'), 0)} in") + (f" x {fnum(b.get('depth_in'), 0)} in deep" if _num(b.get("depth_in")) else "")
        brows.append([cell(b.get("label") or b.get("id"), tone="strong"), cell(_label(b.get("shape"))), cell(dims),
                      cell(fnum(v.get("volume_gal"), 0), num=True), cell(fnum(v.get("freeboard_in"), 1), num=True)])
    blocks.append(table("Vessel and volume", [("Basin", False), ("Shape", False), ("Dimensions", False), ("Volume (gal)", True),
                                              ("Freeboard (in)", True)], brows, empty="No basin defined."))
    vol, op = _num(r.get("total_basin_gallons")), _num(r.get("operating_flow_gpm")) or _num(r.get("design_flow_gpm"))
    minf = _result(d.stored, "minimum_flow_rate")
    short = any(i.get("code") == "REG_MIN_FLOW" for i in d.issues)
    filt = _result(d.stored, "filtration_area")
    plan = _d(_d(d.paper.get("upkeep")).get("water_plan"))
    basis = [
        ("Total volume", fnum(vol, 0, "gal")),
        ("Design flow", fnum(r.get("design_flow_gpm"), 1, "gpm")),
        ("Operating flow", fnum(r.get("operating_flow_gpm"), 1, "gpm")),
        ("Code minimum circulation", f"{fnum(minf.get('value'), 2, 'gpm')}: {(_l(minf.get('steps')) or [''])[0]}".rstrip(": ") if minf else None,
         "fail" if short else "pass"),
        ("Turnover at the operating flow", f"{fnum(vol, 0)} gal / {fnum(op, 1)} gpm = {fnum(vol / op, 1, 'min')}" if vol and op else None),
        ("Total dynamic head", fnum(r.get("tdh_ft"), 1, "ft")),
        ("Filter area required", f"{fnum(filt.get('value'), 1, 'sq ft')} ({(_l(filt.get('citations')) or [''])[0]})" if filt else None),
        ("Chlorinator feed", fnum(plan.get("chlorinator_feed_gph"), 4, "gal/hr") if _num(plan.get("chlorinator_feed_gph")) else None),
        ("Bather load", legacy.get("bather_load")),
    ]
    blocks.append(facts("Design basis", basis))
    loop = []
    for i, p in enumerate(_l(d.net.get("pumps"))):
        p = _d(p)
        v = _d(_d(d.values.get("pumps")).get(p.get("id")))
        loop.append([cell("Pump", tone="strong"), cell(f"{_pump_name(p, i, d.rollups, d.items)}{' (' + p['item'] + ')' if p.get('item') else ''}"),
                     cell(f"{fnum(v.get('flow_gpm'), 1, 'gpm')} at {fnum(v.get('head_ft'), 1, 'ft')}" if _num(v.get("flow_gpm")) else DASH)])
    for e in _l(d.model.get("equipment")):
        e = _d(e)
        if e.get("slot") in (None, "pump", "light", "transformer"):
            continue
        loop.append([cell(SLOT_LABELS.get(e["slot"]) or _label(e["slot"]), tone="strong"), cell(e.get("item") or "Pick a model"),
                     cell(f"x {fqty(e.get('qty') or 1)}")])
    sizes: dict = {}
    for s in _l(d.net.get("segments")):
        s = _d(s)
        size = _d(_d(d.values.get("segments")).get(s.get("id"))).get("size") or s.get("nominal_size")
        if size:
            sizes.setdefault(_label(s.get("line")), []).append(str(size))
    for line, ss in sizes.items():
        loop.append([cell("Piping", tone="strong"), cell(f"{line}: {', '.join(dict.fromkeys(ss))}"), cell(DASH)])
    treat = _d(d.model.get("treatment"))
    loop.append([cell("Treatment", tone="strong"), cell(_sanitizer(treat.get("sanitizer")) + (", UV" if treat.get("uv") else "")), cell(DASH)])
    blocks.append(table("Circulation system", [("Role", False), ("Equipment", False), ("Rating", False)], loop, empty="No circulation equipment yet."))

    pc = _d(d.paper.get("plan_check"))
    iwf = [x for x in _l(pc.get("iwf")) if isinstance(x, dict)]
    if iwf or "interactive" in str(site.get("venue_type") or "").lower():
        rows = []
        for x in iwf:
            label, tone = IWF_STATUS.get(str(x.get("severity")), ("Open", "open"))
            result = x.get("detail") or (f"{fqty(x.get('value'))} {x.get('unit') or ''} (limit {fqty(x.get('limit'))})".strip()
                                         if _num(x.get("value")) is not None else DASH)
            rows.append([cell(x.get("title"), tone="strong"), cell(result), cell(label, tone=tone),
                         cell("Utah R392-302-41" + ("; MAHC" if "MAHC" in str(x.get("citation") or "") else ""), tone="muted")])
        blocks.append(table("Utah interactive water feature checklist (R392-302-41)", [("Requirement", False), ("Design", False),
                                                                                        ("Status", False), ("Citation", False)], rows,
                            empty="List the play features on the interactive feature to run the Utah checklist.",
                            note="Open means the design does not answer it yet; the inspector decides each line."))
    nec = [x for x in _l(pc.get("nec680")) if isinstance(x, dict)]
    blocks.append(table("NEC Article 680 checklist" + (f" ({pc['nec_edition']})" if pc.get("nec_edition") else ""),
                        [("Article", False), ("Requirement", False), ("Design", False), ("Status", False)],
                        [[cell(x.get("article"), tone="strong"), cell(x.get("title")), cell(x.get("detail") or DASH),
                          _status_cell(NEC_STATUS, x.get("status"))] for x in nec],
                        empty="No electrical checklist yet: the design has no electrical loads.",
                        note=str(pc.get("nec_verify") or "")))
    if d.regulated:
        blocks.append(note("PRELIMINARY: an engineering aid for plan check; the engineer of record reviews and seals it before issue."))
    return blocks


def sheet_basis(d: Design) -> list:
    site, r = d.site, d.rollups
    verdict = _d(d.stored.get("verdict"))
    return [facts("Site", [
        ("Address", site.get("address")),
        ("Venue", site.get("venue_type")),
        ("Jurisdiction", site.get("jurisdiction")),
        ("Governing code", site.get("governing_code")),
        ("Elevation", fnum(site.get("elevation_ft"), 0, "ft")),
        ("Design air temperature", fnum(site.get("air_temp_f"), 0, "°F")),
        ("Design water temperature", fnum(site.get("water_temp_f"), 0, "°F")),
        ("Frost depth", fnum(site.get("frost_depth_in"), 0, "in")),
        ("Water hardness", fnum(site.get("hardness_ppm"), 0, "ppm")),
    ]), facts("Design", [
        ("Features", ", ".join(f"{_d(f).get('label') or _d(f).get('id')} ({_d(f).get('type') or _label(_d(f).get('family'))})"
                               for f in _l(d.model.get("features"))) or None),
        ("Design flow", fnum(r.get("design_flow_gpm"), 1, "gpm")),
        ("Operating flow", fnum(r.get("operating_flow_gpm"), 1, "gpm")),
        ("Total dynamic head", fnum(r.get("tdh_ft"), 1, "ft")),
        ("Pump", r.get("selected_pump_label") or r.get("selected_pump")),
        ("Water in the basins", fnum(r.get("total_basin_gallons"), 0, "gal")),
        ("Verdict", verdict.get("headline"), {"good": "pass", "bad": "fail"}.get(verdict.get("level"), "open")),
    ], note="" if d.derived else NOT_DERIVED)]


# ----------------------------------------------------------------------------- budget

BUDGET_GROUPS = ("Hydraulic", "Treatment", "Lighting", "Electrical", "Controls", "Structural", "Labor", "Other")
_TREATMENT_KEYS = ("equipment|filter", "component|filter", "equipment|uv", "equipment|chlorinator", "fitting|filter", "fitting|strainer",
                   "equipment|heater", "equipment|chemical")
_LIGHTING_KEYS = ("equipment|light", "equipment|transformer", "xfmr:lighting", "lvwire:", "lvblock:", "lvfuse:", "jbox")
_SECTION_GROUP = {"Piping": "Hydraulic", "Valves": "Hydraulic", "Nozzles": "Hydraulic", "Equipment": "Hydraulic",
                  "Consumables": "Hydraulic", "Electrical": "Electrical", "Controls": "Controls", "Structural": "Structural",
                  "Labor": "Labor", "Lighting": "Lighting", "Treatment": "Treatment"}


def budget_group(row: dict) -> str:
    """The budget group of one BOM row, from its section and the engine's line key."""
    key = str(row.get("line_key") or "")
    section = str(row.get("section") or "")
    if section == "Labor":
        return "Labor"
    if key.startswith(_TREATMENT_KEYS):
        return "Treatment"
    if key.startswith(_LIGHTING_KEYS):
        return "Lighting"
    return _SECTION_GROUP.get(section, "Other")


def budget_summary(bom: list, budget_rules: dict | None) -> dict:
    """BOM rows grouped for the Budget print, with the contingency, the total and the range.

    ``amount`` is the row's own (rate x qty, as the record stores it); the subtotal is therefore the
    ``budget_total`` rollup. Contingency and range are house rules (``budget_contingency_pct``,
    ``budget_range_pct``), printed under the subtotal and kept out of the BOM a Quotation takes."""
    rules = _d(budget_rules)
    groups = {g: {"label": g, "lines": [], "subtotal": 0.0, "unpriced": 0} for g in BUDGET_GROUPS}
    for row in bom or []:
        if not isinstance(row, dict):
            continue
        g = groups[budget_group(row)]
        rate = _num(row.get("rate")) or 0.0
        amount = _num(row.get("amount"))
        if amount is None:
            amount = rate * (_num(row.get("qty")) or 0.0)
        g["lines"].append(dict(row, amount=round(amount, 2), priced=rate > 0))
        g["subtotal"] = round(g["subtotal"] + amount, 2)
        if rate <= 0:
            g["unpriced"] += 1
    subtotal = round(sum(g["subtotal"] for g in groups.values()), 2)
    cpct = _num(rules.get("contingency_pct")) or 0.0
    rpct = _num(rules.get("range_pct")) or 0.0
    contingency = round(subtotal * cpct / 100.0, 2)
    total = round(subtotal + contingency, 2)
    kept = [g for g in groups.values() if g["lines"]]
    return {
        "groups": kept,
        "subtotal": subtotal,
        "contingency_pct": cpct,
        "contingency": contingency,
        "total": total,
        "range_pct": rpct,
        "low": round(total * (1 - rpct / 100.0), 2),
        "high": round(total * (1 + rpct / 100.0), 2),
        "unpriced": sum(g["unpriced"] for g in kept),
        "unpriced_groups": [g["label"] for g in kept if g["unpriced"]],
        "labor_rate": _num(rules.get("labor_rate_per_hr")) or 0.0,
        "lines": sum(len(g["lines"]) for g in kept),
    }


def sheet_budget(d: Design) -> list:
    s = budget_summary(d.bom, _d(d.paper.get("budget_rules")))
    rows: list = []
    for g in s["groups"]:
        rows.append({"group": f"{g['label']} ({money(g['subtotal'], cents=False)})"})
        for ln in g["lines"]:
            desc = str(ln.get("description") or ln.get("item") or "")
            if ln.get("item") and ln.get("item") != desc:
                desc = f"{desc} ({ln['item']})"
            if ln.get("source") == "Manual":
                desc += " [added by hand]"
            if ln["priced"]:
                rate_cell = cell(money(ln.get("rate")), num=True)
            elif g["label"] == "Labor":
                rate_cell = cell("Rate not set", num=True, tone="fail")
            else:
                rate_cell = cell("No Item price" if ln.get("item") else "No Item", num=True, tone="fail")
            rows.append([cell(desc), cell(fqty(ln.get("qty")), num=True), cell(ln.get("uom") or "ea", tone="muted"), rate_cell,
                         cell(money(ln.get("amount")), num=True, tone="" if ln["priced"] else "muted")])
    foot = [
        [cell("Subtotal (the BOM)", tone="strong"), *blank(3), cell(money(s["subtotal"]), num=True, tone="strong")],
        [cell(f"Contingency ({fnum(s['contingency_pct'], 0)}%)"), *blank(3), cell(money(s["contingency"]), num=True)],
        [cell("Budget", tone="strong"), *blank(3), cell(money(s["total"]), num=True, tone="strong")],
    ]
    if s["range_pct"]:
        foot.append([cell(f"Range (plus or minus {fnum(s['range_pct'], 0)}%)"), *blank(3),
                     cell(f"{money(s['low'], cents=False)} to {money(s['high'], cents=False)}", num=True)])
    notes = []
    if s["unpriced"]:
        notes.append(f"{s['unpriced']} of {s['lines']} lines have no price and are left out of the total "
                     f"({', '.join(s['unpriced_groups'])}). Pick an Item with a price on the selling price list for each.")
    if any(g["label"] == "Labor" for g in s["groups"]) and not s["labor_rate"]:
        notes.append("The labor rate is not set in Water Engineering Settings, so the labor hours are not priced.")
    if not any(g["label"] == "Labor" for g in s["groups"]):
        notes.append("No labor: set the labor hours per family and per square foot of basin in Water Engineering Settings.")
    notes.append("Prices are Sapphire's own Item prices on the default selling price list; labor is the house rule rate. "
                 "No published cost-per-square-foot figure is used.")
    return [
        facts("Summary", [
            ("Budget", money(s["total"], cents=False), "strong"),
            ("Range", f"{money(s['low'], cents=False)} to {money(s['high'], cents=False)}" if s["range_pct"] else None),
            ("BOM subtotal", money(s["subtotal"], cents=False)),
            ("Lines without a price", f"{s['unpriced']} of {s['lines']}" if s["unpriced"] else None, "fail"),
        ]),
        table("Budget by section", [("Line", False), ("Qty", True), ("Unit", False), ("Rate", True), ("Amount", True)], rows,
              foot=foot if rows else [], empty="No bill of materials yet.", note=" ".join(notes)),
    ]


# ----------------------------------------------------------------------------- the context

SHEETS = {
    "results": ("Results", sheet_results),
    "audit": ("Calculation audit", sheet_audit),
    "basis": ("Design basis", sheet_basis),
    "equipment": ("Equipment schedule", sheet_equipment),
    "piping": ("Piping schedule", sheet_piping),
    "fittings": ("Fittings and cut list", sheet_fittings),
    "electrical": ("Electrical", sheet_electrical),
    "controls": ("Controls", sheet_controls),
    "structural": ("Structural takeoff", sheet_structural),
    "drawings": ("Design drawings", sheet_drawings),
    "calcs": ("Design calculations", sheet_calcs),
    "om": ("Operations and maintenance", sheet_om),
    "winterization": ("Winterization and start-up", sheet_winterization),
    "budget": ("Budget", sheet_budget),
}


def _sheet(d: Design, key: str) -> dict:
    title, fn = SHEETS[key]
    try:
        blocks = fn(d)
    except Exception as exc:  # a bad stored row prints a line, never a stack trace on paper
        blocks = [note(f"This section could not be drawn ({type(exc).__name__}). Save the design again, then print it again.")]
    for b in blocks:  # a block named like its sheet would print the same heading twice
        if b.get("title") == title:
            b["title"] = ""
    return {"key": key, "title": title, "blocks": blocks}


def build(doc: dict | None, model: dict | None, stored: dict | None, bom: list | None, *, items: dict | None = None,
          issues: list | None = None, sheets: tuple | list | None = None) -> dict:
    """The print context of one design: its title, meta lines and the sheets asked for (all by default)."""
    d = Design(doc or {}, model or {}, stored or {}, bom or [], items, issues)
    keys = [k for k in (sheets or SHEETS) if k in SHEETS]
    meta = [d.name]
    if d.doc.get("project"):
        meta.append(f"Project {d.doc['project']}")
    if d.doc.get("customer"):
        meta.append(str(d.doc["customer"]))
    meta.append(f"Status: {d.doc.get('status') or 'Draft'}")
    return {
        "name": d.name,
        "title": d.title,
        "meta": [m for m in meta if m],
        "regulated": d.regulated,
        "derived": d.derived,
        "sheets": {k: _sheet(d, k) for k in keys},
    }
