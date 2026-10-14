"""Bench-free tests: the guided page's inputs are the form's inputs (v1.685.0).

Nik, 2026-10-14: "We shouldn't have lost any fidelity or information on any of the UI/UX changes,
and the inputs should be the same." The guided page and the side panel draw a record's fields in a
``frappe.ui.FieldGroup`` with no ``frm``, so whatever the Desk form's scripts do through ``frm``
never reaches them: ``frm.set_query`` filters, ``set_df_property`` labels and requirements. The
config carries each one as data instead (``form_kit/config``: ``queries``, ``query_waivers``,
``label_from``, ``won``). This suite reads the form scripts themselves, for every guided DocType it
audits (``AUDITED``: Opportunity and Item), and fails when one sets something the config does not
mirror:

* every ``set_query("<field>"`` / ``set_query("<field>", "<table>"``, every
  ``frm.fields_dict.<field>.get_query =`` and every ``frm.fields_dict.<table>.grid.get_field(
  "<field>").get_query =`` in ERPNext's form script (at version-16, trimmed into
  ``fixtures/form_kit/erpnext_v16_<doctype>_scripts.json``) and in every script this app runs on the
  form (``hooks.py`` ``doctype_js``, and any other script that registers on the DocType or on a
  DocType held in a variable), is mirrored in ``queries`` or waived in ``query_waivers`` with a
  reason; and nothing is mirrored or waived that no script sets;
* a ``set_query`` whose field is a variable must be resolved in ``DYNAMIC`` below;
* ``label_from`` is exactly the scripts' ``set_df_property("<field>", "label", frm.doc.<other>)``;
* the fields the form makes required on Closed Won are Opportunity's ``won`` fields, which
  ``steps.won_conditions`` turns into the flow's ``mandatory_depends_on``.

Under node it runs ``editor.js`` itself: the Ctrl/Cmd+S binding (``ge_bind_save_key``), and
``apply_condition`` with a fake FieldGroup, including every ``mandatory_depends_on`` and
``read_only_depends_on`` of the Item meta held to what the Desk form would set on 128 records.

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
FIXTURES = APP / "tests" / "fixtures" / "form_kit"
MIGRATED = JS / "crm_enhancements" / "opportunity_migrated_scripts.js"
UNIFIED = JS / "global_enhancements" / "unified_tab_controller.js"

#: The guided DocTypes whose form scripts are audited: ERPNext's own script (a trimmed fixture) per
#: DocType. A DocType added to CONFIGS must be added here (test_every_guided_doctype_is_audited).
AUDITED = {
	"Opportunity": {
		"fixture": FIXTURES / "erpnext_v16_opportunity_scripts.json",
		"erpnext": "erpnext/crm/doctype/opportunity/opportunity.js",
	},
	"Item": {
		"fixture": FIXTURES / "erpnext_v16_item_scripts.json",
		"erpnext": "erpnext/stock/doctype/item/item.js",
	},
}
OPPORTUNITY = CONFIGS["Opportunity"]

SET_QUERY = re.compile(
	r"""\bset_query\(\s*(?:(["'])(?P<field>\w+)\1|(?P<var>[A-Za-z_$][\w$.]*))\s*(?:,\s*(["'])(?P<table>\w+)\4)?"""
)
# A form field's own query (frm.fields_dict.<f> / frm.fields_dict["<f>"] / frm.get_field("<f>")). A
# dialog's fields_dict is not the form's, and a grid column is GRID_QUERY's.
GET_QUERY = re.compile(
	r"""\bfrm\.(?:fields_dict(?:\.(?P<a>\w+)|\[\s*(["'])(?P<b>\w+)\2\s*\])|get_field\(\s*(["'])(?P<c>\w+)\4\s*\))\.get_query\s*="""
)
GRID_QUERY = re.compile(
	r"""\bfrm\.fields_dict(?:\.(?P<a>\w+)|\[\s*(["'])(?P<b>\w+)\2\s*\])\.grid\.get_field\(\s*(["'])(?P<field>\w+)\4\s*\)\.get_query\s*="""
)
LABEL = re.compile(
	r"""set_df_property\(\s*(["'])(?P<field>\w+)\1\s*,\s*(["'])label\3\s*,\s*frm\.doc\.(?P<from>\w+)\s*\)"""
)

#: ``set_query`` calls whose field is a variable: (file, expression) -> the fields it can name.
#: unified_tab_controller.js primary_address_link_field() returns customer_primary_address,
#: supplier_primary_address or primary_address; Opportunity has only the last, and an Item none
#: (unified_tab_controller.js is not an Item script). item.js loops over its `fields` list
#: (test_items_expense_accounts_are_the_loops_list).
DYNAMIC = {
	("public/js/global_enhancements/unified_tab_controller.js", "address_link_field"): {
		"Opportunity": ["primary_address"],
	},
	("erpnext/stock/doctype/item/item.js", "field"): {
		"Item": [
			"purchase_expense_account",
			"purchase_expense_contra_account",
			"expenses_added_to_stock_account",
			"expenses_added_to_stock_contra_account",
			"default_cogs_account",
		],
	},
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


def _registers_on(code, doctype):
	if re.search(r"""form\.on\(\s*["'](""" + re.escape(doctype) + r"""|\*)["']""", code):
		return True
	# A script that registers on a doctype held in a variable (a loop over several, or every one):
	# it cannot be shown not to run on this DocType, so it is read too.
	return bool(re.search(r"""form\.on\(\s*[A-Za-z_]\w*\s*,""", code))


def _app_scripts(doctype):
	"""``{relative path: code}``: doctype_js[doctype] and every app script registering on it."""
	listed = _doctype_js().get(doctype) or []
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
		if _registers_on(code, doctype):
			out[rel] = code
	return out


def _erpnext_lines(doctype):
	spec = AUDITED[doctype]
	data = json.loads(spec["fixture"].read_text(encoding="utf-8"))
	return [(f"{spec['erpnext']}:{row['line']}", row["text"]) for row in data["lines"]]


def _scan(doctype):
	"""``({key: [where]}, [unresolved dynamic], {field: from})`` over every script of ``doctype``."""
	found, unresolved, labels = {}, [], {}
	sources = list(_erpnext_lines(doctype))
	for rel, code in _app_scripts(doctype).items():
		for number, line in enumerate(code.splitlines(), 1):
			sources.append((f"{rel}:{number}", line))
	for where, text in sources:
		rel = where.rsplit(":", 1)[0]
		for match in SET_QUERY.finditer(text):
			field = match.group("field")
			if field is None:
				expression = match.group("var")
				names = DYNAMIC.get((rel, expression), {}).get(doctype)
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
		for match in GRID_QUERY.finditer(text):
			table = match.group("a") or match.group("b")
			found.setdefault(f"{table}.{match.group('field')}", []).append(where)
		for match in LABEL.finditer(text):
			labels[match.group("field")] = match.group("from")
	return found, unresolved, labels


class TestQueriesMirrorTheForm(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.scans = {doctype: _scan(doctype) for doctype in AUDITED}

	def test_every_guided_doctype_is_audited(self):
		self.assertEqual(
			set(AUDITED), set(CONFIGS), "add the new guided DocType to AUDITED, with its fixture"
		)

	def test_the_scan_sees_the_scripts(self):
		"""Guards the scanner itself: a regex that stopped matching would pass everything."""
		opportunity = _app_scripts("Opportunity")
		self.assertIn("public/js/global_enhancements/unified_tab_controller.js", opportunity)
		self.assertIn("public/js/crm_enhancements/opportunity_migrated_scripts.js", opportunity)
		found = self.scans["Opportunity"][0]
		for key in ("primary_contact", "party_name", "items.item_code", "customer_address"):
			self.assertIn(key, found)
		self.assertTrue(any("unified_tab_controller.js" in where for where in found["primary_address"]))
		self.assertIn("public/js/item.js", _app_scripts("Item"))
		item = self.scans["Item"][0]
		for key in (
			"item_defaults.expense_account",
			"reorder_levels.warehouse",
			"item_defaults.default_cogs_account",
		):
			self.assertIn(key, item)

	def test_a_dialogs_query_is_not_the_forms(self):
		"""item.js sets a query on a dialog's price_list: not a field of the Item form."""
		self.assertNotIn("price_list", self.scans["Item"][0])

	def test_every_set_query_is_mirrored_or_waived(self):
		for doctype, (found, _unresolved, _labels) in self.scans.items():
			config = CONFIGS[doctype]
			queries, waivers = config.get("queries") or {}, config.get("query_waivers") or {}
			missing = {
				key: where for key, where in found.items() if key not in queries and key not in waivers
			}
			self.assertEqual(
				missing,
				{},
				f"A {doctype} form script sets a query the guided page does not apply. Mirror it in the "
				"config's `queries`, or waive it in `query_waivers` with a reason.",
			)

	def test_nothing_is_mirrored_that_no_script_sets(self):
		for doctype, (found, _unresolved, _labels) in self.scans.items():
			config = CONFIGS[doctype]
			mirrored = set(config.get("queries") or {}) | set(config.get("query_waivers") or {})
			self.assertEqual(
				sorted(mirrored - set(found)), [], f"{doctype}: mirrored or waived, set by no script"
			)

	def test_a_set_query_on_a_variable_is_resolved(self):
		for doctype, (_found, unresolved, _labels) in self.scans.items():
			self.assertEqual(
				unresolved, [], f"{doctype}: add the expression to DYNAMIC with the fields it names"
			)

	def test_the_dynamic_address_field_is_still_the_three(self):
		code = _code(UNIFIED.read_text(encoding="utf-8"))
		body = code[code.index("primary_address_link_field: function") :]
		body = body[: body.index("\n\t},")]
		self.assertEqual(
			re.findall(r'return "(\w+)";', body),
			["customer_primary_address", "supplier_primary_address", "primary_address"],
		)

	def test_items_expense_accounts_are_the_loops_list(self):
		lines = [text for _where, text in _erpnext_lines("Item")]
		start = lines.index("let fields = [")
		listed = [line.strip().strip('",') for line in lines[start + 1 : lines.index("];")]]
		self.assertEqual(listed, DYNAMIC[("erpnext/stock/doctype/item/item.js", "field")]["Item"])

	def test_the_party_has_a_query_for_every_kind_it_may_be(self):
		queries = OPPORTUNITY["queries"]
		kinds = set(queries["opportunity_from"]["filters"]["name"][1])
		self.assertEqual(kinds, {"Customer", "Lead", "Prospect"})
		self.assertEqual(set(queries["party_name"]["cases"]), kinds)
		self.assertEqual(queries["party_name"]["by"], "opportunity_from")

	def test_contact_and_site_search_the_directory_and_billing_the_account(self):
		"""The directory's sources for the two the form's directory sets; the Billing Address is
		ERPNext's address_query on the account, as on the form (review of #1300)."""
		queries = OPPORTUNITY["queries"]
		for fieldname in ("primary_contact", "primary_address"):
			self.assertEqual(queries[fieldname].get("links"), "directory", fieldname)
		self.assertEqual(
			{k: v for k, v in queries["customer_address"].items() if k != "source"},
			{
				"query": "frappe.contacts.doctype.address.address.address_query",
				"filters": {
					"link_doctype": {"field": "opportunity_from"},
					"link_name": {"field": "party_name"},
				},
			},
		)

	def test_every_waiver_has_a_reason(self):
		for doctype in AUDITED:
			for key, reason in (CONFIGS[doctype].get("query_waivers") or {}).items():
				self.assertTrue(str(reason).strip(), f"{doctype} {key}")

	def test_labels_are_the_scripts_labels(self):
		for doctype, (_found, _unresolved, labels) in self.scans.items():
			mirrored = {k: v["field"] for k, v in (CONFIGS[doctype].get("label_from") or {}).items()}
			self.assertEqual(mirrored, labels, doctype)


class TestTheWonFieldsAreRequired(unittest.TestCase):
	def test_the_won_fields_are_what_the_form_makes_required(self):
		code = _code(MIGRATED.read_text(encoding="utf-8"))
		body = code[code.index("function set_rank_fields_mandatory(frm) {") :]
		body = body[: body.index("\n}\n")]
		fields = re.findall(r'set_df_property\("(\w+)", "reqd", reqd\)', body)
		status = re.search(r'frm\.doc\.status === "([^"]+)"', body).group(1)
		self.assertEqual(fields, OPPORTUNITY["won"]["fields"])
		self.assertEqual(status, OPPORTUNITY["won"]["status"])


class TestTheSchema(unittest.TestCase):
	def _problems(self, **extra):
		return check(dict(OPPORTUNITY, **extra))

	def test_every_config_is_sound(self):
		for doctype, config in CONFIGS.items():
			self.assertEqual(check(config), [], doctype)

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
		queries = OPPORTUNITY["queries"]
		self.assertEqual(query_fieldnames(queries["party_name"]), ["opportunity_from", "company"])
		self.assertEqual(query_fieldnames({"filters": [["Item", "x", "=", {"field": "y"}]]}), ["y"])
		self.assertEqual(query_fieldnames(queries["primary_contact"]), [])
		# A grid row's column is the row's, not a record field the server would have to send.
		self.assertEqual(query_fieldnames(CONFIGS["Item"]["queries"]["item_defaults.expense_account"]), [])


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

	def test_a_grid_column_takes_its_query_with_its_row(self):
		self.assertIn("[`${fieldname}.${child.fieldname}`]", self.editor)
		self.assertIn("Object.assign(this.query_ctx(), { row: row || {} })", self.editor)

	def test_links_search_with_the_records_doctype(self):
		"""search_link honors ignore_user_permissions only with a reference DocType; a grid cell has
		none on the guided page, so its column does not send the flag."""
		self.assertIn("this.set_link_reference(control);", self.editor)
		self.assertIn("control.get_reference_doctype = () => doctype;", self.editor)
		self.assertIn("delete out.ignore_user_permissions;", self.editor)

	def test_the_field_group_applies_the_docfields_conditions(self):
		"""v16's Layout.set_dependant_property skips a plain FieldGroup (no frm, not a dialog)."""
		self.assertIn("set_dependant_property(condition, fieldname, property) {", self.editor)
		self.assertIn("editor.apply_condition(this, condition, fieldname, property);", self.editor)
		self.assertIn("this.condition(df[key], CONDITION_FALLBACK[key])", self.editor)

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


def _item_conditions():
	"""``[(fieldname, key, expression)]``: Item's mandatory_depends_on and read_only_depends_on, from
	the trimmed v16 meta, this app's Item Custom Fields and its Item Property Setters."""
	out = {}
	meta = json.loads((FIXTURES / "erpnext_v16_item.json").read_text(encoding="utf-8"))["doctypes"]["Item"]
	for df in meta["fields"]:
		for key in ("mandatory_depends_on", "read_only_depends_on"):
			if df.get(key):
				out[(df["fieldname"], key)] = df[key]
	for custom in json.loads((APP / "fixtures" / "custom_field.json").read_text(encoding="utf-8")):
		if custom.get("dt") == "Item":
			for key in ("mandatory_depends_on", "read_only_depends_on"):
				if custom.get(key):
					out[(custom["fieldname"], key)] = custom[key]
	for setter in json.loads((APP / "fixtures" / "property_setter.json").read_text(encoding="utf-8")):
		if setter.get("doc_type") == "Item" and setter.get("property") in (
			"mandatory_depends_on",
			"read_only_depends_on",
		):
			out[(setter["field_name"], setter["property"])] = setter["value"]
	return [(fieldname, key, expression) for (fieldname, key), expression in sorted(out.items())]


RUNNER = r"""
const [, , root, payload] = process.argv;
const { ge_bind_save_key, RecordEditor, CONDITION_FALLBACK } = await import(`file://${root}/form_kit/ui/editor.js`);
const { item_conditions } = JSON.parse(payload);
// frappe.utils.eval as v16 has it: the expression with the context's names in scope.
globalThis.frappe = {
	utils: {
		eval: (code, ctx) => new Function(...Object.keys(ctx), `return (${code});`)(...Object.values(ctx)),
	},
};
const out = {};

// ---- Ctrl/Cmd+S
const calls = [];
const make = (name, showing, result) => ({
	name,
	shown: showing,
	showing() { return this.shown; },
	save_now() { calls.push(name); return result; },
});
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

// ---- apply_condition, with a fake FieldGroup: v16 Layout.evaluate_depends_on_value calls a function
// condition with the group's values; a control refreshes when its df changes.
const editor = new RecordEditor();
editor.doctype = "Item";
editor.name = "ITEM-1";
editor.is_new = false;
const fake_group = (base, values) => {
	const refreshed = [];
	const df = Object.assign({ reqd: base.reqd || 0, read_only: base.read_only || 0 }, {
		fk_reqd: base.reqd || 0,
		fk_read_only: base.read_only || 0,
	});
	return {
		values,
		refreshed,
		fields_dict: { f: { df, refresh: () => refreshed.push(1) } },
		evaluate_depends_on_value(condition) {
			return typeof condition === "function" ? condition(this.values) : !!this.values[condition];
		},
	};
};
const run = (base, expression, key, values) => {
	editor.values = values;
	const property = key === "mandatory_depends_on" ? "reqd" : "read_only";
	const fg = fake_group(base, {});
	editor.apply_condition(fg, editor.condition(expression, CONDITION_FALLBACK[key]), "f", property);
	return { value: fg.fields_dict.f.df[property], refreshed: fg.refreshed.length };
};
const won = 'eval:doc.status==="Closed Won"';
out.cond = {
	on: run({}, won, "mandatory_depends_on", { status: "Closed Won" }),
	off: run({}, won, "mandatory_depends_on", { status: "Lost" }),
	base_reqd_kept: run({ reqd: 1 }, won, "mandatory_depends_on", { status: "Lost" }),
	base_read_only_kept: run({ read_only: 1 }, "eval:doc.x", "read_only_depends_on", { x: 0 }),
	read_only_on: run({}, "eval:doc.x", "read_only_depends_on", { x: 1 }),
	throwing_reqd: run({}, "eval:doc.missing.deep", "mandatory_depends_on", {}),
	throwing_read_only: run({}, "eval:doc.missing.deep", "read_only_depends_on", {}),
	throwing_depends_on: editor.condition("eval:doc.missing.deep", CONDITION_FALLBACK.depends_on)({}),
	unchanged_no_refresh: run({ reqd: 1 }, won, "mandatory_depends_on", { status: "Closed Won" }),
	islocal_new: (editor.is_new = true, !!editor.condition("eval:doc.__islocal && doc.is_sales_item", true)({ is_sales_item: 1 })),
	islocal_saved: (editor.is_new = false, !!editor.condition("eval:doc.__islocal && doc.is_sales_item", true)({ is_sales_item: 1 })),
};
// Toggling: on, then off, then on again on the same control.
{
	editor.values = {};
	const fg = fake_group({}, { status: "Closed Won" });
	const cond = editor.condition(won, false);
	const seen = [];
	for (const status of ["Closed Won", "Lost", "Closed Won"]) {
		fg.values = { status };
		editor.apply_condition(fg, cond, "f", "reqd");
		seen.push(fg.fields_dict.f.df.reqd);
	}
	out.cond.toggles = seen;
}

// ---- Item's conditions against the Desk: v16's form sets the property to the expression's truth
// (Layout.set_dependant_property on frm.doc); the guided editor must land on the same value for a
// field that is neither required nor read-only by itself.
const desk = (expression, doc) => {
	if (expression.startsWith("eval:")) return !!frappe.utils.eval(expression.slice(5), { doc, parent: doc });
	const value = doc[expression];
	return Array.isArray(value) ? value.length > 0 : !!value;
};
const flags = ["is_stock_item", "is_fixed_asset", "is_purchase_item", "is_customer_provided_item", "has_batch_no", "has_expiry_date", "has_variants"];
const docs = [];
for (let mask = 0; mask < 1 << flags.length; mask += 1) {
	const doc = {};
	flags.forEach((flag, bit) => (doc[flag] = (mask >> bit) & 1));
	doc.variant_of = mask % 3 === 0 ? "BASE-ITEM" : "";
	doc.variant_based_on = mask % 2 === 0 ? "Item Attribute" : "Manufacturer";
	docs.push(doc);
}
out.item = item_conditions.map(([fieldname, key, expression]) => {
	let differ = 0;
	let on = 0;
	for (const doc of docs) {
		const expected = desk(expression, doc) ? 1 : 0;
		const got = run({}, expression, key, doc).value;
		if (got !== expected) differ += 1;
		on += expected;
	}
	return { fieldname, key, differ, on, total: docs.length };
});
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(shutil.which("node"), "node is not on PATH")
class TestTheEditorUnderNode(unittest.TestCase):
	"""editor.js itself, under node: Ctrl/Cmd+S and apply_condition."""

	@classmethod
	def setUpClass(cls):
		cls.item_conditions = _item_conditions()
		with tempfile.TemporaryDirectory() as tmp:
			folder = Path(tmp)
			(folder / "package.json").write_text('{"type": "module"}', encoding="utf-8")
			shutil.copytree(JS / "form_kit", folder / "form_kit")
			shutil.copytree(JS / "info_blocks", folder / "info_blocks")
			(folder / "runner.mjs").write_text(RUNNER, encoding="utf-8")
			proc = subprocess.run(
				[
					shutil.which("node"),
					str(folder / "runner.mjs"),
					folder.as_posix(),
					json.dumps({"item_conditions": cls.item_conditions}),
				],
				capture_output=True,
				text=True,
				encoding="utf-8",
				timeout=60,
			)
		if proc.returncode != 0:
			raise AssertionError(f"node failed:\n{proc.stderr}")
		cls.out = json.loads(proc.stdout)

	# ---- Ctrl/Cmd+S: frappe's shortcut ends in the current page's save_action (desk.js
	# trigger_primary_action). Each editor adds itself once; a page shown again never binds twice.

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

	# ---- apply_condition

	def test_a_condition_turns_the_flag_on_and_off(self):
		c = self.out["cond"]
		self.assertEqual(c["on"]["value"], 1)
		self.assertEqual(c["off"]["value"], 0)
		self.assertEqual(c["read_only_on"]["value"], 1)
		self.assertEqual(c["toggles"], [1, 0, 1])

	def test_the_field_keeps_what_it_is_by_itself(self):
		"""A field required by its docfield, or read-only by permission, stays so."""
		c = self.out["cond"]
		self.assertEqual(c["base_reqd_kept"]["value"], 1)
		self.assertEqual(c["base_read_only_kept"]["value"], 1)
		self.assertEqual(c["unchanged_no_refresh"]["refreshed"], 0)

	def test_a_condition_that_cannot_be_read_adds_nothing(self):
		"""Review of #1300: a throwing expression shows the field (depends_on) but never makes it
		required or read-only."""
		c = self.out["cond"]
		self.assertEqual(c["throwing_reqd"]["value"], 0)
		self.assertEqual(c["throwing_read_only"]["value"], 0)
		self.assertIs(c["throwing_depends_on"], True)

	def test_a_condition_reads_islocal_as_the_form_does(self):
		"""Item's Standard Selling Rate shows only on a new Item (eval:doc.__islocal && ...)."""
		self.assertIs(self.out["cond"]["islocal_new"], True)
		self.assertIs(self.out["cond"]["islocal_saved"], False)

	def test_items_conditions_are_the_desks(self):
		"""Every mandatory_depends_on / read_only_depends_on on the Item lands where the Desk form
		puts it, on 128 records (each pair of exclusive ticks, batches, expiry, variants)."""
		expected = {
			("is_stock_item", "read_only_depends_on"),
			("is_fixed_asset", "read_only_depends_on"),
			("is_purchase_item", "read_only_depends_on"),
			("is_customer_provided_item", "read_only_depends_on"),
			("asset_category", "mandatory_depends_on"),
			("shelf_life_in_days", "mandatory_depends_on"),
			("attributes", "mandatory_depends_on"),
		}
		self.assertTrue(expected <= {(f, k) for f, k, _e in self.item_conditions})
		for row in self.out["item"]:
			self.assertEqual(row["differ"], 0, row)
			self.assertTrue(0 < row["on"] < row["total"], f"{row}: the matrix never toggles it")


if __name__ == "__main__":
	unittest.main()
