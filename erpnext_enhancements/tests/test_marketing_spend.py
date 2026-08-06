"""Bench-free tests for WP-4: marketing spend import and the value-stream dashboard.

Run: python -m pytest erpnext_enhancements/tests/test_marketing_spend.py -q

Own frappe stub, so it needs its own pytest step in ``ci.yml``.

The normalisation path is where the fiddly bits live — month coercion, currency
stripping, channel aliasing — and it is pure, so it is tested directly rather than
through a report. The rest are structural invariants:

* the importer's generated name must match the doctype's own ``autoname``, or the
  "upsert" silently becomes "insert a duplicate that then collides";
* the value-stream dashboard must have **no required filters**, because it is
  opened live in a standing weekly meeting;
* the Unsourced Leads KPI must never read ``Lead.source`` again — the column with
  no DocField behind it since erpnext v15.
"""

import ast
import json
import pathlib
import sys
import types

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _install_frappe_stub():
	if "frappe" in sys.modules and getattr(sys.modules["frappe"], "_ee_spend_stub", False):
		return

	import datetime as _dt

	frappe = types.ModuleType("frappe")
	frappe._ee_spend_stub = True

	class _Dict(dict):
		def __getattr__(self, key):
			return self.get(key)

		def __setattr__(self, key, value):
			self[key] = value

	frappe._dict = _Dict
	frappe.throw = lambda message, *a, **k: (_ for _ in ()).throw(ValueError(message))
	frappe.log_error = lambda *a, **k: None
	frappe.get_roles = lambda *a, **k: []
	frappe.get_traceback = lambda *a, **k: ""
	frappe.msgprint = lambda *a, **k: None
	frappe.scrub = lambda v: str(v).lower().replace(" ", "_").replace("/", "_")
	frappe.PermissionError = type("PermissionError", (Exception,), {})
	frappe.whitelist = lambda *a, **k: (lambda fn: fn)
	frappe.db = types.SimpleNamespace(
		has_column=lambda *a, **k: True, exists=lambda *a, **k: False,
		sql=lambda *a, **k: [], get_single_value=lambda *a, **k: None,
	)

	def _flt(v, precision=None):
		try:
			out = float(v or 0)
		except (TypeError, ValueError):
			return 0.0
		return round(out, precision) if precision is not None else out

	def _getdate(v=None):
		if v is None:
			return _dt.date.today()
		if isinstance(v, _dt.datetime):
			return v.date()
		if isinstance(v, _dt.date):
			return v
		return _dt.datetime.strptime(str(v)[:10], "%Y-%m-%d").date()

	frappe.utils = types.ModuleType("frappe.utils")
	for name, fn in (
		("flt", _flt), ("getdate", _getdate),
		("cint", lambda v: int(float(v or 0))),
		("nowdate", lambda: _dt.date.today().isoformat()),
		("now", lambda: _dt.datetime.now().isoformat()),
		("add_months", lambda d, m: _getdate(d)),
		("escape_html", lambda v: str(v)),
	):
		setattr(frappe.utils, name, fn)
		setattr(frappe, name, fn)
	frappe._ = lambda v, *a, **k: v
	frappe.model = types.ModuleType("frappe.model")
	frappe.model.document = types.ModuleType("frappe.model.document")
	frappe.model.document.Document = type("Document", (), {})

	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = frappe.utils
	sys.modules["frappe.model"] = frappe.model
	sys.modules["frappe.model.document"] = frappe.model.document


_install_frappe_stub()

from erpnext_enhancements.kpi_dashboards import marketing_spend_import as importer

# ---------------------------------------------------------------------------
# channel canonicalisation
# ---------------------------------------------------------------------------


def test_the_four_spellings_of_google_ads_become_one_line_item():
	"""'Google Ads', 'google ads', 'Google AdWords' and 'AdWords' are one line to a
	marketer and four to a GROUP BY. That fragmentation is what makes a spend
	rollup useless."""
	for spelling in ("Google Ads", "google ads", "Google AdWords", "adwords", "GOOGLE PPC"):
		assert importer.canonical_channel(spelling) == "Google Ads", spelling


