"""Bench-free tests for ``water_engineering/svg_safe.py``: a stored drawing is printed only as the SVG
the Fountain Designer itself draws.

* **The rule runs**: samples carrying elements, attributes and values off the allowlist (assembled from
  fragments) come back without them, and text that is not one well-formed SVG root comes back ``""``.
  Every output is read back with ``html.parser``, the way a page reads it, and holds only listed names.
* **The module's own drawings survive whole**: the real derive over four designs (the three demo
  designs and a regulated spa, as ``test_water_prints`` builds them), the print drawings
  ``engine/drawings.py`` makes directly and the seeded Standard Details all come back with the same
  elements, attributes and text. The allowlist is exactly what they use, no more.
* **Every print path goes through it**: the formats and the packet place a drawing only through
  ``we_safe_svg``, ``prints.drawing`` and the packet helpers call ``clean_svg``, and the packet preview
  is a sandboxed frame, not a document written into a window of the site.

Plain pytest functions; frappe is stubbed per test (monkeypatch). Its own ``python -m pytest`` step in
ci.yml, with ``test_water_prints``.

Run: python -m pytest erpnext_enhancements/tests/test_water_svg_safe.py -q
"""

import ast
import importlib
import json
import re
import sys
import types
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.tests.test_water_prints import MODELS, FakeDoc, designed, environment
from erpnext_enhancements.water_engineering import prints, svg_safe
from erpnext_enhancements.water_engineering.engine import drawings

APP = REPO_ROOT / "erpnext_enhancements"
WE = APP / "water_engineering"
clean = svg_safe.clean_svg

# Names assembled at run time, so the samples read as data rather than markup in the source.
SCR = "scr" + "ipt"
FO = "foreign" + "Object"
ON = "on" + "load"
JS = "java" + "script:"
EXT = "https://" + "example.invalid/x"


def svg(body, attrs=""):
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"{attrs}>{body}</svg>'


class _Reader(HTMLParser):
    """Every tag and attribute name in a page, as an HTML parser reads it (lower case)."""

    def __init__(self):
        super().__init__()
        self.tags, self.attrs = set(), set()

    def handle_starttag(self, tag, attrs):
        self.tags.add(tag)
        self.attrs.update(name for name, _ in attrs)

    handle_startendtag = handle_starttag


def read(markup):
    r = _Reader()
    r.feed(markup)
    r.close()
    return r


def assert_only_listed(markup):
    r = read(markup)
    assert r.tags <= {e.lower() for e in svg_safe.ELEMENTS}, r.tags
    assert r.attrs <= {a.lower() for a in svg_safe.ATTRIBUTES} | {"style", "xmlns"}, r.attrs
    low = markup.lower()
    for token in (SCR, FO.lower(), JS, "example.invalid", "href", "url(h", "url(d", "expression", "@import"):
        assert token not in low, token


def tree(markup):
    def walk(el):
        return (el.tag.split("}")[-1], sorted(el.attrib.items()), el.text or "", el.tail or "", [walk(c) for c in el])

    return walk(ET.fromstring(markup))


# ----------------------------------------------------------------------------- the rule runs


def test_a_script_element_is_dropped_and_its_siblings_kept():
    out = clean(svg(f'<{SCR}>alert(1)</{SCR}><rect x="1" y="1" width="2" height="2"/>'))
    assert_only_listed(out)
    assert "alert" not in out and '<rect x="1" y="1" width="2" height="2"/>' in out


def test_an_event_attribute_is_dropped():
    out = clean(svg(f'<rect width="2" height="2" {ON}="alert(1)"/>', f' {ON}="alert(2)"'))
    assert_only_listed(out)
    assert "alert" not in out and "onload" not in out.lower() and 'width="2"' in out


def test_foreign_content_is_dropped_with_everything_inside_it():
    out = clean(svg(f'<{FO} width="10" height="10"><div xmlns="http://www.w3.org/1999/xhtml">'
                    f'<{SCR}>alert(1)</{SCR}>hello</div></{FO}><circle cx="5" cy="5" r="2"/>'))
    assert_only_listed(out)
    assert "hello" not in out and "<circle" in out


