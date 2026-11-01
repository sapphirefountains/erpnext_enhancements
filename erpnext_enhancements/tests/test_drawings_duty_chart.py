"""Bench-free pytest suite for the Fountain Designer page's server-made drawings:
``engine/drawings.pump_duty_svg`` (the duty-point chart) and ``page_svg`` (the
adapter that fits the print drawings to the page).

The page draws these through the allow-list in ``public/js/fountain_designer/chart.js``,
whose node fallback refuses the WHOLE drawing over one tag or attribute it does not
know. ``passes()`` below is a port of that fallback, and it reads ELEMENTS and
ATTRIBUTES out of chart.js at test time, so the suite follows the page. When node is
on the PATH, chart.js's own ``safe_svg`` is run too.

Run: python -m pytest erpnext_enhancements/tests/test_drawings_duty_chart.py -q
"""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.water_engineering.engine import drawings as D

CHART_JS = REPO_ROOT / "erpnext_enhancements" / "public" / "js" / "fountain_designer" / "chart.js"
CHART_SRC = CHART_JS.read_text(encoding="utf-8")


def _js_set(name):
    m = re.search(rf"const {name} = new Set\(\[([\s\S]*?)\]\)", CHART_SRC)
    assert m, f"{name} not found in chart.js"
    return frozenset(re.findall(r'"([^"]+)"', m.group(1)))


ELEMENTS = _js_set("ELEMENTS")
ATTRIBUTES = _js_set("ATTRIBUTES")


def _safe_value(value):
    if re.search(r"javascript:|data:|expression\(|@import", value, re.I):
        return False
    if re.search(r"url\(", value, re.I) and not re.match(r"^\s*url\(\s*#[\w-]+\s*\)\s*$", value, re.I):
        return False
    return True


def refusal(source):
    """chart.js's ``filtered()``: None when the drawing passes, else why it is refused."""
    source = source.strip()
    if not re.match(r"^(<\?xml[^>]*>\s*)?<svg[\s>]", source, re.I):
        return "does not start with <svg"
    body = re.sub(r"<!--[\s\S]*?-->", "", re.sub(r"<\?xml[^>]*>", "", source, flags=re.I))
    if re.search(r"<!\[CDATA\[|<!DOCTYPE|<!ENTITY", body, re.I):
        return "CDATA/DOCTYPE/ENTITY"
    for tag in re.findall(r"<[^>]*>", body):
        m = re.match(r"^</?\s*([a-zA-Z][\w:-]*)([\s\S]*?)/?>$", tag)
        if not m:
            return f"unparsable tag {tag!r}"
        if m.group(1).lower() not in ELEMENTS:
            return f"element {m.group(1)!r}"
        rest = stripped = m.group(2)
        for a in re.finditer(r"""([^\s=]+)\s*=\s*("[^"]*"|'[^']*')""", rest):
            if a.group(1).lower() not in ATTRIBUTES:
                return f"attribute {a.group(1)!r} in {tag!r}"
            if not _safe_value(a.group(2)[1:-1]):
                return f"value of {a.group(1)!r} in {tag!r}"
            stripped = stripped.replace(a.group(0), "", 1)
        if stripped.strip():
            return f"leftover {stripped.strip()!r} in {tag!r}"
    return None


def passes(svg):
    why = refusal(svg)
    assert why is None, f"chart.js would refuse the drawing: {why}"


def root_tag(svg):
    return re.match(r"<svg\b[^>]*>", svg).group(0)


# --- states ------------------------------------------------------------------------------------------

PUMP = [  # a 2 HP pool pump, full speed, deliberately out of order
    {"flow_gpm": 160, "head_ft": 18.0},
    {"flow_gpm": 0, "head_ft": 52.0},
    {"flow_gpm": 40, "head_ft": 50.5},
    {"flow_gpm": 120, "head_ft": 32.0},
    {"flow_gpm": 80, "head_ft": 44.0},
]
SYSTEM = [{"flow_gpm": q, "head_ft": round(8 + 0.0011 * q * q, 2)} for q in range(0, 181, 20)]