def test_an_unknown_channel_passes_through_unchanged():
	"""An unknown channel is a NEW channel, not an error. Blocking an import over
	one would be worse than a slightly untidy list."""
	assert importer.canonical_channel("Blimp Advertising") == "Blimp Advertising"
	assert importer.canonical_channel("  Radio  ") == "Radio"
	assert importer.canonical_channel("") == ""
	assert importer.canonical_channel(None) == ""


def test_aliases_never_collide():
	"""An alias appearing under two canonical names would make canonicalisation
	depend on dict ordering — i.e. silently merge the wrong two budgets."""
	seen = {}
	for canonical, aliases in importer.CHANNEL_ALIASES.items():
		for alias in aliases:
			assert alias not in seen, f"{alias!r} maps to both {seen.get(alias)!r} and {canonical!r}"
			seen[alias] = canonical
		assert canonical.lower() not in seen or seen[canonical.lower()] == canonical


# ---------------------------------------------------------------------------
# month + amount normalisation
# ---------------------------------------------------------------------------


def test_months_snap_to_the_first():
	"""Spend is monthly. Storing the invoice date makes every GROUP BY month
	produce one bucket per invoice."""
	assert str(importer._normalise_month("2026-03-17")) == "2026-03-01"
	assert str(importer._normalise_month("2026-03-01")) == "2026-03-01"


def test_bare_month_formats_parse():
	"""'2026-03' and '03/2026' are both common in platform exports and neither
	parses as a date on its own."""
	assert str(importer._normalise_month("2026-03")) == "2026-03-01"
	assert str(importer._normalise_month("2026/03")) == "2026-03-01"
	assert str(importer._normalise_month("03/2026")) == "2026-03-01"


def test_unparseable_months_are_refused_not_guessed():
	assert importer._normalise_month("last quarter") is None
	assert importer._normalise_month("") is None
	assert importer._normalise_month(None) is None


def test_currency_formatting_is_stripped():
	assert importer._normalise_amount("$1,234.56") == 1234.56
	assert importer._normalise_amount("1234.56") == 1234.56
	assert importer._normalise_amount(" 2,000 ") == 2000


def test_parenthesised_figures_are_negative():
	"""Accounting exports write credits as (500). Read as 500 it inflates the
	budget baseline every downstream figure is divided by."""
	assert importer._normalise_amount("(500)") == -500
	assert importer._normalise_amount("($1,000.00)") == -1000


def test_unparseable_amounts_are_refused():
	assert importer._normalise_amount("n/a") is None
	assert importer._normalise_amount("") is None


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------


CSV = """Month,Channel,Amount,Notes
2026-01,google ads,"$1,200.00",Q1 push
2026-01,Facebook Ads,800,
2026-02,AdWords,1500,
bad-month,Print,100,
2026-02,Print,(50),refund
"""


def test_parse_maps_columns_canonicalises_and_reports_problems():
	rows, problems = importer.parse_rows(CSV)

	assert len(rows) == 4, "the unparseable month should be dropped, not guessed"
	assert any("bad-month" in p for p in problems)

	channels = [r["channel"] for r in rows]
	assert channels == ["Google Ads", "Meta Ads", "Google Ads", "Print"]

	# The rename is REPORTED, not silent — a wrong alias merges two budgets.
	renamed = [r for r in rows if r["renamed"]]
	assert {r["raw_channel"] for r in renamed} == {"google ads", "Facebook Ads", "AdWords"}

	assert rows[0]["amount"] == 1200.0
	assert rows[3]["amount"] == -50.0
	assert rows[0]["month"] == "2026-01-01"


def test_alternate_column_headings_are_accepted():
	"""Ad platforms and accounting exports never agree on headings."""
	rows, problems = importer.parse_rows("Period,Line Item,Cost\n2026-05,Print,250\n")
	assert not problems
	assert rows[0]["channel"] == "Print" and rows[0]["amount"] == 250


def test_missing_required_column_fails_with_a_useful_message():
	rows, problems = importer.parse_rows("Month,Channel\n2026-05,Print\n")
	assert not rows
	assert any("amount" in p for p in problems)


