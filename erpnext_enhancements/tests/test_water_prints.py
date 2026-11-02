"""Bench-free tests for the Fountain Designer's paper outputs (phase G, v1.770.0).

* **Every Water Feature Design print format and the submittal packet render** under real Jinja2,
  configured as frappe configures it (no autoescape, ``DebugUndefined``), with the ``we_*`` globals
  stubbed the way the controller suite stubs frappe, over the three demo designs phase F ships
  (``public/js/fountain_designer/demo``, run through ``derive_model``) and a regulated spa. The
  output carries no Jinja left over, no doubled escape, no table with no rows, no raw float, no
  ``None``, and no drawing id twice on one page.
* The **context** (``water_engineering/prints.py``) reads what the derive stored: the plan-check
  lines, the drawings, the O&M split, the winterization checklist, the budget arithmetic.
* The **derive** stores what the prints read (``paper``), prices labor at the house rule, lists
  the outputs the page links to, and gives the page the winterization module's own steps.
* ``packet.we_print`` reads a record (and survives a broken one).

Plain pytest functions with their own frappe stub (installed per test): its own ``python -m
pytest`` step in ci.yml.

Run: python -m pytest erpnext_enhancements/tests/test_water_prints.py -q
"""

import copy
import html
import importlib
import json
import re
import sys
import types
from pathlib import Path

import jinja2
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.water_engineering import derive, prints
from erpnext_enhancements.water_engineering.engine import kits

APP = REPO_ROOT / "erpnext_enhancements"
DEMO = APP / "public" / "js" / "fountain_designer" / "demo"
# The keys the controller writes into results_json (water_feature_design.RESULT_KEYS).
STORED_KEYS = ("results", "values", "rollups", "charts", "outputs", "verdict", "electrical", "structural", "controls", "paper")


def _curve(shutoff, qmax, hp, klass, item):
    points = []
    for i in range(6):
        q = qmax * i / 5
        eff = 0 if i == 0 else max(70 * (1 - ((q - 0.6 * qmax) / (0.6 * qmax)) ** 2), 5)
        points.append({"flow_gpm": q, "head_ft": shutoff * (1 - 0.85 * (q / qmax) ** 2), "efficiency_pct": eff,
                       "npshr_ft": 4 + 8 * (q / qmax) ** 2})
    return {"item": item, "name": f"Stub {klass} {hp} HP", "curve_name": f"{item}-CURVE", "points": points,
            "shutoff_head_ft": shutoff, "max_flow_gpm": qmax, "motor_eff_pct": 82, "hp": hp, "generic_class": klass,
            "catalog_source": "datasheet"}


# The same stub catalog and price list as tests/test_designer_derive.py.
CATALOG = {"pumps": [
    _curve(s, q, hp, k, f"STUB-{k[:3].upper()}-{hp}")
    for k in ("end-suction", "submersible", "self-priming")
    for s, q, hp in ((20, 25, 0.25), (45, 90, 0.75), (60, 160, 1.5), (80, 260, 3), (100, 420, 5), (120, 700, 10))
]}
PRICES = {"items": {r["item"]: {"rate": 400 + 300 * r["hp"]} for r in CATALOG["pumps"]}, "hints": {}}


def spa_model():
    """A regulated spa on model 2, the Fika path: a commercial spa venue, its code, and the vessel the
    schema-1 record carried (``model.legacy``, where ``engine.model.migrate`` keeps it)."""
    m = kits.apply_kit("lobby-sheet-wall-8ft")
    m["site"].update(venue_type="Commercial Spa", governing_code="Utah R392-302")
    m["basins"][0].update(label="Spa vessel", shape="round", diameter_in=96, depth_in=36)
    m["legacy"] = {"vessel_model": "Fika reflexology spa", "vessel_listing": "NSF/ANSI 50", "bather_load": 4}
    return m


def demo_model(name):
    return json.loads((DEMO / f"derive_{name}.json").read_text(encoding="utf-8"))["model"]


MODELS = {"lobby": lambda: demo_model("lobby"), "plaza": lambda: demo_model("plaza"), "pond": lambda: demo_model("pond"),
          "spa": spa_model}
_CACHE: dict = {}


