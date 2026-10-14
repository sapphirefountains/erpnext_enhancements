"""Which script reports still format a phone or a date by hand (form kit P3.3). Bench-free.

The report face (``public/js/report_face/``, docs/app-design-system.md "The report face") is the one
formatter script reports call for their phone, party, pillar, money and date columns, and the
Python side of a report reaches the same rules through ``utils/normalize`` or the print helpers.
This suite reads every Script Report's ``.js``, ``.py`` and ``.html`` (comments and docstrings
stripped: the comment that explains a rule names the very thing it forbids) and lists every place
that still formats a phone or a date itself.

Three lists decide what a finding means:

* ``ENFORCED``: the CRM and sales reports, moved first (ERPNext TASK-2026-02756). A finding in one
  of them fails the build, and so does one that stops calling the face or leaves a phone,
  party or value-stream column unmarked.
* ``ALLOWED``: a report that legitimately shows something differently, with the reason.
* ``KNOWN_REMAINING``: the rest still to move, "as they are touched". Reported, not failed, but
  the list can only shrink: a report not on it that starts formatting by hand fails, a listed
  report that picks up a new kind of hand formatting fails, and an entry that no longer finds
  anything fails until it is deleted, so the list is always the truth.

Run: python -m unittest erpnext_enhancements.tests.test_report_hand_formatting -v
"""

import ast
import io
import json
import re
import sys
import tokenize
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP = REPO_ROOT / "erpnext_enhancements"
CELLS_JS = APP / "public" / "js" / "report_face" / "cells.js"

# ---------------------------------------------------------------------------
# The rules. Each is (id, the file kinds it reads, pattern, what it catches).

JS = {".js", ".html"}
PY = {".py"}

RULES = (
	(
		"date:frappe-datetime",
		JS,
		re.compile(r"\bfrappe\.datetime\.(?:str_to_user|obj_to_user|global_date_format)\s*\("),
		"frappe.datetime.str_to_user and its siblings follow the Desk's System Settings, not the face",
	),
	(
		"date:moment-format",
		JS,
		re.compile(r"\bmoment\s*\(.*?\)\s*\.format\s*\("),
		"moment(...).format(...)",
	),
	(
		"date:pattern-string",
		JS,
		re.compile(r"\.format\s*\(\s*[\"'`][^\"'`\n]*\b(?:YYYY|YY|MM|DD|HH|hh|mm)\b"),
		'a .format("MM/DD/YYYY")-style pattern',
	),
	(
		"date:to-locale",
		JS,
		re.compile(r"\.toLocale(?:Date|Time)String\s*\("),
		"toLocaleDateString / toLocaleTimeString",
	),
	(
		"date:assembled",
		JS,
		re.compile(r"\.getMonth\s*\(\s*\)\s*\+\s*1"),
		"a date assembled from getMonth() + 1",
	),
	(
		"date:frappe-format",
		JS | PY,
		re.compile(
			r"\bfrappe\.format(?:_value)?\s*\((?:(?!\bfrappe\.format).){0,200}?[\"']?fieldtype[\"']?\s*:\s*"
			r"[\"'](?:Date|Datetime|Time)[\"']",
			re.S,
		),
		"frappe.format(x, {fieldtype: 'Date'}): the Desk's format, not the face's",
	),
	(
		"date:strftime",
		PY,
		re.compile(r"\.strftime\s*\("),
		"strftime",
	),
	(
		"date:frappe-utils",
		PY,
		re.compile(
			r"(?<![\w.])(?:formatdate|global_date_format)\s*\("
			r"|\bfrappe\.utils\.(?:formatdate|format_date|format_datetime|format_time|global_date_format)\s*\("
			r"|from\s+frappe\.utils(?:\.data)?\s+import\s+[^\n]*\b(?:formatdate|format_date|format_datetime|format_time|global_date_format)\b"
		),
		"frappe.utils' formatdate / format_datetime: the Desk's format; use utils.normalize",
	),
	(
		"date:in-text",
		PY,
		None,  # see _date_in_text: a bare *_date variable passed to str.format prints ISO
		"a date passed straight into .format(...), which prints 2026-10-14",
	),
	(
		"phone:regex",
		JS | PY,
		re.compile(r"(?:\\d|\[0-9\])\{3\}.{0,40}?(?:\\d|\[0-9\])\{4\}|\(\$1\)\s*\$2"),
		"a phone rebuilt with a regular expression",
	),
	(
		"phone:slices",
		PY,
		re.compile(r"\[\s*3\s*:\s*6\s*\]"),
		"a phone rebuilt from [3:6]-style slices",
	),
	(
		"phone:phonenumbers",
		PY,
		re.compile(r"\bphonenumbers\.format_number\s*\("),
		"phonenumbers.format_number",
	),
)