def test_an_oversized_file_is_refused():
	"""A per-click export instead of a monthly summary."""
	big = "Month,Channel,Amount\n" + "".join(f"2026-01,Print,{i}\n" for i in range(importer.MAX_ROWS + 5))
	rows, problems = importer.parse_rows(big)
	assert not rows and problems


# ---------------------------------------------------------------------------
# the upsert contract
# ---------------------------------------------------------------------------


def test_generated_name_matches_the_doctypes_own_autoname():
	"""If these drift, 'upsert' silently becomes 'insert', and the insert then
	collides on the naming rule — which is the exact failure that made frappe's
	own Data Import unusable here."""
	with open(ROOT / "kpi_dashboards" / "doctype" / "marketing_spend" / "marketing_spend.json", encoding="utf-8") as fh:
		autoname = json.load(fh)["autoname"]
	assert autoname == "format:SPEND-{month}-{channel}"
	assert importer._spend_name("2026-01-01", "Google Ads") == "SPEND-2026-01-01-Google Ads"


def test_import_is_role_gated():
	"""It writes the budget baseline every marketing figure is divided by."""
	assert importer.IMPORTER_ROLES == {"System Manager", "Sales Manager", "Accounts Manager"}
	source = (ROOT / "kpi_dashboards" / "marketing_spend_import.py").read_text(encoding="utf-8")
	assert "_check_permission()" in source


def test_preview_and_run_are_separate_endpoints():
	"""Nobody should discover a misread column by looking at a dashboard a week
	later."""
	source = (ROOT / "kpi_dashboards" / "marketing_spend_import.py").read_text(encoding="utf-8")
	tree = ast.parse(source)
	whitelisted = {
		node.name
		for node in ast.walk(tree)
		if isinstance(node, ast.FunctionDef)
		for d in node.decorator_list
		if isinstance(getattr(d, "func", d), ast.Attribute) and getattr(d, "func", d).attr == "whitelist"
	}
	assert whitelisted == {"preview_import", "run_import"}


# ---------------------------------------------------------------------------
# the dashboard
# ---------------------------------------------------------------------------


def test_the_dashboard_has_no_required_filters():
	"""It is opened live in a standing weekly meeting. A report that greets you
	with an empty filter bar wastes the first ninety seconds of it every week."""
	js = (ROOT / "kpi_dashboards" / "report" / "value_stream_performance" / "value_stream_performance.js").read_text(encoding="utf-8")
	assert "reqd: 1" not in js
	assert "reqd:1" not in js

	report = (ROOT / "kpi_dashboards" / "report" / "value_stream_performance" / "value_stream_performance.py").read_text(encoding="utf-8")
	assert "DEFAULT_MONTHS" in report, "the server must default the window when no dates are given"


def test_win_rate_excludes_open_deals():
	"""won / (won + lost). Including open deals makes every rate look terrible and
	move whenever somebody adds a lead."""
	report = (ROOT / "kpi_dashboards" / "report" / "value_stream_performance" / "value_stream_performance.py").read_text(encoding="utf-8")
	assert "decided = won + lost" in report
	assert "won / decided" in report


def test_unallocated_spend_is_never_apportioned():
	"""A made-up split is worse than an honest gap, because it looks like data."""
	report = (ROOT / "kpi_dashboards" / "report" / "value_stream_performance" / "value_stream_performance.py").read_text(encoding="utf-8")
	assert "UNALLOCATED" in report
	for smell in ("/ len(streams)", "pro_rata", "apportion"):
		assert smell not in report, f"spend appears to be divided across streams ({smell})"


def test_dashboard_query_count_stays_bounded():
	"""Four queries, not one per stream per metric. The Value Stream child table
	carries ~1,460 rows across three parent doctypes, so a per-stream subquery
	re-scans it every time."""
	source = (ROOT / "kpi_dashboards" / "report" / "value_stream_performance" / "value_stream_performance.py").read_text(encoding="utf-8")
	tree = ast.parse(source)
	sql_calls = [
		node for node in ast.walk(tree)
		if isinstance(node, ast.Call)
		and isinstance(node.func, ast.Attribute)
		and node.func.attr == "sql"
	]
	assert len(sql_calls) <= 4, f"{len(sql_calls)} raw queries — this report has to stay fast"

	# ...and none of them inside a loop over streams.
	for node in ast.walk(tree):
		if isinstance(node, (ast.For, ast.While)):
			body = ast.dump(node)
			assert "attr='sql'" not in body, "a query inside a loop — that is the N+1 this was written to avoid"