def designed(name, rules=None):
    """``(reply, stored)``: the derive of a fixture and results_json as the controller stores it."""
    key = (name, json.dumps(rules, sort_keys=True))
    if key not in _CACHE:
        out = derive.derive_model(MODELS[name](), CATALOG, rules, prices=PRICES)
        _CACHE[key] = (out, json.loads(json.dumps({k: out.get(k) for k in STORED_KEYS})))
    return _CACHE[key]


@pytest.fixture()
def spf(monkeypatch):
    """``setup_print_formats`` imported under a frappe stub (it imports frappe at the top)."""
    frappe = types.ModuleType("frappe")
    frappe._ = lambda s, *a, **k: s
    frappe.log_error = lambda *a, **k: None
    frappe.get_traceback = lambda *a, **k: ""
    monkeypatch.setitem(sys.modules, "frappe", frappe)
    monkeypatch.delitem(sys.modules, "erpnext_enhancements.water_engineering.setup_print_formats", raising=False)
    return importlib.import_module("erpnext_enhancements.water_engineering.setup_print_formats")


def environment(context):
    """Jinja as frappe builds it, with the we_* and ps_* globals a Water Feature Design format calls."""
    env = jinja2.Environment(autoescape=False, undefined=jinja2.DebugUndefined)
    env.globals.update(
        we_print=lambda doc, sheets=None: context(sheets),
        we_title_block=lambda name: {
            "preliminary": True, "project": {"name": "Plaza & Lobby", "customer": "A & B Holdings"},
            "engineer": {"name": None, "license": None, "address": None, "phone": None, "stamp": None},
            "contractor": {"name": "Sapphire Fountains", "address": "85 W 300 S", "phone": "(801) 555-0100"},
            "issue_date": "2026-11-01", "drawn_by": "NB", "governing_code": "Utah R392-302", "venue_type": "Commercial Spa",
            "revisions": [{"mark": "A", "date": "2026-11-01", "description": "Issued for plan check"}],
        },
        we_standard_notes=lambda code=None: [{"category": "General", "title": "Code", "body": "Build to R392-302 & the NEC."}],
        we_standard_details=lambda code=None: [{"title": "Main drain", "detail_key": "md", "svg": '<svg viewBox="0 0 10 10"></svg>'}],
        ps_date=lambda value: "11/01/2026" if value else "",
        ps_state=prints.ps.ps_state,
        ps_party=lambda doc: "<b>Acme &amp; Sons</b><br>1 Main St",
    )
    return env


def render(spf, name, print_format, doc_extra=None):
    out, stored = designed(name)
    doc = {"name": f"WFD-{name}", "design_title": f"{name.title()} & Co. design", "status": "Derived", "project": "PRJ-1",
           "customer": "Acme & Sons", "docstatus": 0, "issue_date": None, "modified": "2026-11-01 10:00:00", "designer": "Logan"}
    doc.update(doc_extra or {})

    def context(sheets):
        return prints.build(doc, out["model"], stored, out["bom"], issues=out["issues"], sheets=sheets)

    html_ = spf.water_formats()[print_format]
    return environment(context).from_string(html_).render(doc=types.SimpleNamespace(**doc))


def visible_text(page):
    """The page as a reader sees it: no styles, no drawings, no tags, entities decoded."""
    page = re.sub(r"<style.*?</style>", " ", page, flags=re.S)
    page = re.sub(r"<svg.*?</svg>", " ", page, flags=re.S)
    return html.unescape(re.sub(r"<[^>]+>", " ", page))


def formatted_text(page):
    """The visible text minus the engine's own formulas and working, which the audit prints verbatim."""
    page = re.sub(r'<(span|pre) class="we-working".*?</\1>', " ", page, flags=re.S)
    return visible_text(page)


ALL_FORMATS = prints.PRINT_FORMATS


# ----------------------------------------------------------------------------- every format renders