@pytest.mark.parametrize("element", ["use", "image", "a", "iframe", "object", "embed", "style", "animate", "set"])
def test_elements_off_the_list_are_dropped(element):
    out = clean(svg(f'<{element} href="{EXT}" xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="{EXT}">'
                    f'<rect width="1" height="1"/></{element}><line x1="0" y1="0" x2="1" y2="1"/>'))
    assert_only_listed(out)
    assert f"<{element}" not in out and "<line" in out
    assert "<rect" not in out, "a dropped element takes its content with it"


def test_a_same_drawing_reference_is_kept_and_any_other_dropped():
    out = clean(svg('<defs><marker id="arrow" markerWidth="9"><path d="M0,0 L7,3 Z"/></marker></defs>'
                    '<line x1="0" y1="0" x2="5" y2="5" marker-end="url(#arrow)"/>'
                    f'<line x1="0" y1="0" x2="5" y2="5" marker-end="url({EXT})" fill="{JS}alert(1)"/>'
                    f'<rect width="1" height="1" fill="url(&quot;{EXT}&quot;)" stroke="url(data:x)"/>'))
    assert_only_listed(out)
    assert out.count('marker-end="url(#arrow)"') == 1 and 'id="arrow"' in out
    assert out.count("marker-end") == 1


def test_href_attributes_are_dropped_wherever_they_appear():
    out = clean(svg(f'<rect href="{JS}x" width="1"/><text xmlns:xlink="http://www.w3.org/1999/xlink" '
                    f'xlink:href="#frag" x="1">label</text>'))
    assert_only_listed(out)
    assert '<text x="1">label</text>' in out


def test_style_keeps_only_plain_size_and_font_on_the_root():
    root_style = f' style="max-width:320px;font-family:Helvetica,Arial;background:url({EXT});width:expression(1)"'
    out = clean(svg(f'<rect width="1" height="1" style="fill:url({EXT})"/><{"sty" + "le"}>@import "{EXT}";</style>',
                    root_style))
    assert_only_listed(out)
    assert 'style="max-width:320px;font-family:Helvetica,Arial"' in out
    assert out.count("style=") == 1, "style is allowed on the root only"
    assert clean(svg("", f' style="background:url({EXT})"')) == '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"/>'


def test_a_nested_svg_is_dropped():
    out = clean(svg(f'<svg><{SCR}>x</{SCR}><rect width="1"/></svg><circle r="1"/>'))
    assert_only_listed(out)
    assert out.count("<svg") == 1 and "<circle" in out and "<rect" not in out


def test_text_is_written_back_escaped():
    sample = svg('<text x="1">&lt;b&gt;A &amp; B&lt;/b&gt; "q"</text>', ' aria-label="a &quot;b&quot; &lt;c&gt;"')
    out = clean(sample)
    assert "<b>" not in out and "&lt;b&gt;A &amp; B&lt;/b&gt;" in out
    assert 'aria-label="a &quot;b&quot; &lt;c&gt;"' in out
    assert_only_listed(out)


def test_ids_and_classes_must_be_plain_names():
    out = clean(svg('<rect id="ok-1" class="fd-a fd-b" width="1"/><rect id="a b&quot;" class="x;y" width="2"/>'))
    assert 'id="ok-1"' in out and 'class="fd-a fd-b"' in out
    assert out.count("id=") == 1 and out.count("class=") == 1


@pytest.mark.parametrize("sample", [
    None, 12, "", "   ", "not markup", "<rect/>", "<html><body>x</body></html>",
    '<svg viewBox="0 0 1 1"><rect>',  # not closed
    '<svg viewBox="0 0 1 1"></svg><svg></svg>',  # two roots
    f'<svg viewBox="0 0 1 1"></svg><{SCR}>x</{SCR}>',  # anything after the root
    '<svg viewBox="0 0 1 1" width="1" width="2"></svg>',  # not well-formed
    '<!DOCTYPE svg [<!ENTITY a "aaaa">]><svg viewBox="0 0 1 1"><text>&a;</text></svg>',
    '<svg xmlns="http://www.w3.org/1999/xhtml"></svg>',  # an svg in another namespace
])
def test_anything_but_one_well_formed_svg_root_is_nothing(sample):
    assert clean(sample) == ""
    assert svg_safe.we_safe_svg(sample) == ""