RULE_IDS = {rule[0] for rule in RULES}

# str.format on a string or on _("..."), not frappe.format(...).
_FORMAT_CALL = re.compile(r"[\)\"']\s*\.format\(([^()]*)\)")
_BARE_DATE = re.compile(r"(?<![\w.\"'])(?:\w+_date|as_of)(?![\w(\"'])")


def _date_in_text(code):
	return [m for m in _FORMAT_CALL.finditer(code) if _BARE_DATE.search(m.group(1))]


# ---------------------------------------------------------------------------
# What is scanned

#: The CRM and sales reports: every report in CRM Enhancements, plus Value Stream Performance
#: (Opportunity win rates and revenue per stream, filed under KPI Dashboards).
ENFORCED = frozenset(
	{
		"crm_enhancements/account_data_quality",
		"crm_enhancements/attribution_gaps",
		"crm_enhancements/closed_won_reconciliation",
		"crm_enhancements/hand_off_process_coverage",
		"crm_enhancements/named_account_targets",
		"crm_enhancements/opportunity_loss_reasons",
		"crm_enhancements/party_naming_audit",
		"kpi_dashboards/value_stream_performance",
	}
)

#: A report that legitimately shows a phone or a date differently: {report: {rule: reason}}.
#: Empty on 2026-10-13: nothing found so far has a reason to differ. (Data Normalization Preview,
#: which shows phones exactly as stored on purpose, does it by not formatting them at all, which
#: no rule flags.) An entry needs its reason, and fails once the rule stops finding anything.
ALLOWED = {}

#: Still to move, "as they are touched" (2026-10-13). {report: {rules found}}. Only shrinks.
KNOWN_REMAINING = {
	"hr_enhancements/crew_qualification_roster": {"date:frappe-datetime", "date:frappe-format"},
	"kpi_dashboards/marketing_spend_rollup": {"date:in-text"},
	"project_enhancements/supplier_pickup_list": {"date:frappe-datetime"},
}


def _script_reports():
	"""{"module/report": folder} for every Script Report in the app."""
	out = {}
	for spec in sorted(APP.glob("*/report/*/*.json")):
		folder = spec.parent
		if spec.stem != folder.name:
			continue
		try:
			doc = json.loads(spec.read_text(encoding="utf-8"))
		except ValueError:
			continue
		if doc.get("doctype") != "Report" or doc.get("report_type") != "Script Report":
			continue
		out[f"{folder.parent.parent.name}/{folder.name}"] = folder
	return out


def _files(folder):
	return [
		path
		for path in sorted(folder.iterdir())
		if path.suffix in {".js", ".py", ".html"}
		and path.name != "__init__.py"
		and not path.name.startswith("test_")
	]


def _line_breaks(match):
	return "\n" * match.group(0).count("\n")


def _strip_js(source):
	# A comment block becomes its own line breaks, so a finding's line number is the file's.
	source = re.sub(r"<!--[\s\S]*?-->", _line_breaks, source)
	source = re.sub(r"/\*[\s\S]*?\*/", _line_breaks, source)
	lines = []
	for line in source.splitlines():
		if line.strip().startswith("//"):
			lines.append("")
			continue
		# A trailing comment after code. A URL's // has no space before it.
		lines.append(re.sub(r"\s//\s.*$", "", line))
	return "\n".join(lines)


def _strip_py(source):
	"""The source with comments and docstrings blanked, line numbers kept."""
	lines = source.splitlines()
	try:
		tree = ast.parse(source)
	except SyntaxError:
		return source
	for node in ast.walk(tree):
		if (
			isinstance(node, ast.Expr)
			and isinstance(node.value, ast.Constant)
			and isinstance(node.value.value, str)
		):
			for i in range(node.lineno - 1, node.end_lineno):
				lines[i] = ""
	text = "\n".join(lines)
	cuts = {}
	try:
		for tok in tokenize.generate_tokens(io.StringIO(text).readline):
			if tok.type == tokenize.COMMENT:
				cuts[tok.start[0] - 1] = tok.start[1]
	except (tokenize.TokenError, IndentationError):
		return text
	out = text.splitlines()
	for row, col in cuts.items():
		out[row] = out[row][:col]
	return "\n".join(out)