@pytest.mark.parametrize("name", list(MODELS))
@pytest.mark.parametrize("print_format", ALL_FORMATS)
def test_every_format_renders_clean_on_every_design(spf, name, print_format):
    page = render(spf, name, print_format)
    text = visible_text(page)
    assert "{{" not in page and "{%" not in page, "Jinja left in the output (frappe's DebugUndefined prints it)"
    assert "&amp;amp;" not in page and "&amp;#" not in page, "an escape was escaped again"
    title = "Plaza & Lobby" if print_format == prints.PF_PACKET else "& Co. design"  # the packet's cover is the title block's
    assert title in text, "the title prints its ampersand once"
    assert not re.search(r"<tbody>\s*</tbody>", page), "a table with no rows"
    assert not re.search(r"\b(None|nan|inf|NaN|Infinity|undefined|Undefined)\b", text)
    # Every number on paper is formatted: no float repr, no exponent.
    numbers = formatted_text(page)
    assert not re.search(r"\d\.\d{5,}", numbers), re.findall(r".{30}\d\.\d{5,}.{10}", numbers)[:3]
    assert not re.search(r"\d(e|E)[+-]\d", numbers)
    ids = re.findall(r'\bid="([^"]+)"', page)
    assert len(ids) == len(set(ids)), sorted({i for i in ids if ids.count(i) > 1})
    assert "Sapphire Fountains, LLC" in text  # the print design system's chrome


def test_the_formats_seeded_are_the_listed_ones(spf):
    assert set(spf.water_formats()) == set(prints.FORMATS) | {prints.PF_PACKET}
    names = {pf for _k, _l, pf in prints.OUTPUTS}
    assert names <= set(prints.FORMATS), "every paper the page lists has a format"
    assert all(pf.startswith("Water Feature Design - ") for pf in spf.water_formats())


def test_no_template_calls_a_global_hooks_does_not_register(spf):
    hooks = (APP / "hooks.py").read_text(encoding="utf-8")
    registered = set(re.findall(r'"erpnext_enhancements\.[\w.]+\.(\w+)"', hooks))
    for pf, html_ in spf.water_formats().items():
        called = set(re.findall(r"\b((?:we|ps)_\w+)\(", html_)) - set(re.findall(r"macro (\w+)\(", html_))
        assert called <= registered, (pf, called - registered)
    assert "erpnext_enhancements.water_engineering.packet.we_print" in hooks


def test_an_unworked_record_prints_its_sheets_with_a_reason(spf):
    for print_format in ALL_FORMATS:
        def context(sheets):
            return prints.build({"name": "WFD-NEW"}, {}, {}, [], sheets=sheets)

        page = environment(context).from_string(spf.water_formats()[print_format]).render(doc=types.SimpleNamespace(name="WFD-NEW"))
        assert "{{" not in page and not re.search(r"<tbody>\s*</tbody>", page), print_format
        assert print_format == prints.PF_PACKET or "WFD-NEW" in page


# ----------------------------------------------------------------------------- what each sheet shows


def test_schedules_read_the_solved_network_and_the_aggregated_fittings(spf):
    out, stored = designed("plaza")
    text = visible_text(render(spf, "plaza", prints.PF_SCHEDULES))
    fits = stored["results"]["bom_rows"]["value"]["fittings"]
    assert fits and "Total fittings" in text
    total = sum(f["qty"] for f in fits)
    assert re.search(rf"Total fittings\s*\|?\s*{int(total)}\b", text.replace("  ", " ")) or f" {int(total)} " in text
    seg = stored["results"]["network_operating_point"]["value"]["segments"]["s2"]
    assert prints.fnum(seg["velocity_fps"], 2) in text and prints.fnum(seg["loss_ft"], 2) in text
    assert "Cut list (20 ft sticks)" in text and "Sticks of" in text
    assert "P1" in text and "16.7 A" in text  # the pump's circuit from the electrical report


def test_design_calculations_carry_the_utah_and_nec_checklists(spf):
    text = visible_text(render(spf, "plaza", prints.PF_CALCS))
    assert "Utah interactive water feature checklist (R392-302-41)" in text
    assert "Whole volume treated within 30 minutes" in text and "Open" in text
    assert "NEC Article 680 checklist" in text and "680.51(A)" in text
    assert "PRELIMINARY" in text
    _, stored = designed("plaza")
    iwf = stored["paper"]["plan_check"]["iwf"]
    assert iwf and all(i["code"].startswith("IWF_") for i in iwf) and iwf[0]["feature_label"] == "Plaza jets"


def test_the_spa_path_works_on_a_model_2_design(spf):
    out, stored = designed("spa")
    assert out["model"]["site"]["regulated"] is True
    text = visible_text(render(spf, "spa", prints.PF_CALCS))
    assert "Code minimum circulation" in text and prints.fnum(stored["results"]["minimum_flow_rate"]["value"], 2, "gpm") in text
    assert "Fika reflexology spa" in text and "NSF/ANSI 50" in text and "Bather load" in text
    assert "Turnover at the operating flow" in text
    assert "Regulated" in text and "health-department plan check" in text
    assert "Utah interactive" not in text, "a spa has no interactive checklist"


