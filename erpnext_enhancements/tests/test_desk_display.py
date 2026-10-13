"""Bench-free tests for the Desk phone display (form kit P2-B), run under node.

``public/js/normalize/desk_display.js`` decides, in pure functions, what a read surface shows
for a phone and what the model receives when one is typed; a thin layer wraps Frappe's Data
formatter and ``ControlData.format_for_input`` / ``parse`` with them. Both halves run here
under node: the pure functions directly, and the wiring against a fake ``frappe`` with just
the three surfaces it patches. No DOM is needed.

The module and its import are copied to a temp dir beside a ``package.json`` that says
``"type": "module"``: the repo's own package.json does not, and node 20 (CI) will not load ES
module syntax from a ``.js`` file without it. Skips cleanly when ``node`` is not on PATH.

Also pinned: the global bundle imports the module, and boot honours the kill switch (the
server half is in ``test_normalize_hook``).

Run: python -m unittest erpnext_enhancements.tests.test_desk_display -v
"""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.utils import normalize as n
from erpnext_enhancements.utils.normalize_fields import FIELDS

JS_DIR = REPO_ROOT / "erpnext_enhancements" / "public" / "js"
DESK_DISPLAY = JS_DIR / "normalize" / "desk_display.js"
NORMALIZE_JS = JS_DIR / "normalize" / "normalize.js"
BUNDLE = JS_DIR / "erpnext_enhancements.bundle.js"

REGISTRY = {"fields": {"Contact": {"mobile_no": "phone"}, "Contact Phone": {"phone": "phone"}}}

RUNNER = r"""
import { pathToFileURL } from "node:url";
const [moduleFile, registryJson] = process.argv.slice(2);
globalThis.window = globalThis;
const d = await import(pathToFileURL(moduleFile).href);
const registry = JSON.parse(registryJson);
const out = {};
const contact = { parent: "Contact", fieldname: "mobile_no", fieldtype: "Data" };
const other = { parent: "Contact", fieldname: "first_name", fieldtype: "Data" };

out.display = {
	registered_stored: d.display_value(contact, "+18015550100,123", registry),
	registered_raw: d.display_value(contact, "801-555-0100", registry),
	registered_not_ok: d.display_value(contact, "65-688-000-88", registry),
	registered_international: d.display_value(contact, "+442079460958", registry),
	registered_empty: d.display_value(contact, "", registry),
	parent_from_doc: d.display_value({ fieldname: "mobile_no", fieldtype: "Data" }, "8015550100", registry, "Contact"),
	grid_row: d.display_value({ parent: "Contact Phone", fieldname: "phone", fieldtype: "Data" }, "+18015550100", registry),
	fallback_bare_df: d.display_value({ fieldname: "phone", label: "Phone" }, "+18015550100", registry),
	fallback_no_df: d.display_value(undefined, "+18015550100,7", registry),
	fallback_typed_not_stored: d.display_value({ fieldname: "x", fieldtype: "Data" }, "801-555-0100", registry),
	fallback_international: d.display_value({ fieldname: "x", fieldtype: "Data" }, "+442079460958", registry),
	fallback_link: d.display_value({ fieldname: "x", fieldtype: "Link" }, "+18015550100", registry),
	fallback_select: d.display_value({ fieldname: "x", fieldtype: "Select" }, "+18015550100", registry),
	non_phone_field: d.display_value(other, "801-555-0100", registry),
	url_option: d.display_value({ ...contact, options: "URL" }, "+18015550100", registry),
	number_value: d.display_value(contact, 8015550100, registry),
	no_registry: d.display_value(contact, "801-555-0100", null),
};
out.parse = {
	raw: d.parse_value("801-555-0100"),
	ext: d.parse_value("(801) 555-0100 x123"),
	display_back: d.parse_value("(801) 555-0100 x123"),
	not_ok: d.parse_value("555-0100"),
	letters: d.parse_value("call Brian"),
	cleared: d.parse_value(""),
	blank: d.parse_value("   "),
	nil: d.parse_value(null),
	undef: d.parse_value(undefined) === undefined,
};
out.input = {
	stored: d.input_value("+18015550100,123"),
	nil: d.input_value(null),
	not_ok: d.input_value("555-0100"),
	typing_same: d.input_value("+18015550100", "801555-0100"),
	typing_other: d.input_value("+18015550100", "801-555-0199"),
	not_focused: d.input_value("+18015550100", undefined),
};
out.escape = d.escape_html(`<a href="x">'&`);
out.stored_shape = ["+18015550100", "+18015550100,1", "+18015550100,1234567", "+11015550100", "+4420", "8015550100"].map((v) => d.STORED_NANP.test(v));
out.is_phone_control = {
	data: d.is_phone_control({ df: contact }, registry),
	filter: d.is_phone_control({ df: { ...contact, is_filter: 1 } }, registry),
	link: d.is_phone_control({ df: { ...contact, fieldtype: "Link" } }, registry),
	phone_type: d.is_phone_control({ df: { ...contact, fieldtype: "Phone" } }, registry),
	read_only: d.is_phone_control({ df: { ...contact, fieldtype: "Read Only" } }, registry),
	grid_doctype: d.is_phone_control({ df: { fieldname: "phone", fieldtype: "Data" }, doctype: "Contact Phone" }, registry),
	other_field: d.is_phone_control({ df: other }, registry),
};

// The wiring, against a fake frappe with the three surfaces it patches.
function fake(withRegistry) {
	class ControlData {
		format_for_input(v) { return v == null ? "" : v; }
		parse(v) { return v; }
	}
	const f = {
		boot: withRegistry ? { sf_normalize: registry } : {},
		form: { formatters: { Data: (value, df) => (value == null ? "" : value) } },
		ui: { form: { ControlData } },
		provide(path) {
			let o = globalThis;
			for (const part of path.split(".")) o = o[part] = o[part] || {};
		},
	};
	return f;
}
const off = fake(false);
const offData = off.form.formatters.Data;
out.kill_switch = { installed: d.install(off), formatter_untouched: off.form.formatters.Data === offData };

const on = fake(true);
out.installed = d.install(on);
out.installed_twice_is_harmless = d.install(on);
const fmt = on.form.formatters.Data;
out.formatter = {
	registered: fmt("+18015550100,123", contact),
	other_field: fmt("801-555-0100", other),
	bare_stored: fmt("+18015550100", { fieldname: "x" }),
	nil: fmt(null, contact),
	html_passthrough: fmt("<b>x</b>", other),
};
const control = new on.ui.form.ControlData();
control.df = contact;
const plain = new on.ui.form.ControlData();
plain.df = other;
out.control = {
	shows: control.format_for_input("+18015550100"),
	stores: control.parse("801.555.0100"),
	keeps_not_ok: control.parse("555-0100"),
	clears: control.parse(""),
	other_shows: plain.format_for_input("+18015550100"),
	other_parse: plain.parse("801.555.0100"),
};
out.published_phone_display = typeof globalThis.erpnext_enhancements?.normalize?.phone_display;

// Fail open: a registry whose lookups throw must leave the original behaviour.
const realWarn = console.warn;
let warnings = 0;
console.warn = () => { warnings += 1; };
const poisoned = fake(false);
poisoned.boot.sf_normalize = { fields: new Proxy({}, { has() { throw new Error("boom"); }, get() { throw new Error("boom"); }, getOwnPropertyDescriptor() { throw new Error("boom"); } }) };
d.install(poisoned);
const pc = new poisoned.ui.form.ControlData();
pc.df = contact;
out.fail_open = {
	formatter: poisoned.form.formatters.Data("801-555-0100", contact),
	format: pc.format_for_input("801-555-0100"),
	parse: pc.parse("801-555-0100"),
};
console.warn = realWarn;
out.fail_open.warned_at_most_once = warnings <= 1;
process.stdout.write(JSON.stringify(out));
"""


