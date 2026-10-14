"""Bench-free tests: the guided page's inputs are the form's inputs (v1.685.0).

Nik, 2026-10-14: "We shouldn't have lost any fidelity or information on any of the UI/UX changes,
and the inputs should be the same." The guided page and the side panel draw a record's fields in a
``frappe.ui.FieldGroup`` with no ``frm``, so whatever the Desk form's scripts do through ``frm``
never reaches them: ``frm.set_query`` filters, ``set_df_property`` labels and requirements. The
config carries each one as data instead (``form_kit/config``: ``queries``, ``query_waivers``,
``label_from``, ``won``). This suite reads the form scripts themselves and fails when one sets
something the config does not mirror:

* every ``set_query("<field>"`` / ``set_query("<field>", "<table>"`` and every
  ``fields_dict.<field>.get_query =`` in ERPNext's ``opportunity.js`` (at version-16, trimmed into
  ``fixtures/form_kit/erpnext_v16_opportunity_scripts.json``) and in every script this app runs on
  the Opportunity form (``hooks.py`` ``doctype_js["Opportunity"]``, and any other script that
  registers on Opportunity), is mirrored in ``queries`` or waived in ``query_waivers`` with a
  reason; and nothing is mirrored or waived that no script sets;
* a ``set_query`` whose field is a variable must be resolved in ``DYNAMIC`` below;
* ``label_from`` is exactly the scripts' ``set_df_property("<field>", "label", frm.doc.<other>)``;
* the fields the form makes required on Closed Won are the config's ``won`` fields, which
  ``steps.won_conditions`` turns into the flow's ``mandatory_depends_on``.

It also runs ``editor.js``'s Ctrl/Cmd+S binding (``ge_bind_save_key``) under node.

Run: python -m unittest erpnext_enhancements.tests.test_form_kit_inputs -v
"""

import ast
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.form_kit.config import CONFIGS, check, query_fieldnames

APP = REPO_ROOT / "erpnext_enhancements"
JS = APP / "public" / "js"
HOOKS = APP / "hooks.py"
ERPNEXT_SCRIPT = APP / "tests" / "fixtures" / "form_kit" / "erpnext_v16_opportunity_scripts.json"
MIGRATED = JS / "crm_enhancements" / "opportunity_migrated_scripts.js"
UNIFIED = JS / "global_enhancements" / "unified_tab_controller.js"

DOCTYPE = "Opportunity"
CONFIG = CONFIGS[DOCTYPE]
ERPNEXT_PATH = "erpnext/crm/doctype/opportunity/opportunity.js"

SET_QUERY = re.compile(
	r"""\bset_query\(\s*(?:(["'])(?P<field>\w+)\1|(?P<var>[A-Za-z_$][\w$.]*))\s*(?:,\s*(["'])(?P<table>\w+)\4)?"""
)
GET_QUERY = re.compile(
	r"""(?:fields_dict(?:\.(?P<a>\w+)|\[\s*(["'])(?P<b>\w+)\2\s*\])|get_field\(\s*(["'])(?P<c>\w+)\4\s*\))\.get_query\s*="""
)
LABEL = re.compile(
	r"""set_df_property\(\s*(["'])(?P<field>\w+)\1\s*,\s*(["'])label\3\s*,\s*frm\.doc\.(?P<from>\w+)\s*\)"""
)

#: ``set_query`` calls whose field is a variable: (file, expression) -> the Opportunity fields it can
#: name. unified_tab_controller.js primary_address_link_field() returns customer_primary_address,
#: supplier_primary_address or primary_address; Opportunity has only the last
#: (test_the_dynamic_address_field_is_still_the_three).
DYNAMIC = {
	("public/js/global_enhancements/unified_tab_controller.js", "address_link_field"): ["primary_address"],
}


def _code(text):
	"""Comments out (line numbers kept): a commented-out set_query is not one the form runs."""
	text = re.sub(r"/\*[\s\S]*?\*/", lambda m: "\n" * m.group(0).count("\n"), text)
	return "\n".join(re.sub(r"(^|[^:\"'])//.*$", r"\1", line) for line in text.splitlines())


def _doctype_js():
	tree = ast.parse(HOOKS.read_text(encoding="utf-8"))
	for node in tree.body:
		if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "doctype_js" for t in node.targets):
			return ast.literal_eval(node.value)
	raise AssertionError("hooks.py has no doctype_js")