def test_a_pass_and_a_fail_print_as_such():
    stored = {"paper": {"plan_check": {"iwf": [
        {"title": "Inlets", "severity": "pass", "value": 4, "limit": 4, "unit": "", "citation": "x", "detail": ""},
        {"title": "Tank", "severity": "blocker", "detail": "100 gal; needs 300 gal.", "citation": "x"},
    ], "nec680": [{"article": "680.21", "title": "GFCI", "status": "fail", "detail": "none"},
                  {"article": "680.2", "title": "LV", "status": "n/a"}]}},
        "results": {"x": {}}}
    blocks = prints.build({}, {"site": {"venue_type": "Interactive Water Feature"}}, stored, [], sheets=["calcs"])["sheets"]["calcs"]["blocks"]
    iwf = next(b for b in blocks if "R392-302-41" in b["title"])
    statuses = [r["cells"][2] for r in iwf["rows"]]
    assert [s["text"] for s in statuses] == ["Pass", "Fail"]
    assert prints.GREEN in statuses[0]["style"] and prints.ps.RED in statuses[1]["style"]
    nec = next(b for b in blocks if b["title"].startswith("NEC"))
    assert [r["cells"][3]["text"] for r in nec["rows"]] == ["Fail", "N/A"]


def test_drawings_share_a_page_without_sharing_ids(spf):
    _, stored = designed("lobby")
    assert stored["controls"]["power_svg"] and stored["controls"]["ladder_svg"], "derive stores the controls drawings"
    page = render(spf, "lobby", prints.PF_DRAWINGS)
    assert page.count("<svg") >= 4
    ids = re.findall(r'\bid="([^"]+)"', page)
    assert len(ids) == len(set(ids))
    for ref in re.findall(r"url\(#([^)]+)\)", page):
        assert ref in ids, f"url(#{ref}) points at nothing"


def test_unique_ids_suffixes_every_reference():
    svg = '<svg viewBox="0 0 1 1"><defs><marker id="arrow"/></defs><path marker-end="url(#arrow)"/></svg>'
    a, b = prints.unique_ids(svg, "one"), prints.unique_ids(svg, "two")
    ida, idb = re.findall(r'id="([^"]+)"', a)[0], re.findall(r'id="([^"]+)"', b)[0]
    assert ida != idb and f"url(#{ida})" in a and f"url(#{idb})" in b
    assert prints.unique_ids(None, "x") is None and prints.unique_ids("not svg", "x") is None


def test_om_splits_the_water_and_adds_up(spf):
    _, stored = designed("lobby")
    up = stored["paper"]["upkeep"]
    lines = {w["key"]: w for w in up["water_lines"]}
    assert set(lines) == {"evaporation", "spray", "refill", "backwash"}
    assert lines["backwash"]["gal"] is None, "no figure the engine does not make"
    assert sum(w["cost"] for w in up["water_lines"] if w["cost"] is not None) == pytest.approx(up["om"]["water"], abs=0.05)
    assert sum(w["gal"] for w in up["water_lines"] if w["gal"] is not None) == pytest.approx(up["om"]["water_gal"], rel=1e-6)
    assert up["maintenance"] and any(m["task"] == "Winterize" for m in up["maintenance"])
    text = visible_text(render(spf, "lobby", prints.PF_OM))
    for word in ("Cost per year", "Where the water goes", "Legionella band", "Maintenance schedule", "Drain, clean and refill"):
        assert word in text
    assert prints.money(up["om"]["total"]) in text