class TestDeskDisplayUnderNode(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        node = shutil.which("node")
        if not node:
            raise unittest.SkipTest("node is not on PATH")
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "package.json").write_text('{"type": "module"}', encoding="utf-8")
            shutil.copy(NORMALIZE_JS, folder / "normalize.js")
            shutil.copy(DESK_DISPLAY, folder / "desk_display.js")
            (folder / "runner.mjs").write_text(RUNNER, encoding="utf-8")
            proc = subprocess.run(
                [node, str(folder / "runner.mjs"), str(folder / "desk_display.js"), json.dumps(REGISTRY)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=60,
            )
        if proc.returncode != 0:
            raise AssertionError(f"node failed:\n{proc.stderr}")
        cls.out = json.loads(proc.stdout)

    def test_a_registered_phone_shows_the_way_people_read_it(self):
        d = self.out["display"]
        self.assertEqual(d["registered_stored"], "(801) 555-0100 x123")
        self.assertEqual(d["registered_raw"], "(801) 555-0100")
        self.assertEqual(d["parent_from_doc"], "(801) 555-0100")
        self.assertEqual(d["grid_row"], "(801) 555-0100")
        # Already shown as it reads: nothing to change, the original formatter runs.
        self.assertIsNone(d["registered_international"])
        self.assertIsNone(d["registered_not_ok"])
        self.assertIsNone(d["registered_empty"])
        self.assertIsNone(d["number_value"])
        self.assertIsNone(d["no_registry"])

    def test_the_stored_shape_is_shown_even_without_a_registry_match(self):
        d = self.out["display"]
        self.assertEqual(d["fallback_bare_df"], "(801) 555-0100")
        self.assertEqual(d["fallback_no_df"], "(801) 555-0100 x7")
        self.assertIsNone(d["fallback_typed_not_stored"], "only the exact stored shape")
        self.assertIsNone(d["fallback_international"])
        self.assertIsNone(d["fallback_link"])
        self.assertIsNone(d["fallback_select"])

    def test_everything_else_passes_through(self):
        d = self.out["display"]
        self.assertIsNone(d["non_phone_field"])
        self.assertIsNone(d["url_option"])

    def test_what_is_typed_is_normalized_before_the_model(self):
        p = self.out["parse"]
        self.assertEqual(p["raw"], "+18015550100")
        self.assertEqual(p["ext"], "+18015550100,123")
        self.assertEqual(p["display_back"], "+18015550100,123")
        self.assertEqual(p["not_ok"], "555-0100", "a value that cannot be normalized is kept as typed")
        self.assertEqual(p["letters"], "call Brian")
        self.assertEqual(p["cleared"], "", "clearing the field clears it")
        self.assertEqual(p["blank"], "")
        self.assertIsNone(p["nil"])
        self.assertTrue(p["undef"])

    def test_the_input_shows_the_display_form_but_keeps_what_is_being_typed(self):
        i = self.out["input"]
        self.assertEqual(i["stored"], "(801) 555-0100 x123")
        self.assertEqual(i["nil"], "")
        self.assertEqual(i["not_ok"], "555-0100")
        self.assertEqual(i["typing_same"], "801555-0100")
        self.assertEqual(i["typing_other"], "(801) 555-0100")
        self.assertEqual(i["not_focused"], "(801) 555-0100")

    def test_the_stored_shape_and_escaping(self):
        self.assertEqual(self.out["stored_shape"], [True, True, False, False, False, False])
        self.assertEqual(self.out["escape"], "&lt;a href=&quot;x&quot;&gt;&#39;&amp;")

    def test_only_a_registered_data_control_is_reformatted(self):
        c = self.out["is_phone_control"]
        self.assertTrue(c["data"])
        self.assertTrue(c["read_only"])
        self.assertTrue(c["grid_doctype"])
        self.assertFalse(c["filter"], "a list filter stays what was typed")
        self.assertFalse(c["link"])
        self.assertFalse(c["phone_type"])
        self.assertFalse(c["other_field"])

    def test_the_kill_switch_installs_nothing(self):
        self.assertEqual(self.out["kill_switch"], {"installed": False, "formatter_untouched": True})

    def test_the_wiring(self):
        self.assertTrue(self.out["installed"])
        self.assertTrue(self.out["installed_twice_is_harmless"])
        f = self.out["formatter"]
        self.assertEqual(f["registered"], "(801) 555-0100 x123")
        self.assertEqual(f["other_field"], "801-555-0100")
        self.assertEqual(f["bare_stored"], "(801) 555-0100")
        self.assertEqual(f["nil"], "")
        self.assertEqual(f["html_passthrough"], "<b>x</b>", "everything else reaches the original unchanged")
        c = self.out["control"]
        self.assertEqual(c["shows"], "(801) 555-0100")
        self.assertEqual(c["stores"], "+18015550100")
        self.assertEqual(c["keeps_not_ok"], "555-0100")
        self.assertEqual(c["clears"], "")
        self.assertEqual(c["other_shows"], "+18015550100", "an unregistered field is untouched")
        self.assertEqual(c["other_parse"], "801.555.0100")
        self.assertEqual(self.out["published_phone_display"], "function")

    def test_it_fails_open(self):
        f = self.out["fail_open"]
        self.assertEqual(f["formatter"], "801-555-0100")
        self.assertEqual(f["format"], "801-555-0100")
        self.assertEqual(f["parse"], "801-555-0100")
        self.assertTrue(f["warned_at_most_once"])


class TestPythonAgrees(unittest.TestCase):
    def test_the_stored_shape_matches_what_python_stores(self):
        """desk_display's STORED_NANP and value_display's must accept every NANP value phone() stores."""
        import re

        from erpnext_enhancements.utils import value_display

        source = DESK_DISPLAY.read_text(encoding="utf-8")
        js_pattern = re.search(r"export const STORED_NANP = /(.+)/;", source).group(1)
        self.assertEqual(js_pattern.strip("^$"), value_display.STORED_NANP.pattern.replace("?:", ""))
        for raw in ("801-555-0100", "(801) 555-0100 x123", "1-801-555-0100 ext 999999"):
            stored = n.phone(raw).value
            with self.subTest(raw=raw):
                self.assertRegex(stored, "^" + js_pattern.strip("^$") + "$")


class TestShipping(unittest.TestCase):
    def test_the_global_bundle_imports_it(self):
        self.assertIn('import "./normalize/desk_display.js";', BUNDLE.read_text(encoding="utf-8"))

    def test_it_imports_the_twin_and_nothing_else(self):
        imports = [
            line for line in DESK_DISPLAY.read_text(encoding="utf-8").splitlines() if line.startswith("import ")
        ]
        self.assertEqual(imports, ['import { phone, phone_display } from "./normalize.js";'])

    def test_boot_reads_the_kill_switch(self):
        source = (REPO_ROOT / "erpnext_enhancements" / "utils" / "normalize_hook.py").read_text(encoding="utf-8")
        self.assertIn("if is_disabled():\n\t\t\treturn None", source)

    def test_every_registered_phone_doctype_reaches_the_desk(self):
        """desk_registry is built from FIELDS, so a newly registered phone is shown with no
        second list to keep in step."""
        phones = {dt for dt, fields in FIELDS.items() if "phone" in fields.values()}
        self.assertGreater(len(phones), 20)


if __name__ == "__main__":
    unittest.main()