def test_too_long_or_too_deep_is_refused_or_cut():
    assert clean(svg("<rect/>" * 10) + " " * svg_safe.MAX_LENGTH) == ""
    deep = svg("<defs>" * 40 + '<rect width="1"/>' + "</defs>" * 40)
    out = clean(deep)
    assert out.startswith("<svg") and "<rect" not in out
    assert ET.fromstring(out) is not None


def test_the_xml_prolog_and_comments_are_dropped():
    out = clean('<?xml version="1.0"?><!-- note -->' + svg('<!-- inner --><rect width="1"/>'))
    assert out == svg('<rect width="1"/>')


def test_clean_output_is_stable():
    for sample in (svg(f'<rect width="1" {ON}="x"/><text x="1">A &amp; B</text>'), drawings.circulation_schematic_svg({})):
        once = clean(sample)
        assert clean(once) == once


def test_a_drawing_without_the_namespace_stays_without_it():
    assert clean('<svg viewBox="0 0 10 10"></svg>') == '<svg viewBox="0 0 10 10"/>'


# ----------------------------------------------------------------------------- the module's own drawings survive


def _stored_drawings():
    out = []
    for name in MODELS:
        _, stored = designed(name)
        for section in ("charts", "controls"):
            for key, value in (stored.get(section) or {}).items():
                if isinstance(value, str) and "<svg" in value:
                    out.append((f"{name}.{section}.{key}", value))
    return out


def _print_drawings():
    """The print drawings engine/drawings.py makes directly (root style, markerUnits), with every branch."""
    return [
        ("schematic", drawings.circulation_schematic_svg({"nodes": ["Basin", {"label": "Pump", "sub": "A & B <x>"}]})),
        ("schematic-empty", drawings.circulation_schematic_svg({})),
        ("oneline", drawings.electrical_oneline_svg({
            "service": {"label": "240 V", "main_breaker": 60, "sub": "100 A available"}, "feeder": {"label": "F", "sub": "50 ft"},
            "panel": {"label": "Panel"}, "transformer_va": 100,
            "branches": [{"label": "P1", "breaker": 20, "vfd": "VFD 2 HP", "wire": "#12"}, {"label": "L1", "breaker": "15 A 1P"}],
        })),
        ("power", drawings.power_schematic_svg({
            "pumps": [{"tag": "P1", "topology": "vfd_bypass", "sub": "5 HP"}, {"tag": "P2", "topology": "vfd"},
                      {"tag": "P3", "topology": "soft_starter"}, {"tag": "P4"}],
            "transformers": [{"label": "Control transformer 350 VA"}], "spd": True,
        })),
        ("ladder", drawings.control_ladder_svg({"supply": "24 VAC", "rungs": [{"label": "Run", "elements": [
            {"type": "no", "label": "HOA"}, {"type": "nc", "label": "ES"}, {"type": "box", "label": "PLC out"},
            {"type": "light", "label": "Run", "color": "green"}]}, {"elements": [{"type": "coil", "label": "M1"}]}]})),
        ("duty-empty", drawings.pump_duty_svg({})),
    ]