def test_the_pond_takes_make_up_water_only(spf):
    """A pond or lake is never drained and refilled (the cycles-of-concentration rule is for recirculating
    features): its yearly water is the evaporation and spray make-up alone, and the prints say so."""
    out, stored = designed("pond")
    up = stored["paper"]["upkeep"]
    plan, om = up["water_plan"], up["om"]
    assert plan["natural"] and plan["refresh_days"] is None and om["refills"] == 0
    assert om["water_gal"] == pytest.approx(plan["makeup_gpd"] * 365, rel=1e-9)
    assert plan["makeup_gpd"] == pytest.approx(stored["results"]["evaporation_rate"]["value"], rel=1e-9), \
        "an aerator pond with no pump network still evaporates (ASHRAE over its surface, all day)"
    lines = {w["key"]: w for w in up["water_lines"]}
    assert lines["refill"]["gal"] == 0 and lines["refill"]["note"].startswith("Never")
    assert om["water"] == pytest.approx(lines["evaporation"]["cost"] + lines["spray"]["cost"], abs=0.02)
    assert om["water"] < 2000, "make-up only: not the $112k a year of refilling 1.9 million gallons monthly"
    assert not any("Drain, clean" in m["task"] for m in up["maintenance"])
    text = visible_text(render(spf, "pond", prints.PF_OM))
    assert "Never: a pond or lake keeps its water" in text
    report = visible_text(render(spf, "pond", prints.PF_REPORT))
    assert prints.money(om["total"], cents=False) + " a year" in report
    # A recirculating feature still drains and refills.
    _, lobby = designed("lobby")
    assert lobby["paper"]["upkeep"]["om"]["refills"] > 0 and not lobby["paper"]["upkeep"]["water_plan"]["natural"]


def test_winterization_reads_the_module_checklist(spf):
    out, stored = designed("plaza")
    w = stored["paper"]["winterization"]
    titles = [i["title"] for i in w["winterize"]]
    assert out["outputs"]["winterization"]["steps"] == titles[:40], "the page's drawer gets the real steps"
    assert "Pull the upward-facing nozzles and fit winter caps" in titles
    assert w["checklist_text"].startswith("WINTERIZE") and "[ ] Inspect the basin" in w["checklist_text"]
    text = visible_text(render(spf, "plaza", prints.PF_OM))
    assert "Spring start-up" in text and "Checklist text for a Maintenance Visit" in text and "30 in" in text


def test_an_indoor_feature_needs_no_winterization():
    m = demo_model("lobby")
    m["features"][0].setdefault("inputs", {})["indoor"] = True
    out = derive.derive_model(m, CATALOG, None, prices=PRICES)
    assert out["outputs"]["winterization"]["needed"] is False
    assert out["paper"]["winterization"]["winterize"] == []
    assert "WINTERIZE" not in out["paper"]["winterization"]["checklist_text"]


def test_the_packet_is_the_plan_set_and_never_the_budget(spf):
    page = render(spf, "spa", prints.PF_PACKET)
    text = visible_text(page)
    for title in ("HEALTH DEPARTMENT SUBMITTAL", "Design basis", "Equipment schedule", "Piping schedule", "Fittings and cut list",
                  "Electrical", "Structural takeoff", "Design drawings", "Plan check", "Calculations by section",
                  "Standard details (SP-4)", "Standard notes", "P.E. STAMP", "SHEET INDEX", "Issued for plan check", "11/01/2026"):
        assert title in text, title
    assert "Budget by section" not in text and "Cost per year" not in text
    assert page.count("page-break-before:always") == len(prints.PACKET_SHEETS) + 2


def test_the_outputs_list_is_what_derive_and_the_page_carry():
    out, _ = designed("lobby")
    assert [s["key"] for s in out["outputs"]["schedules"]] == [k for k, _l, _pf in prints.OUTPUTS]
    assert all(s["url"] is None for s in out["outputs"]["schedules"])
    for name in ("lobby", "plaza", "pond"):
        reply = json.loads((DEMO / f"derive_{name}.json").read_text(encoding="utf-8"))
        assert [s["key"] for s in reply["outputs"]["schedules"]] == [k for k, _l, _pf in prints.OUTPUTS], name


# ----------------------------------------------------------------------------- the budget