REALISTIC = {
    "title": "Duty point",
    "pumps": [{"label": "WhisperFlo 2 HP", "curve": PUMP, "speeds": [80, 60]}],
    "system": SYSTEM,
    "duty": {"flow_gpm": 132, "head_ft": 27.6},
    "bep": {"flow_lo_gpm": 95, "flow_hi_gpm": 145},
    "npsh": {"available_ft": 18.0, "required_ft": 5.7, "margin_ft": 12.3},
    "design": {"flow_gpm": 125},
}

NAN, INF = float("nan"), float("inf")
JUNK = [
    None,
    "not a state",
    42,
    [],
    {"pumps": "WhisperFlo", "system": 7, "duty": "132 gpm", "bep": [1, 2], "npsh": None, "design": "x"},
    {"pumps": [None, 3, {"curve": None}, {"curve": [{"flow_gpm": NAN, "head_ft": 1}, "x"]}]},
    {
        "title": None,
        "pumps": [{"label": None, "curve": [{"flow_gpm": 0, "head_ft": 40}, {"flow_gpm": INF, "head_ft": 2},
                                           {"flow_gpm": "100", "head_ft": "20"}, {"flow_gpm": True, "head_ft": 5}],
                   "speeds": [NAN, "fast", -10, 0, 100, 250, None, 70]}],
        "system": [{"flow_gpm": -5, "head_ft": 3}, {"flow_gpm": 50, "head_ft": NAN}, {"head_ft": 4}],
        "duty": {"flow_gpm": NAN, "head_ft": 20},
        "bep": {"flow_lo_gpm": INF, "flow_hi_gpm": 90},
        "npsh": {"available_ft": "lots", "required_ft": NAN, "margin_ft": -INF},
        "design": {"flow_gpm": -INF},
    },
    {"duty": {"flow_gpm": 0, "head_ft": 0}},
    {"duty": {"flow_gpm": 1e300, "head_ft": 1e-300}, "bep": {"flow_lo_gpm": 5, "flow_hi_gpm": 5}},
]


# --- the allow-list copy ---------------------------------------------------------------------------

def test_attribute_copy_matches_chart_js():
    assert D._PAGE_ATTRIBUTES == ATTRIBUTES
    assert {"svg", "polyline", "circle", "rect", "text", "title", "line"} <= ELEMENTS


def test_port_refuses_what_chart_js_refuses():
    assert refusal('<svg viewBox="0 0 1 1" style="x"></svg>')
    assert refusal('<svg viewBox="0 0 1 1"><script>1</script></svg>')
    assert refusal('<svg viewBox="0 0 1 1"><rect fill="url(http://x)"/></svg>')
    assert refusal('<svg viewBox="0 0 1 1"><rect onclick="x"/></svg>')
    assert refusal('<svg viewBox="0 0 1 1"><rect fill="url(#g)"/></svg>') is None


# --- pump_duty_svg -----------------------------------------------------------------------------------

def test_realistic_chart_passes_and_carries_the_page_classes():
    svg = D.pump_duty_svg(REALISTIC)
    passes(svg)
    for cls in ("fd-curve-pump", "fd-curve-system", "fd-point", "fd-bep", "fd-grid"):
        assert f'class="{cls}"' in svg, cls
    root = root_tag(svg)
    assert 'viewBox="0 0 480 300"' in root and 'role="img"' in root
    assert 'aria-label="Duty point: 132 gpm at 27.6 ft"' in root
    assert " width=" not in root and " height=" not in root
    assert "<title>Duty point: 132 gpm at 27.6 ft</title>" in svg
    assert "style=" not in svg and "markerUnits" not in svg and "#" not in svg.replace("url(#", "")
    assert "NPSH margin 12.3 ft (available 18.0, required 5.7)" in svg
    assert "Flow (gpm)" in svg and "Head (ft)" in svg
    assert 'class="fd-design"' in svg and "Design 125 gpm" in svg
    assert 'class="fd-bep"' in svg and 'fill-opacity="0.08"' in svg
    # one full-speed curve and two dotted VFD-speed curves, all the pump class
    assert len(re.findall(r'<polyline class="fd-curve-pump"', svg)) == 3
    assert len(re.findall(r'<polyline class="fd-curve-pump"[^>]*stroke-dasharray', svg)) == 2