def _registers_on_opportunity(code):
	if re.search(r"""form\.on\(\s*["'](Opportunity|\*)["']""", code):
		return True
	# A script that registers on a doctype held in a variable (a loop over several, or every one):
	# it cannot be shown not to run on Opportunity, so it is read too.
	return bool(re.search(r"""form\.on\(\s*[A-Za-z_]\w*\s*,""", code))


def _app_scripts():
	"""``{relative path: code}``: doctype_js["Opportunity"] and every app script registering on it."""
	listed = _doctype_js().get(DOCTYPE) or []
	listed = [listed] if isinstance(listed, str) else list(listed)
	out = {}
	for rel in listed:
		path = APP / rel
		out[rel] = _code(path.read_text(encoding="utf-8"))
	for path in sorted(APP.rglob("*.js")):
		rel = path.relative_to(APP).as_posix()
		if rel in out or "/node_modules/" in rel or rel.startswith("public/dist") or "/form_kit/" in rel:
			continue
		if rel.endswith((".bundle.js", ".min.js")) or "/lib/" in rel:
			continue
		code = _code(path.read_text(encoding="utf-8", errors="replace"))
		if _registers_on_opportunity(code):
			out[rel] = code
	return out


def _erpnext_lines():
	data = json.loads(ERPNEXT_SCRIPT.read_text(encoding="utf-8"))
	return [(f"{ERPNEXT_PATH}:{row['line']}", row["text"]) for row in data["lines"]]


def _scan():
	"""``({key: [where]}, [unresolved dynamic], {field: from})`` over every script."""
	found, unresolved, labels = {}, [], {}
	sources = [(where, text) for where, text in _erpnext_lines()]
	for rel, code in _app_scripts().items():
		for number, line in enumerate(code.splitlines(), 1):
			sources.append((f"{rel}:{number}", line))
	for where, text in sources:
		rel = where.rsplit(":", 1)[0]
		for match in SET_QUERY.finditer(text):
			field = match.group("field")
			if field is None:
				expression = match.group("var")
				names = DYNAMIC.get((rel, expression))
				if names is None:
					unresolved.append(f"{where}: set_query({expression}")
					continue
			else:
				names = [field]
			for name in names:
				key = f"{match.group('table')}.{name}" if match.group("table") else name
				found.setdefault(key, []).append(where)
		for match in GET_QUERY.finditer(text):
			name = match.group("a") or match.group("b") or match.group("c")
			found.setdefault(name, []).append(where)
		for match in LABEL.finditer(text):
			labels[match.group("field")] = match.group("from")
	return found, unresolved, labels