def code_of(path):
	source = path.read_text(encoding="utf-8")
	return _strip_py(source) if path.suffix == ".py" else _strip_js(source)


def findings_in(path, code=None):
	"""[(rule id, line number, the text matched)] for one file."""
	code = code_of(path) if code is None else code
	found = []
	for rule_id, kinds, pattern, _why in RULES:
		if path.suffix not in kinds:
			continue
		matches = _date_in_text(code) if pattern is None else list(pattern.finditer(code))
		for m in matches:
			line = code.count("\n", 0, m.start()) + 1
			found.append((rule_id, line, m.group(0).strip()[:80]))
	return found


def scan():
	"""{report: [(rule, "file:line", text)]} for every Script Report with a finding."""
	out = {}
	for report, folder in _script_reports().items():
		for path in _files(folder):
			for rule_id, line, text in findings_in(path):
				out.setdefault(report, []).append((rule_id, f"{path.name}:{line}", text))
	return out


def _unallowed(report, findings):
	allowed = ALLOWED.get(report, {})
	return [f for f in findings if f[0] not in allowed]


# ---------------------------------------------------------------------------
# Column checks on the enforced reports


def _columns(path):
	"""Every dict literal in a report's .py that looks like a column (has fieldname and fieldtype)."""
	tree = ast.parse(path.read_text(encoding="utf-8"))
	out = []
	for node in ast.walk(tree):
		if not isinstance(node, ast.Dict):
			continue
		keys = {}
		for k, v in zip(node.keys, node.values, strict=True):
			if isinstance(k, ast.Constant) and isinstance(v, ast.Constant):
				keys[k.value] = v.value
		if "fieldname" in keys and "fieldtype" in keys:
			out.append(keys)
	return out


def _kinds():
	match = re.search(r"export const KINDS = \[([^\]]*)\];", CELLS_JS.read_text(encoding="utf-8"))
	return set(re.findall(r'"([a-z]+)"', match.group(1)))


FACE_FIELDTYPES = {"Date", "Datetime", "Time", "Currency", "Phone"}
PHONE_NAME = re.compile(r"(?:^|_)(?:phone|mobile|cell)(?:_|$)")
STREAM_NAME = re.compile(r"^value_streams?$")
PARTY_NAME = re.compile(r"^(?:party|party_name|customer|account)$")
FACE_CALL = re.compile(
	r"report_face\b[\s\S]{0,120}?\.format\(\s*value,\s*row,\s*column,\s*data,\s*default_formatter\s*\)"
)