def _polyline(svg, series):
    m = re.search(rf'data-series="{re.escape(series)}" points="([^"]+)"', svg)
    return [tuple(map(float, p.split(","))) for p in m.group(1).split()]


def test_curves_are_sorted_and_speeds_follow_the_affinity_laws():
    svg = D.pump_duty_svg(REALISTIC)
    full = _polyline(svg, "pump-1")
    assert [x for x, _ in full] == sorted(x for x, _ in full)
    left, bottom = D._PLOT_L, D._PLOT_B
    for pct in (80, 60):
        slow = _polyline(svg, f"pump-1-{pct}")
        k = pct / 100
        # shut-off head scales by s^2, run-out flow by s (1-decimal rounding allowed)
        assert (bottom - slow[0][1]) == pytest.approx((bottom - full[0][1]) * k * k, abs=0.15)
        assert (slow[-1][0] - left) == pytest.approx((full[-1][0] - left) * k, abs=0.15)


def test_duty_point_lands_inside_the_plot_where_its_numbers_say():
    svg = D.pump_duty_svg(REALISTIC)
    m = re.search(r'<circle class="fd-point"[^>]*cx="([\d.]+)" cy="([\d.]+)"', svg)
    cx, cy = float(m.group(1)), float(m.group(2))
    assert D._PLOT_L < cx < D._PLOT_R and D._PLOT_T < cy < D._PLOT_B
    # the axis tick labels give the scale: last x tick and last y tick sit at the plot edges
    x_ticks = [float(t) for t in re.findall(rf'y="{D._PLOT_B + 16}" text-anchor="middle" font-size="11" '
                                             r'fill="currentColor">([\d.]+)<', svg)]
    y_ticks = [float(t) for t in re.findall(r'text-anchor="end" dominant-baseline="middle" font-size="11" '
                                            r'fill="currentColor">([\d.]+)<', svg)]
    q = (cx - D._PLOT_L) / (D._PLOT_R - D._PLOT_L) * max(x_ticks)
    h = (D._PLOT_B - cy) / (D._PLOT_B - D._PLOT_T) * max(y_ticks)
    assert q == pytest.approx(132, abs=0.5) and h == pytest.approx(27.6, abs=0.1)
    # ~10% headroom over the largest flow/head drawn, on round ticks
    assert 160 * 1.1 <= max(x_ticks) <= 160 * 1.6 and 52 * 1.1 <= max(y_ticks) <= 52 * 1.6
    assert min(x_ticks) == 0 and min(y_ticks) == 0


def test_coordinates_are_rounded_to_one_decimal():
    svg = D.pump_duty_svg(REALISTIC)
    for name, value in re.findall(r'\b(x|y|x1|y1|x2|y2|cx|cy|width|height|points)="([^"]*)"', svg):
        for number in re.findall(r"-?\d+(?:\.\d+)?", value):
            assert len(number.partition(".")[2]) <= 1, (name, value)


@pytest.mark.parametrize("state", [None, {}, {"pumps": []}, {"bep": {"flow_lo_gpm": 1, "flow_hi_gpm": 2}}])
def test_nothing_drawable_says_so(state):
    svg = D.pump_duty_svg(state)
    passes(svg)
    assert "No duty point yet" in svg and "<title>" in svg and 'role="img"' in svg
    assert " width=" not in root_tag(svg)


@pytest.mark.parametrize("state", JUNK)
def test_junk_never_raises_and_still_passes(state):
    svg = D.pump_duty_svg(state)
    passes(svg)
    assert not re.search(r"nan|inf", re.sub(r"<[^>]*>", " ", svg), re.I) or "No duty point yet" in svg
    assert not re.search(r'="[^"]*(nan|inf)[^"]*"', svg, re.I)


def test_numeric_strings_count_and_bad_speeds_drop():
    svg = D.pump_duty_svg(JUNK[6])
    passes(svg)
    full = _polyline(svg, "pump-1")
    assert len(full) == 2  # (0, 40) and ("100", "20"); inf and bool points dropped
    assert re.findall(r'data-series="pump-1-([\d.]+)"', svg) == ["70"]
    assert 'class="fd-point"' not in svg and 'class="fd-bep"' not in svg and "NPSH" not in svg