class TestQueriesMirrorTheForm(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.found, cls.unresolved, cls.labels = _scan()
		cls.queries = CONFIG.get("queries") or {}
		cls.waivers = CONFIG.get("query_waivers") or {}

	def test_the_scan_sees_the_scripts(self):
		"""Guards the scanner itself: a regex that stopped matching would pass everything."""
		self.assertIn("public/js/global_enhancements/unified_tab_controller.js", _app_scripts())
		self.assertIn("public/js/crm_enhancements/opportunity_migrated_scripts.js", _app_scripts())
		self.assertIn("primary_contact", self.found)
		self.assertIn("party_name", self.found)
		self.assertIn("items.item_code", self.found)
		self.assertTrue(any("unified_tab_controller.js" in where for where in self.found["primary_address"]))

	def test_every_set_query_is_mirrored_or_waived(self):
		missing = {
			key: where
			for key, where in self.found.items()
			if key not in self.queries and key not in self.waivers
		}
		self.assertEqual(
			missing,
			{},
			"A form script sets a query the guided page does not apply. Mirror it in the config's "
			"`queries` (form_kit/config/opportunity.py), or waive it in `query_waivers` with a reason.",
		)

	def test_nothing_is_mirrored_that_no_script_sets(self):
		stale = sorted((set(self.queries) | set(self.waivers)) - set(self.found))
		self.assertEqual(stale, [], "a mirrored or waived query that no form script sets any more")

	def test_a_set_query_on_a_variable_is_resolved(self):
		self.assertEqual(self.unresolved, [], "add the expression to DYNAMIC with the fields it can name")

	def test_the_dynamic_address_field_is_still_the_three(self):
		code = _code(UNIFIED.read_text(encoding="utf-8"))
		body = code[code.index("primary_address_link_field: function") :]
		body = body[: body.index("\n\t},")]
		self.assertEqual(
			re.findall(r'return "(\w+)";', body),
			["customer_primary_address", "supplier_primary_address", "primary_address"],
		)

	def test_the_party_has_a_query_for_every_kind_it_may_be(self):
		kinds = set(self.queries["opportunity_from"]["filters"]["name"][1])
		self.assertEqual(kinds, {"Customer", "Lead", "Prospect"})
		self.assertEqual(set(self.queries["party_name"]["cases"]), kinds)
		self.assertEqual(self.queries["party_name"]["by"], "opportunity_from")

	def test_the_contact_and_address_pickers_search_the_directory(self):
		for fieldname in ("primary_contact", "primary_address", "customer_address"):
			self.assertEqual(self.queries[fieldname].get("links"), "directory", fieldname)

	def test_every_waiver_has_a_reason(self):
		for key, reason in self.waivers.items():
			self.assertTrue(str(reason).strip(), key)

	def test_labels_are_the_scripts_labels(self):
		self.assertEqual({k: v["field"] for k, v in (CONFIG.get("label_from") or {}).items()}, self.labels)


class TestTheWonFieldsAreRequired(unittest.TestCase):
	def test_the_won_fields_are_what_the_form_makes_required(self):
		code = _code(MIGRATED.read_text(encoding="utf-8"))
		body = code[code.index("function set_rank_fields_mandatory(frm) {") :]
		body = body[: body.index("\n}\n")]
		fields = re.findall(r'set_df_property\("(\w+)", "reqd", reqd\)', body)
		status = re.search(r'frm\.doc\.status === "([^"]+)"', body).group(1)
		self.assertEqual(fields, CONFIG["won"]["fields"])
		self.assertEqual(status, CONFIG["won"]["status"])


class TestTheSchema(unittest.TestCase):
	def _problems(self, **extra):
		config = dict(CONFIG, **extra)
		return check(config)

	def test_the_opportunity_config_is_sound(self):
		self.assertEqual(check(CONFIG), [])

	def test_a_query_without_a_source_is_refused(self):
		problems = self._problems(queries={"party_name": {"filters": {"disabled": 0}}})
		self.assertIn("queries.party_name needs a source", problems)

	def test_a_malformed_query_is_refused(self):
		problems = self._problems(
			queries={
				"a": {"source": "x", "links": "everyone"},
				"b": {"source": "x", "by": "opportunity_from"},
				"c": {"source": "x", "nonsense": 1},
				"d": {"source": "x", "query": "no_dots"},
				"e": {"source": "x", "by": "opportunity_from", "cases": {"Lead": {"filters": "x"}}},
			}
		)
		joined = "\n".join(problems)
		self.assertIn("queries.a: links must be one of", joined)
		self.assertIn("queries.b: 'by' needs a field", joined)
		self.assertIn("queries.c: unknown keys ['nonsense']", joined)
		self.assertIn("queries.d: query must be a dotted path", joined)
		self.assertIn("queries.e: case Lead: filters must be a dict or a list", joined)

	def test_a_waiver_needs_a_reason_and_cannot_also_be_mirrored(self):
		problems = self._problems(query_waivers={"party_name": "", "x": "a reason"})
		self.assertIn("query_waivers.party_name has no reason", problems)
		self.assertIn("party_name is both mirrored in queries and waived", problems)

	def test_label_from_needs_a_field_and_a_source(self):
		problems = self._problems(label_from={"party_name": {"field": "opportunity_from"}})
		self.assertIn("label_from.party_name needs a field and a source", problems)

	def test_the_fields_a_query_reads(self):
		self.assertEqual(query_fieldnames(CONFIG["queries"]["party_name"]), ["opportunity_from", "company"])
		self.assertEqual(query_fieldnames({"filters": [["Item", "x", "=", {"field": "y"}]]}), ["y"])
		self.assertEqual(query_fieldnames(CONFIG["queries"]["primary_contact"]), [])


class TestTheEditorAppliesThem(unittest.TestCase):
	"""The one editor both shells run (the guided page and the side panel) is where each lands."""

	@classmethod
	def setUpClass(cls):
		cls.editor = _code((JS / "form_kit" / "ui" / "editor.js").read_text(encoding="utf-8"))
		cls.panel = _code((JS / "form_kit" / "ui" / "side_panel.js").read_text(encoding="utf-8"))

	def test_every_link_takes_its_query_from_the_config(self):
		self.assertIn("const query = ((this.flow && this.flow.queries) || {})[field.fieldname];", self.editor)
		self.assertIn("df.get_query = () => kit.resolve_query(query, this.query_ctx()) || {};", self.editor)
		self.assertIn("kit.link_query(field, ctx().party, ctx().directory)", self.editor)

	def test_the_field_group_applies_the_docfields_conditions(self):
		"""v16's Layout.set_dependant_property skips a plain FieldGroup (no frm, not a dialog)."""
		self.assertIn("set_dependant_property(condition, fieldname, property) {", self.editor)
		self.assertIn("editor.apply_condition(this, condition, fieldname, property);", self.editor)
		body = self.editor[self.editor.index("apply_condition(fg, condition, fieldname, property) {") :]
		body = body[: body.index("\n\t}\n")]
		self.assertIn("const value = base || fg.evaluate_depends_on_value(condition) ? 1 : 0;", body)

	def test_the_label_follows_its_field(self):
		self.assertIn("this.relabel(fieldname);", self.editor)
		self.assertIn(
			"if (label_from[field.fieldname]) df.label = kit.label_for(field, label_from, this.values);",
			self.editor,
		)

	def test_ctrl_s_is_bound_once_through_the_page(self):
		self.assertIn("this.bind_save_key();", self.editor)
		self.assertIn("ge_bind_save_key(this.save_key_host(), this);", self.editor)
		self.assertNotIn("keydown", self.editor[self.editor.index("save_key_host() {") :][:1500])
		self.assertIn('if (this.spec && this.spec.kind === "move") {', self.panel)
		self.assertIn("return super.save_now();", self.panel)


SAVE_KEY_RUNNER = r"""
const [, , root] = process.argv;
const { ge_bind_save_key } = await import(`file://${root}/form_kit/ui/editor.js`);
const calls = [];
const make = (name, showing, result) => ({
	name,
	shown: showing,
	showing() { return this.shown; },
	save_now() { calls.push(name); return result; },
});
const out = {};
const host = { save_action: () => calls.push("before") };
const page = make("page", true, true);
out.first = ge_bind_save_key(host, page);
out.again = ge_bind_save_key(host, page);
host.save_action();
out.showing = calls.splice(0);
page.shown = false;
host.save_action();
out.hidden = calls.splice(0);
const panel = make("panel", true, false);
ge_bind_save_key(host, panel);
host.save_action();
out.declined = calls.splice(0);
out.count = host.fk_save_editors.length;
page.shown = true;
const over = make("over", true, true);
ge_bind_save_key(host, over);
host.save_action();
out.both = calls.splice(0);
const bare = {};
ge_bind_save_key(bare, make("bare", false, true));
out.bare_quiet = (bare.save_action(), calls.splice(0));
out.no_host = ge_bind_save_key(null, page);
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(shutil.which("node"), "node is not on PATH")
class TestCtrlSUnderNode(unittest.TestCase):
	"""Ctrl/Cmd+S: frappe's shortcut ends in the current page's save_action (desk.js
	trigger_primary_action). Each editor adds itself once; a page shown again never binds twice."""

	@classmethod
	def setUpClass(cls):
		with tempfile.TemporaryDirectory() as tmp:
			folder = Path(tmp)
			(folder / "package.json").write_text('{"type": "module"}', encoding="utf-8")
			shutil.copytree(JS / "form_kit", folder / "form_kit")
			shutil.copytree(JS / "info_blocks", folder / "info_blocks")
			(folder / "runner.mjs").write_text(SAVE_KEY_RUNNER, encoding="utf-8")
			proc = subprocess.run(
				[shutil.which("node"), str(folder / "runner.mjs"), folder.as_posix()],
				capture_output=True,
				text=True,
				encoding="utf-8",
				timeout=60,
			)
		if proc.returncode != 0:
			raise AssertionError(f"node failed:\n{proc.stderr}")
		cls.out = json.loads(proc.stdout)

	def test_binding_is_once_per_editor(self):
		self.assertTrue(self.out["first"])
		self.assertFalse(self.out["again"])
		self.assertEqual(self.out["count"], 2)
		self.assertFalse(self.out["no_host"])

	def test_the_showing_editor_saves_and_nothing_else_runs(self):
		self.assertEqual(self.out["showing"], ["page"])

	def test_a_hidden_editor_leaves_the_key_to_what_the_page_had(self):
		self.assertEqual(self.out["hidden"], ["before"])

	def test_an_editor_with_nothing_to_save_passes_it_on(self):
		self.assertEqual(self.out["declined"], ["panel", "before"])

	def test_every_editor_on_screen_saves(self):
		"""A side panel open over a page with its own editor: Ctrl+S saves what is on screen."""
		self.assertEqual(self.out["both"], ["page", "panel", "over"])

	def test_a_page_without_its_own_save_action_does_nothing_when_hidden(self):
		self.assertEqual(self.out["bare_quiet"], [])


if __name__ == "__main__":
	unittest.main()