def test_budget_groups_contingency_and_range():
    bom = [
        {"section": "Piping", "line_key": "pipe|SCH40 PVC|3\"", "description": "pipe", "qty": 10, "rate": 5, "amount": 50},
        {"section": "Equipment", "line_key": "equipment|filter|F1", "item": "F1", "qty": 1, "rate": 400, "amount": 400},
        {"section": "Equipment", "line_key": "equipment|light|L1", "item": "L1", "qty": 4, "rate": 0, "amount": 0},
        {"section": "Electrical", "line_key": "xfmr:lighting:60", "qty": 1, "rate": 90, "amount": 90},
        {"section": "Electrical", "line_key": "main:25", "qty": 1, "rate": 60, "amount": 60},
        {"section": "Controls", "line_key": "estop", "qty": 1, "rate": 0, "amount": 0},
        {"section": "Structural", "line_key": "structural.concrete", "qty": 2, "rate": 200, "amount": 400},
        {"section": "Labor", "line_key": "labor|family|weir", "qty": 8, "rate": 100, "amount": 800},
        {"section": "Equipment", "item": "X", "qty": 2, "rate": 25, "amount": None, "source": "Manual"},
        {"section": "Freight", "qty": 1, "rate": 30, "amount": 30, "source": "Manual"},
    ]
    s = prints.budget_summary(bom, {"contingency_pct": 10, "range_pct": 15, "labor_rate_per_hr": 100})
    sub = {g["label"]: g["subtotal"] for g in s["groups"]}
    assert sub == {"Hydraulic": 100.0, "Treatment": 400.0, "Lighting": 90.0, "Electrical": 60.0, "Controls": 0.0,
                   "Structural": 400.0, "Labor": 800.0, "Other": 30.0}
    assert s["subtotal"] == 1880.0
    assert s["contingency"] == 188.0 and s["total"] == 2068.0
    assert s["low"] == pytest.approx(2068 * 0.85) and s["high"] == pytest.approx(2068 * 1.15)
    assert s["unpriced"] == 2 and s["unpriced_groups"] == ["Lighting", "Controls"]
    zero = prints.budget_summary(bom, {})
    assert zero["contingency"] == 0 and zero["low"] == zero["high"] == zero["total"] == zero["subtotal"]


def test_the_budget_subtotal_is_the_rollup_and_says_what_has_no_price(spf):
    out, stored = designed("lobby")
    s = prints.budget_summary(out["bom"], stored["paper"]["budget_rules"])
    assert s["subtotal"] == pytest.approx(out["rollups"]["budget_total"])
    text = visible_text(render(spf, "lobby", prints.PF_BUDGET))
    assert "No Item" in text and "have no price" in text and "Contingency (10%)" in text
    assert "Range (plus or minus 15%)" in text
    assert "No published cost-per-square-foot figure" in text


def test_labor_is_priced_at_the_house_rule_and_never_invented():
    rules = {"labor_hours_weir": 8, "labor_hours_per_sqft_basin": 0.5}
    unpriced, _ = designed("lobby", rules)
    labor = [r for r in unpriced["bom"] if r["section"] == "Labor"]
    assert labor and all(r["rate"] == 0 and r["amount"] == 0 for r in labor), "0 = not set: no fabricated labor"
    priced, stored = designed("lobby", {**rules, "labor_rate_per_hr": 95})
    labor = [r for r in priced["bom"] if r["section"] == "Labor"]
    assert all(r["rate"] == 95 and r["amount"] == pytest.approx(95 * r["qty"], abs=0.01) and r["catalog_source"] == "house rule"
               for r in labor)
    assert priced["rollups"]["budget_total"] == pytest.approx(unpriced["rollups"]["budget_total"] + sum(r["amount"] for r in labor), abs=0.05)
    assert stored["paper"]["budget_rules"] == {"labor_rate_per_hr": 95.0, "contingency_pct": 10.0, "range_pct": 15.0}


# ----------------------------------------------------------------------------- odd records


def test_a_section_that_fails_prints_a_line_not_a_trace(monkeypatch):
    def boom(_d):
        raise KeyError("x")

    monkeypatch.setitem(prints.SHEETS, "om", ("Operations and maintenance", boom))
    sheet = prints.build({}, {}, {}, [], sheets=["om"])["sheets"]["om"]
    assert sheet["blocks"][0]["kind"] == "note" and "could not be drawn" in sheet["blocks"][0]["text"]


def test_junk_stored_values_never_raise():
    for junk in ({"results": "x", "rollups": [], "paper": 3, "electrical": "y"}, {"results": {"bom_rows": {"value": [1, 2]}}}):
        ctx = prints.build({"name": "J"}, {"network": "bad", "features": [None, 3]}, junk, [None, {"qty": "lots"}])
        assert set(ctx["sheets"]) == set(prints.SHEETS)