class RulesTest(unittest.TestCase):
	"""Each rule fires on the hand formatting it names and not on the face, or on prose about it."""

	def _ids(self, suffix, code):
		return {rule for rule, _line, _text in findings_in(Path("x" + suffix), code)}

	def test_each_js_rule_fires(self):
		samples = {
			"date:frappe-datetime": "x = frappe.datetime.str_to_user(d);",
			"date:moment-format": "x = moment(d).format('L');",
			"date:pattern-string": 'x = frappe.datetime.str_to_obj(d).format("MM/DD/YYYY");',
			"date:to-locale": "x = new Date(d).toLocaleDateString();",
			"date:assembled": "x = `${d.getMonth() + 1}/${d.getDate()}`;",
			"date:frappe-format": 'x = frappe.format(d, { fieldtype: "Date" });',
			"phone:regex": 'x = p.replace(/(\\d{3})(\\d{3})(\\d{4})/, "($1) $2-$3");',
		}
		for rule, code in samples.items():
			with self.subTest(rule=rule):
				self.assertIn(rule, self._ids(".js", _strip_js(code)))

	def test_each_python_rule_fires(self):
		samples = {
			"date:strftime": "x = d.strftime('%m/%d/%Y')\n",
			"date:frappe-utils": "from frappe.utils import formatdate\nx = formatdate(d)\n",
			"date:frappe-format": 'x = frappe.format(d, {"fieldtype": "Date"})\n',
			"date:in-text": 'x = _("{0} to {1}").format(from_date, to_date)\n',
			"phone:regex": 'x = re.sub(r"(\\d{3})(\\d{3})(\\d{4})", r"(\\1) \\2-\\3", p)\n',
			"phone:slices": 'x = f"({d[:3]}) {d[3:6]}-{d[6:]}"\n',
			"phone:phonenumbers": "x = phonenumbers.format_number(n, 1)\n",
		}
		for rule, code in samples.items():
			with self.subTest(rule=rule):
				self.assertIn(rule, self._ids(".py", _strip_py(code)))

	def test_every_rule_has_a_sample(self):
		# The two sample tables above cover every rule, so none can be added untested.
		self.assertEqual(
			RULE_IDS,
			{
				"date:frappe-datetime",
				"date:moment-format",
				"date:pattern-string",
				"date:to-locale",
				"date:assembled",
				"date:frappe-format",
				"date:strftime",
				"date:frappe-utils",
				"date:in-text",
				"phone:regex",
				"phone:slices",
				"phone:phonenumbers",
			},
		)

	def test_the_face_and_the_shared_library_are_not_hand_formatting(self):
		js = _strip_js(
			"x = face.date(d) + face.phone(p) + face.datetime(t);\n"
			"default: frappe.datetime.get_today(),\n"
			"if (frappe.datetime.get_day_diff(a, b) > 0) {}\n"
		)
		self.assertEqual(self._ids(".js", js), set())
		py = _strip_py(
			"from erpnext_enhancements.utils import normalize\n"
			"x = normalize.format_date(d) + normalize.format_datetime(t)\n"
			"y = print_style.ps_date(d) + ps.ps_phone(p)\n"
			'z = _("{0} to {1}").format(normalize.format_date(from_date), normalize.format_date(to_date))\n'
			'w = frappe.format_value(total, {"fieldtype": "Currency"})\n'
		)
		self.assertEqual(self._ids(".py", py), set())

	def test_comments_and_docstrings_are_not_code(self):
		js = _strip_js(
			"/* frappe.datetime.str_to_user( */\n// moment(d).format('L')\nx = 1; // d.toLocaleDateString()\n"
		)
		self.assertEqual(self._ids(".js", js), set())
		html = _strip_js("<!-- frappe.datetime.str_to_user(x) -->\n<p>ok</p>\n")
		self.assertEqual(self._ids(".html", html), set())
		py = _strip_py('"""Never d.strftime(\'%m\')."""\nx = 1  # formatdate(d)\n')
		self.assertEqual(self._ids(".py", py), set())