# ---------------------------------------------------------------------------
# the dead-column fix
# ---------------------------------------------------------------------------


def test_unsourced_leads_no_longer_reads_the_dead_source_column():
	"""`Lead.source` has had no DocField since erpnext v15 renamed it to
	utm_source. frappe never drops columns, so the query ran and returned a
	number — just not this number. Nothing writes `source`, so every new Lead
	scored as unsourced forever while the KPI looked plausible."""
	source = (ROOT / "kpi_dashboards" / "snapshots.py").read_text(encoding="utf-8")
	assert "custom_lead_source" in source

	# Check the SQL, not the file text — the phrase legitimately appears in the
	# comment explaining why it was removed. Comments are absent from the AST, so
	# scanning string constants is exactly the right granularity.
	tree = ast.parse(source)
	docstrings = set()
	for node in ast.walk(tree):
		body = getattr(node, "body", None)
		if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and body:
			first = body[0]
			if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
				docstrings.add(id(first.value))

	for node in ast.walk(tree):
		if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
			assert "coalesce(source" not in node.value.lower().replace(" ", ""), (
				"a query still reads the dead `source` column: " + node.value[:120]
			)


def test_source_alerting_fires_on_transitions_only():
	"""A nightly 'GSC is still broken' email is unsubscribed from within a week,
	and then the next real outage is invisible. That is roughly how a 40-day
	failure went unremarked."""
	source = (ROOT / "kpi_dashboards" / "snapshots.py").read_text(encoding="utf-8")
	tree = ast.parse(source)
	fn = next(
		n for n in ast.walk(tree)
		if isinstance(n, ast.FunctionDef) and n.name == "_alert_on_source_change"
	)
	dumped = ast.dump(fn)
	assert "was != now" in source, "alerting must compare against the previous state"
	assert "recovered" in dumped, "a recovery must be reported too, or nobody knows it is fixed"


# ---------------------------------------------------------------------------
# structure
# ---------------------------------------------------------------------------


def test_new_reports_have_explicit_roles():
	for slug in ("marketing_spend_rollup", "value_stream_performance"):
		path = ROOT / "kpi_dashboards" / "report" / slug / f"{slug}.json"
		with open(path, encoding="utf-8") as fh:
			report = json.load(fh)
		roles = {r["role"] for r in report.get("roles", [])}
		assert roles and "All" not in roles
		assert report["module"] == "KPI Dashboards"


def test_channel_breakdown_is_a_child_table_in_kpi_dashboards():
	path = ROOT / "kpi_dashboards" / "doctype" / "marketing_web_channel" / "marketing_web_channel.json"
	with open(path, encoding="utf-8") as fh:
		doc = json.load(fh)
	assert doc["istable"] == 1
	assert doc["module"] == "KPI Dashboards"


def test_these_are_native_reports_not_triton_views():
	"""Explicitly required: Triton is scoped to management and exploratory
	reporting, and these are operational reports everyone needs."""
	for slug in ("marketing_spend_rollup", "value_stream_performance"):
		with open(ROOT / "kpi_dashboards" / "report" / slug / f"{slug}.json", encoding="utf-8") as fh:
			assert json.load(fh)["report_type"] == "Script Report"
		source = (ROOT / "kpi_dashboards" / "report" / slug / f"{slug}.py").read_text(encoding="utf-8")
		# The word appears in docstrings explaining why these are native; what
		# must not appear is an actual dependency on it.
		tree = ast.parse(source)
		for node in ast.walk(tree):
			if isinstance(node, (ast.Import, ast.ImportFrom)):
				names = [a.name for a in node.names] + [getattr(node, "module", "") or ""]
				assert not any("triton" in (n or "").lower() for n in names), f"{slug} imports Triton"