def test_formatting_helpers():
    assert prints.fnum(1234.567, 1, "gpm") == "1,234.6 gpm"
    assert prints.fnum(None) == prints.DASH and prints.fnum(float("nan")) == prints.DASH
    assert prints.fnum(-0.0001, 2) == "0.00"
    assert prints.fqty(3.0) == "3" and prints.fqty(2.5) == "2.5" and prints.fqty(1200) == "1,200"
    assert prints.money(1234.5) == "$1,234.50" and prints.money(-5) == "-$5.00" and prints.money(None) == prints.DASH
    assert prints.cell("x", href="javascript:alert(1)").get("href") is None
    assert prints.cell("x", href="https://example.com/a.pdf")["href"] == "https://example.com/a.pdf"


def test_the_controller_stores_what_the_prints_read():
    src = (APP / "water_engineering" / "doctype" / "water_feature_design" / "water_feature_design.py").read_text(encoding="utf-8")
    keys = re.search(r"RESULT_KEYS = \((.*?)\n\)", src, re.S).group(1)
    assert set(re.findall(r'"(\w+)"', keys)) == set(STORED_KEYS)


# ----------------------------------------------------------------------------- packet.we_print


class FakeDoc(dict):
    def __getattr__(self, key):
        return self.get(key)


@pytest.fixture()
def packet(monkeypatch):
    frappe = types.ModuleType("frappe")
    frappe._ = lambda s, *a, **k: s
    frappe.logged = []
    frappe.log_error = lambda *a, **k: frappe.logged.append(k.get("title") or a)
    frappe.get_traceback = lambda *a, **k: ""
    frappe.get_meta = lambda doctype: types.SimpleNamespace(has_field=lambda f: True)
    frappe.get_all = lambda doctype, **k: [{"name": "STUB-SEL-1.5", "item_name": "Self-priming pump", "custom_model_no": "SP-15",
                                             "custom_spec_source_url": "https://example.com/sp15.pdf"}]
    frappe.whitelist = lambda *a, **k: (lambda f: f)
    frappe.db = types.SimpleNamespace(get_value=lambda *a, **k: None)
    monkeypatch.setitem(sys.modules, "frappe", frappe)
    monkeypatch.delitem(sys.modules, "erpnext_enhancements.water_engineering.packet", raising=False)
    module = importlib.import_module("erpnext_enhancements.water_engineering.packet")
    module._frappe = frappe
    return module


def test_we_print_reads_the_record(packet):
    out, stored = designed("lobby")
    doc = FakeDoc(name="WFD-1", design_title="Lobby", model_json=json.dumps(out["model"]), results_json=json.dumps(stored),
                  design_issues_json=json.dumps(out["issues"]), bom=[FakeDoc(r) for r in out["bom"]])
    ctx = packet.we_print(doc, ["equipment"])
    rows = ctx["sheets"]["equipment"]["blocks"][0]["rows"]
    pump = rows[0]["cells"]
    assert pump[1]["text"] == "Self-priming pump" and pump[3]["text"] == "SP-15" and pump[7]["href"] == "https://example.com/sp15.pdf"
    assert packet.we_circulation_schematic(doc).startswith("<svg")


def test_we_print_never_raises(packet):
    def boom(*a, **k):
        raise RuntimeError("no db")

    packet._frappe.get_all = boom
    ctx = packet.we_print(FakeDoc(name="WFD-2", model_json="{not json", results_json="[]", bom=None), ["budget"])
    assert ctx["name"] == "WFD-2" and ctx["sheets"]["budget"]["blocks"]
    packet._frappe.get_meta = boom
    assert packet._item_info(["A"]) == {}
    ctx = packet.we_print(types.SimpleNamespace(name="WFD-3"), ["results"])  # not even a dict: logged, still prints
    assert ctx["name"] == "WFD-3" and packet._frappe.logged


def test_the_print_inputs_copy_from_a_deep_copy():
    out, stored = designed("pond")
    before = copy.deepcopy(stored)
    prints.build({}, out["model"], stored, out["bom"])
    assert stored == before, "building the context never writes into the stored derive"


# ----------------------------------------------------------------------------- the house style (Nikolas, 2026-11-01)