class ReportFormattingScanTest(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.reports = _script_reports()
		cls.found = scan()

	def test_the_lists_name_real_script_reports(self):
		self.assertGreaterEqual(len(self.reports), 30, "the report discovery found too few to be right")
		for name in ENFORCED | set(ALLOWED) | set(KNOWN_REMAINING):
			with self.subTest(report=name):
				self.assertIn(name, self.reports)
		self.assertFalse(ENFORCED & set(KNOWN_REMAINING), "a report cannot be enforced and still to move")
		for name, rules in list(ALLOWED.items()) + list(KNOWN_REMAINING.items()):
			for rule in rules:
				self.assertIn(rule, RULE_IDS, f"{name} names a rule that does not exist: {rule}")

	def test_the_crm_and_sales_reports_format_nothing_by_hand(self):
		for name in sorted(ENFORCED):
			with self.subTest(report=name):
				left = _unallowed(name, self.found.get(name, []))
				self.assertEqual(
					left,
					[],
					f"{name} formats a phone or a date by hand; call the report face (form kit P3.3)",
				)

	def test_no_other_report_starts_formatting_by_hand(self):
		for name, findings in sorted(self.found.items()):
			if name in ENFORCED:
				continue
			left = {rule for rule, _where, _text in _unallowed(name, findings)}
			if not left:
				continue
			with self.subTest(report=name):
				known = KNOWN_REMAINING.get(name)
				self.assertIsNotNone(
					known,
					f"{name} formats by hand ({sorted(left)}): use the report face instead; "
					"the known-remaining list only shrinks",
				)
				self.assertLessEqual(
					left, known, f"{name} picked up new hand formatting: {sorted(left - known)}"
				)

	def test_the_known_remaining_list_is_current(self):
		"""An entry whose report no longer finds a rule is deleted, so the list is the truth."""
		for name, rules in sorted(KNOWN_REMAINING.items()):
			found = {rule for rule, _where, _text in _unallowed(name, self.found.get(name, []))}
			with self.subTest(report=name):
				self.assertEqual(
					found,
					rules,
					f"{name} no longer formats {sorted(rules - found)} by hand: take it off KNOWN_REMAINING",
				)

	def test_every_allowance_is_still_needed(self):
		for name, rules in sorted(ALLOWED.items()):
			found = {rule for rule, _where, _text in self.found.get(name, [])}
			for rule, reason in rules.items():
				with self.subTest(report=name, rule=rule):
					self.assertTrue(reason.strip(), "an allowance needs its reason")
					self.assertIn(rule, found, f"{name} no longer needs its {rule} allowance")

	def test_report_what_is_still_to_move(self):
		"""Not a check: the list, printed, so a CI log says what is left."""
		lines = []
		for name, findings in sorted(self.found.items()):
			left = _unallowed(name, findings)
			for rule, where, text in left:
				lines.append(f"  {name}: {rule} at {where}: {text}")
		sys.stderr.write(
			"\nScript reports still formatting a phone or a date by hand (form kit P3.3):\n"
			+ ("\n".join(lines) if lines else "  none")
			+ "\n"
		)


class EnforcedColumnsTest(unittest.TestCase):
	"""The CRM and sales reports mark their columns and call the face."""

	@classmethod
	def setUpClass(cls):
		cls.reports = _script_reports()
		cls.kinds = _kinds()

	def test_the_kinds_are_the_documented_seven(self):
		self.assertEqual(self.kinds, {"phone", "party", "pillar", "money", "date", "datetime", "time"})

	def test_every_marker_in_any_report_is_a_kind(self):
		for name, folder in sorted(self.reports.items()):
			for path in folder.glob("*.py"):
				for column in _columns(path):
					face = column.get("sf_face")
					if face in (None, False, "none"):
						continue
					with self.subTest(report=name, column=column.get("fieldname")):
						self.assertIn(face, self.kinds)
						if face == "party":
							self.assertIn(
								column.get("fieldtype"),
								("Link", "Dynamic Link"),
								"a party column is a Link or a Dynamic Link, so Frappe still draws a link without the face",
							)

	def test_phone_party_and_stream_columns_are_marked(self):
		for name in sorted(ENFORCED):
			path = self.reports[name] / f"{self.reports[name].name}.py"
			for column in _columns(path):
				fieldname = column["fieldname"]
				if column.get("hidden"):
					continue
				with self.subTest(report=name, column=fieldname):
					if PHONE_NAME.search(fieldname):
						self.assertEqual(column.get("sf_face"), "phone")
					if STREAM_NAME.match(fieldname):
						self.assertEqual(column.get("sf_face"), "pillar")
					if PARTY_NAME.match(fieldname) and column.get("fieldtype") in (
						"Link",
						"Dynamic Link",
						"Data",
					):
						self.assertEqual(column.get("sf_face"), "party")

	def test_a_report_with_a_face_column_calls_the_face(self):
		for name in sorted(ENFORCED):
			folder = self.reports[name]
			columns = _columns(folder / f"{folder.name}.py")
			needs = [
				c["fieldname"]
				for c in columns
				if c.get("sf_face") not in (None, False, "none") or c.get("fieldtype") in FACE_FIELDTYPES
			]
			if not needs:
				continue
			with self.subTest(report=name):
				code = _strip_js((folder / f"{folder.name}.js").read_text(encoding="utf-8"))
				self.assertRegex(
					code, FACE_CALL, f"{name} has {needs} but its formatter does not call the report face"
				)

	def test_no_report_link_is_hand_built(self):
		"""get_form_link (or the face's party link), never a hand-typed /app/ or /desk/ path."""
		for name in sorted(ENFORCED):
			for path in _files(self.reports[name]):
				with self.subTest(report=name, file=path.name):
					self.assertNotRegex(code_of(path), r"[\"'`(]/(?:app|desk)/")


if __name__ == "__main__":
	unittest.main()