def test_duty_point_alone_is_drawn():
    svg = D.pump_duty_svg({"duty": {"flow_gpm": 4.5, "head_ft": 12}})
    passes(svg)
    assert 'class="fd-point"' in svg and 'aria-label="Duty point: 4.5 gpm at 12.0 ft"' in svg


def test_labels_and_title_are_escaped():
    svg = D.pump_duty_svg({
        "title": 'Pump "A" <b>',
        "pumps": [{"label": "<script>x & co", "curve": PUMP}],
        "duty": {"flow_gpm": 100, "head_ft": 38},
    })
    passes(svg)
    assert "<script" not in svg and "&lt;script&gt;x &amp; co" in svg
    assert "Pump &quot;A&quot; &lt;b&gt;: 100 gpm at 38.0 ft" in svg


def test_npsh_margin_is_worked_out_when_not_given():
    svg = D.pump_duty_svg({"duty": {"flow_gpm": 50, "head_ft": 20},
                           "npsh": {"available_ft": 18, "required_ft": 5.7}})
    assert "NPSH margin 12.3 ft (available 18.0, required 5.7)" in svg
    svg = D.pump_duty_svg({"duty": {"flow_gpm": 50, "head_ft": 20}, "npsh": {"margin_ft": -1.25}})
    assert "NPSH margin -1.2 ft<" in svg or "NPSH margin -1.3 ft<" in svg


def test_reversed_bep_window_is_put_right():
    svg = D.pump_duty_svg({**REALISTIC, "bep": {"flow_lo_gpm": 145, "flow_hi_gpm": 95}})
    m = re.search(r'<rect class="fd-bep" x="([\d.]+)" y="[\d.]+" width="([\d.]+)"', svg)
    assert float(m.group(2)) > 0


def test_several_pumps_fit_the_legend():
    pumps = [{"label": f"Very long pump model name number {i}", "curve": PUMP, "speeds": [75]} for i in range(5)]
    svg = D.pump_duty_svg({"pumps": pumps, "system": SYSTEM})
    passes(svg)
    xs = [float(x) for x in re.findall(r'<line class="fd-curve-[a-z]+" x1="([\d.]+)" y1="12"', svg)]
    assert xs and min(xs) >= D._PLOT_L


# --- page_svg ----------------------------------------------------------------------------------------

CIRCULATION = {
    "title": "Circulation Equipment Schematic",
    "nodes": [
        {"label": "Basin", "sub": "1,200 gal"},
        {"label": "Pump", "sub": "WhisperFlo 2 HP"},
        {"label": "Filter", "sub": "CCP520"},
        "Heater",
        {"label": "Returns", "sub": "6 x 1.5 in"},
    ],
}
ONELINE = {
    "title": "Electrical One-Line",
    "service": {"label": "240 V 1φ Service", "main_breaker": 100, "sub": "100 A available"},
    "feeder": {"label": "Feeder #2 + #8G, 1-1/4 PVC", "sub": "85 ft, 1.9% VD"},
    "panel": {"label": "Fountain panel"},
    "branches": [
        {"label": "P-1 Display pump", "sub": "2 HP, 12.0 A, 230 V 1φ", "breaker": "20 A 2P GFCI",
         "wire": "#12 + #12G, 3/4 PVC, 1.2% VD", "vfd": "VFD 2 HP"},
        {"label": "L-1 Lights", "sub": "1.5 A, 120 V 1φ", "breaker": 15, "wire": "#12 + #12G"},
    ],
    "transformers": [{"label": "Lighting transformer 300 VA", "sub": "120/12 V"}],
    "transformer_va": 150,
}
POWER = {
    "title": "Power Schematic",
    "service": {"label": "480 V 3φ"},
    "disconnect": {"label": "60 A disconnect"},
    "spd": True,
    "pumps": [
        {"tag": "P-1", "label": "Display", "topology": "vfd_bypass", "breaker": "30 A 3P", "vfd": "VFD 10 HP",
         "overload": "OL 14 A", "sub": "10 HP"},
        {"tag": "P-2", "label": "Filter", "topology": "across_the_line", "breaker": "15 A 3P",
         "contactor": "K 18 A", "overload": "OL 4 A"},
    ],
    "transformers": [{"label": "Control transformer 350 VA"}],
}
LADDER = {
    "title": "Control Ladder",
    "supply": "120 VAC from CT-1",
    "rungs": [
        {"label": "E-stop", "elements": [{"type": "nc", "label": "ES-1"}, {"type": "coil", "label": "CR-ES"}]},
        {"label": "Run", "elements": [{"type": "no", "label": "HOA-H"}, {"type": "box", "label": "Controller"},
                                      {"type": "light", "label": "Run", "color": "green"}]},
        {"label": "Fault", "elements": [{"type": "no", "label": "FLT"},
                                        {"type": "light", "label": "Fault", "color": "red"}]},
    ],
}
PRINT_DRAWINGS = [
    ("circulation", D.circulation_schematic_svg, CIRCULATION),
    ("oneline", D.electrical_oneline_svg, ONELINE),
    ("power", D.power_schematic_svg, POWER),
    ("ladder", D.control_ladder_svg, LADDER),
    ("circulation-empty", D.circulation_schematic_svg, {}),
]