@pytest.mark.parametrize("print_format", list(prints.FORMATS))
def test_every_view_wears_the_print_design_system(spf, print_format):
    """The Pillar Stripe chrome (docs/print-design-system.md): Design pillar stripe and eyebrow, letterhead,
    the facts row (customer party block, project and site, designer and status), a red DRAFT on a design
    not issued, the running line and closing stripe, Letter paper."""
    page = render(spf, "lobby", print_format)
    text = visible_text(page)
    stripe = prints.ps.stripe_css(prints.PILLAR)
    assert page.count(stripe) == 2, "the top stripe and the closing stripe, in the Design pillar's colors"
    assert f"color:{prints.ps.VIOLET_DEEP}" in page and "DESIGN" in text, "the Design pillar's eyebrow"
    for label in ("CUSTOMER", "PROJECT AND SITE", "DESIGNER"):
        assert label in text
    assert "Acme & Sons" in text and "PRJ-1" in text and "Logan" in text
    assert "DRAFT" in text and "11/01/2026" in text
    assert "@page{size:letter}" in page
    assert "Sapphire Fountains, LLC" in text and "www.sapphirefountains.com" in text
    squashed = page.replace(" ", "")
    assert "display:flex" not in squashed and "display:grid" not in squashed, "print-safe CSS only"
    assert "box-shadow" not in page and "border-radius" not in page, "no cards, no shadows"


def test_an_issued_design_prints_no_draft_marker(spf):
    text = visible_text(render(spf, "lobby", prints.PF_REPORT, {"docstatus": 1, "issue_date": "2026-11-02"}))
    assert "DRAFT" not in text


def test_the_design_report_is_the_client_view(spf):
    out, stored = designed("lobby")
    page = render(spf, "lobby", prints.PF_REPORT)
    text = visible_text(page)
    for heading in ("What the feature does", "At a glance", "How the water moves", "Where the pump runs", "Equipment",
                    "Budget by section", "Running costs per year", "Notes and assumptions"):
        assert heading in text, heading
    assert page.count("<svg") >= 3, "the wordmark, the schematic and the duty-point chart"
    assert page.count("page-break-after:always") == 2, "summary, drawings, then the lists"
    # The client's equipment list names things; part numbers stay on the Schedules.
    for code in [r["item"] for r in out["bom"] if r.get("item")] + ["PNT-CCP520", "PNT-MCL-12"]:
        assert code not in text, code
    s = prints.budget_summary(out["bom"], stored["paper"]["budget_rules"])
    assert prints.money(s["total"], cents=False) in text
    assert "Contingency (10%)" in text and "Range (plus or minus 15%)" in text


def test_the_report_is_the_doctype_default():
    rows = json.loads((APP / "fixtures" / "property_setter.json").read_text(encoding="utf-8"))
    row = next(r for r in rows if r["name"] == "Water Feature Design-main-default_print_format")
    assert (row["doc_type"], row["property"], row["value"]) == ("Water Feature Design", "default_print_format", prints.PF_REPORT)


def test_schedules_start_each_schedule_on_a_page(spf):
    page = render(spf, "plaza", prints.PF_SCHEDULES)
    sheets = prints.FORMATS[prints.PF_SCHEDULES][1]
    assert page.count("page-break-before:always") == len(sheets) - 1
    text = visible_text(page)
    for title in ("Equipment schedule", "Piping schedule", "Fittings and cut list", "Structural takeoff", "Electrical", "Controls"):
        assert title in text


def test_engineering_calculations_follow_the_ladder(spf):
    text = visible_text(render(spf, "plaza", prints.PF_CALCS))
    order = [s for s, _n in prints.LADDER if s in text]
    positions = [text.index(s) for s in order]
    assert positions == sorted(positions) and len(order) >= 6, order
    assert text.index("Calculations by section") < text.index("Plan check")


def test_superseded_formats_are_disabled_not_deleted(spf):
    state = {"Water Feature Design - Results": 0, "Water Feature Design - Calculation Audit": 1}
    writes = []
    spf.frappe.db = types.SimpleNamespace(
        exists=lambda doctype, name: name in state,
        get_value=lambda doctype, name, field: state[name],
        set_value=lambda doctype, name, field, value, update_modified=True: writes.append((name, field, value, update_modified)),
    )
    spf.disable_superseded_formats()
    assert writes == [("Water Feature Design - Results", "disabled", 1, False)]
    assert set(prints.SUPERSEDED).isdisjoint(prints.PRINT_FORMATS)