def _standard_details():
    """The Standard Details setup.py seeds (read with ast: setup.py imports frappe at the top)."""
    out = []
    for node in ast.parse((WE / "setup.py").read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id.startswith("_DETAIL_"):
            out.append((node.targets[0].id, ast.literal_eval(node.value)))
    assert len(out) == 3
    return out


def _all_drawings():
    return _stored_drawings() + _print_drawings() + _standard_details()


def test_the_derive_draws_what_the_prints_print():
    keys = {k.split(".", 1)[1] for k, _ in _stored_drawings()}
    assert {"charts.duty_svg", "charts.schematic_svg", "charts.oneline_svg", "controls.power_svg", "controls.ladder_svg"} <= keys
    assert len({k.split(".")[0] for k, _ in _stored_drawings()}) == 4


@pytest.mark.parametrize("label,drawing", [pytest.param(label, d, id=label) for label, d in _all_drawings()])
def test_nothing_the_module_draws_is_dropped(label, drawing):
    out = clean(drawing)
    assert out, label
    assert tree(out) == tree(drawing), label


def test_the_print_context_and_the_template_keep_the_whole_drawing():
    """prints.drawing cleans and suffixes the ids; we_safe_svg in the template cleans again. With the
    suffix taken off, the page gets the drawing the derive stored."""
    for label, drawing in _stored_drawings():
        block = prints.drawing("T", drawing, "k", empty="none")
        if 'id="' in drawing:
            assert re.search(r"-k[0-9a-f]{4}\b", block["svg"]), f"{label}: the ids are suffixed"
        assert tree(re.sub(r"-k[0-9a-f]{4}\b", "", block["svg"])) == tree(drawing), label
        assert tree(svg_safe.we_safe_svg(block["svg"])) == tree(block["svg"]), label


def test_the_allowlist_is_exactly_what_the_module_draws():
    elements, attributes = set(), set()

    def walk(el):
        elements.add(el.tag.split("}")[-1])
        attributes.update(el.attrib)
        for child in el:
            walk(child)

    for _, drawing in _all_drawings():
        walk(ET.fromstring(drawing))
    assert elements == set(svg_safe.ELEMENTS)
    assert attributes - {"style"} == set(svg_safe.ATTRIBUTES)


# ----------------------------------------------------------------------------- every print path goes through it


def test_prints_drawing_refuses_what_does_not_pass():
    hostile = svg(f'<{SCR}>x</{SCR}><rect width="1" {ON}="x"/>')
    block = prints.drawing("T", hostile, "k", empty="none")
    assert_only_listed(block["svg"])
    assert '<rect width="1"/>' in block["svg"]
    assert prints.drawing("T", "<svg><rect>", "k", empty="none")["svg"] is None, "malformed prints the empty sentence"


@pytest.fixture()
def spf(monkeypatch):
    frappe = types.ModuleType("frappe")
    frappe._ = lambda s, *a, **k: s
    frappe.log_error = lambda *a, **k: None
    frappe.get_traceback = lambda *a, **k: ""
    monkeypatch.setitem(sys.modules, "frappe", frappe)
    monkeypatch.delitem(sys.modules, "erpnext_enhancements.water_engineering.setup_print_formats", raising=False)
    return importlib.import_module("erpnext_enhancements.water_engineering.setup_print_formats")


def test_every_format_places_a_drawing_only_through_we_safe_svg(spf):
    formats = spf.water_formats()
    assert set(formats) == set(prints.PRINT_FORMATS)
    for name, source in formats.items():
        for expr in re.findall(r"\{\{-?\s*(.*?)\s*-?\}\}", source):
            if "svg" in expr:
                assert expr in ("svg", "we_safe_svg(d.svg)"), f"{name}: {{{{ {expr} }}}}"
        assert "{%- set svg = we_safe_svg(b.svg) if b.svg else '' -%}" in source, name
    assert "{{ we_safe_svg(d.svg) }}" in formats[prints.PF_PACKET]


def test_a_hostile_block_renders_without_its_payload(spf):
    """The template cleans the drawing itself, so a context that did not (a print built some other way)
    still prints only the listed SVG."""
    hostile = svg(f'<{SCR}>alert(1)</{SCR}><{FO}><p>x</p></{FO}><rect width="1" {ON}="alert(2)"/>')
    sheets = {"drawings": {"title": "Drawings", "blocks": [{"kind": "drawing", "title": "D", "svg": hostile, "empty": "none"}]}}

    def context(_sheets):
        return {"title": "T", "meta": [], "facts": {"project": "", "site": "", "designer": "", "status": ""}, "sheets": sheets,
                "codes": ""}

    env = environment(context)
    env.globals["we_standard_details"] = lambda code=None: [{"title": "D", "detail_key": "d", "svg": hostile}]
    for name in (prints.PF_DRAWINGS, prints.PF_PACKET):
        if name == prints.PF_PACKET:
            sheets.update({k: {"title": k, "blocks": []} for k in prints.PACKET_SHEETS if k != "drawings"})
        page = env.from_string(spf.water_formats()[name]).render(doc=types.SimpleNamespace(
            name="WFD-1", issue_date=None, modified="2026-11-01", docstatus=0, customer="", governing_code=""))
        assert "alert" not in page and f"<{SCR}" not in page and FO not in page and "onload" not in page.lower(), name
        assert '<rect width="1"/>' in page, name


@pytest.fixture()
def packet(monkeypatch):
    frappe = types.ModuleType("frappe")
    frappe._ = lambda s, *a, **k: s
    frappe.log_error = lambda *a, **k: None
    frappe.get_traceback = lambda *a, **k: ""
    frappe.whitelist = lambda *a, **k: (lambda f: f)
    frappe.rows = []
    frappe.get_all = lambda doctype, **k: [dict(r) for r in frappe.rows]
    monkeypatch.setitem(sys.modules, "frappe", frappe)
    monkeypatch.delitem(sys.modules, "erpnext_enhancements.water_engineering.packet", raising=False)
    module = importlib.import_module("erpnext_enhancements.water_engineering.packet")
    module._frappe = frappe
    return module


def test_the_packet_helpers_return_only_clean_drawings(packet):
    hostile = svg(f'<{SCR}>x</{SCR}><rect width="1" {ON}="x"/>')
    doc = FakeDoc(name="WFD-1", results_json=json.dumps({"charts": {"schematic_svg": hostile, "oneline_svg": "<svg><rect>"}}))
    out = packet.we_circulation_schematic(doc)
    assert out.startswith("<svg") and SCR not in out and "onload" not in out
    assert packet.we_electrical_oneline(doc) == ""
    packet._frappe.rows = [{"detail_key": "d", "title": "D", "svg": hostile, "governing_code": ""},
                           {"detail_key": "e", "title": "E", "svg": "not a drawing", "governing_code": ""}]
    details = packet.we_standard_details()
    assert SCR not in details[0]["svg"] and "<rect" in details[0]["svg"] and details[1]["svg"] == ""


def test_no_print_path_reads_a_stored_drawing_without_the_function():
    """Every line in the print code that reads a stored drawing hands it to drawing() or clean_svg()."""
    reads = re.compile(r"""\.get\(["'](?:schematic|oneline|duty|power|ladder)_svg["']\)|\bd\.svg\b|\bb\.svg\b|\[["']svg["']\]""")
    for path in (WE / "prints.py", WE / "packet.py", WE / "setup_print_formats.py"):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#") or not reads.search(line):
                continue
            assert re.search(r"\bdrawing\(|clean_svg\(|we_safe_svg\(", line), f"{path.name}:{n}: {line.strip()}"


def test_the_hook_registers_we_safe_svg():
    hooks = (APP / "hooks.py").read_text(encoding="utf-8")
    assert '"erpnext_enhancements.water_engineering.svg_safe.we_safe_svg"' in hooks


def test_the_packet_preview_is_a_sandboxed_frame():
    js = (WE / "doctype" / "water_feature_design" / "water_feature_design.js").read_text(encoding="utf-8")
    code = re.sub(r"//[^\n]*", "", js)
    assert "document.write" not in code and 'window.open("",' not in code
    assert 'frame.setAttribute("sandbox", "")' in code and "frame.srcdoc = r.message" in code
    assert "allow-scripts" not in code and "allow-same-origin" not in code
    assert "/printview?" in code and "trigger_print" in code, "Print opens the format in frappe's print view"


def test_the_engine_stays_frappe_free():
    source = (WE / "svg_safe.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*(import|from)\s+frappe", source, re.M)
    assert not re.search(r"^\s*(import|from)\s+(lxml|bs4|defusedxml|bleach)", source, re.M)