@pytest.mark.parametrize("svg", [None, "", "   ", 0])
def test_page_svg_of_nothing_is_none(svg):
    assert D.page_svg(svg) is None


@pytest.mark.parametrize("name,draw,state", PRINT_DRAWINGS, ids=[d[0] for d in PRINT_DRAWINGS])
def test_page_svg_makes_print_drawings_pass(name, draw, state):
    printed = draw(state)
    assert refusal(printed), "the print drawing should need the adapter"
    svg = D.page_svg(printed)
    passes(svg)
    root = root_tag(svg)
    assert "viewBox=" in root and " width=" not in root and " height=" not in root
    assert "style=" not in svg and "markerUnits" not in svg
    lowered = svg.lower()
    for ink in ("#ffffff", D._INK, D._BORDER, D._MUTED, D._ACCENT, D._BOX, "#fff7e6", "#eef6ff", "#fdecea"):
        assert ink not in lowered, ink
    # boxes keep a faint wash so they read on a dark page; every label survives
    if name != "circulation-empty":
        assert 'fill="currentColor" stroke="currentColor"' in svg and 'fill-opacity="0.06"' in svg
    for text in re.findall(r">([^<>]+)</text>", printed):
        assert f">{text}</text>" in svg
    # element count unchanged apart from the white background
    assert svg.count("<") == printed.count("<") - 1


def test_print_drawings_are_unchanged():
    printed = D.circulation_schematic_svg(CIRCULATION)
    assert 'fill="#ffffff"' in printed and "style=" in printed and 'markerUnits="strokeWidth"' in printed
    D.page_svg(printed)
    assert D.circulation_schematic_svg(CIRCULATION) == printed


def test_page_svg_keeps_inner_sizes():
    svg = D.page_svg(D.circulation_schematic_svg(CIRCULATION))
    assert re.search(r'<rect x="12" y="44" width="118" height="60"', svg)


# --- chart.js itself, when node is here ----------------------------------------------------------------

NODE = shutil.which("node")
NODE_SCRIPT = """
import { readFileSync } from "node:fs";
const src = readFileSync(process.argv[1], "utf8");
const mod = await import("data:text/javascript;base64," + Buffer.from(src).toString("base64"));
const inputs = JSON.parse(readFileSync(0, "utf8"));
process.stdout.write(JSON.stringify(inputs.map((s) => mod.safe_svg(s).length)));
"""


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_chart_js_safe_svg_accepts_them():
    good = [D.pump_duty_svg(REALISTIC), D.pump_duty_svg(None)] + [D.pump_duty_svg(s) for s in JUNK]
    good += [D.page_svg(draw(state)) for _, draw, state in PRINT_DRAWINGS]
    raw = D.circulation_schematic_svg(CIRCULATION)
    proc = subprocess.run(
        [NODE, "--input-type=module", "-e", NODE_SCRIPT, "--", str(CHART_JS)],
        input=json.dumps([*good, raw]),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=True,
    )
    lengths = json.loads(proc.stdout)
    assert all(n > 0 for n in lengths[:-1]), lengths
    assert lengths[-1] == 0  # the print drawing as it is: refused, which is why page_svg exists
