"""Bench-free tests for WP-5 account hygiene and WP-7 pipeline reconciliation.

Run: python -m pytest erpnext_enhancements/tests/test_account_hygiene.py -q

Installs its own minimal ``frappe`` stub, so it needs its own pytest step in
``ci.yml`` — sharing a process with another stub-installing suite is how they
cross-talk.

The invariants worth having, in order of what they cost if broken:

* **Nothing auto-links an Opportunity to a Project.** A wrong link corrupts
  revenue attribution, which is the exact defect WP-1 exists to fix — an
  auto-linker would manufacture the problem the programme is trying to remove.
* **Industry is never enforced by ``reqd`` / ``mandatory_depends_on``.** Those are
  evaluated on every save by every caller, and QBO's ``mapping.py`` inserts
  Customers without ``ignore_mandatory`` — a declarative rule would park every
  synced customer in manual review, on a system that is the book of record until
  the January go-live.
* **Bulk assignment never overwrites and never infers.**
* **The Customer field_order lost no fields** in the layout rewrite.
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
	if "frappe" in sys.modules and getattr(sys.modules["frappe"], "_ee_hygiene_stub", False):
		return

	import datetime as _dt

	frappe = types.ModuleType("frappe")
	frappe._ee_hygiene_stub = True

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
	frappe.request = None
	frappe.flags = _Dict()
	frappe.PermissionError = type("PermissionError", (Exception,), {})
	frappe.parse_json = json.loads
	frappe.has_permission = lambda *a, **k: True
	frappe.whitelist = lambda *a, **k: (lambda fn: fn)
	frappe.db = types.SimpleNamespace(
		has_column=lambda *a, **k: True,
		table_exists=lambda *a, **k: True,
		exists=lambda *a, **k: False,
		count=lambda *a, **k: 0,
		get_value=lambda *a, **k: None,
		set_value=lambda *a, **k: None,
		sql=lambda *a, **k: [],
		sql_list=lambda *a, **k: [],
	)

	def _cint(v):
		try:
			return int(float(v or 0))
		except (TypeError, ValueError):
			return 0

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
	for name, fn in (("cint", _cint), ("flt", _flt), ("getdate", _getdate),
	                 ("nowdate", lambda: _dt.date.today().isoformat()),
	                 ("escape_html", lambda v: str(v))):
		setattr(frappe.utils, name, fn)
		setattr(frappe, name, fn)
	frappe._ = lambda v, *a, **k: v

	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = frappe.utils


_install_frappe_stub()

from erpnext_enhancements.crm_enhancements import data_quality, reconciliation

# ---------------------------------------------------------------------------
# WP-7: the no-auto-link rule
# ---------------------------------------------------------------------------


def test_nothing_bulk_links_opportunities_to_projects():
	"""The single most important invariant in this package.

	`link_opportunity_to_project` takes ONE opportunity and ONE project. If a
	`link_all` / `link_matching` / bulk variant ever appears, a fuzzy score becomes
	a mass write and the revenue attribution WP-1 was commissioned to fix gets
	corrupted at scale.
	"""
	source = (ROOT / "crm_enhancements" / "reconciliation.py").read_text(encoding="utf-8")
	tree = ast.parse(source)

	whitelisted = []
	for node in ast.walk(tree):
		if not isinstance(node, ast.FunctionDef):
			continue
		for decorator in node.decorator_list:
			target = decorator.func if isinstance(decorator, ast.Call) else decorator
			if isinstance(target, ast.Attribute) and target.attr == "whitelist":
				whitelisted.append(node)

	names = {fn.name for fn in whitelisted}
	assert names == {"get_candidates", "link_opportunity_to_project"}, (
		f"unexpected whitelisted endpoints in reconciliation.py: {sorted(names)}"
	)

	linker = next(fn for fn in whitelisted if fn.name == "link_opportunity_to_project")
	params = [a.arg for a in linker.args.args]
	assert params == ["opportunity", "project"], (
		"link_opportunity_to_project must take exactly one opportunity and one project"
	)
	# A list/plural parameter would be the shape a bulk linker takes.
	for param in params:
		assert not param.endswith("s"), f"{param} looks plural — linking must stay singular"


def test_scoring_never_writes():
	"""score_candidates ranks and explains. It must not touch the database."""
	source = (ROOT / "crm_enhancements" / "reconciliation.py").read_text(encoding="utf-8")
	tree = ast.parse(source)
	scorer = next(
		n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "score_candidates"
	)
	dumped = ast.dump(scorer)
	for forbidden in ("set_value", "insert", "save", "delete"):
		assert forbidden not in dumped, f"score_candidates calls {forbidden} — it must be read-only"


def test_customer_is_a_precondition_not_a_score():
	"""A Project for a different customer is not a weaker match; it is not a match.
	An opportunity with no party must yield no candidates at all."""
	assert reconciliation.score_candidates({"customer": None, "opportunity_amount": 1000}) == []
	assert reconciliation.score_candidates({}) == []


def test_date_scoring_falls_off_and_is_deterministic():
	full, _reason = reconciliation._date_score("2026-01-01", "2026-01-10")
	assert full == reconciliation.WEIGHT_DATE, "9 days apart should be a full date match"

	none, _reason = reconciliation._date_score("2026-01-01", "2028-01-01")
	assert none == 0, "two years apart should score nothing"

	near, _r1 = reconciliation._date_score("2026-01-01", "2026-05-01")
	far, _r2 = reconciliation._date_score("2026-01-01", "2026-10-01")
	assert near > far > 0, "score must decay monotonically with distance"

	# Deterministic: the same input twice gives the same answer. The whole point
	# of WP-6/WP-7 tooling is that a given input has one answer.
	assert reconciliation._date_score("2026-01-01", "2026-05-01") == (near, _r1)


def test_value_scoring_ignores_missing_amounts():
	"""A blank amount is missing data, not evidence of a bad match — it must score
	zero rather than penalise, or every opportunity with no amount would rank its
	real project last."""
	assert reconciliation._value_score(0, 5000) == (0, None)
	assert reconciliation._value_score(5000, 0) == (0, None)

	full, _r = reconciliation._value_score(10000, 10500)
	assert full == reconciliation.WEIGHT_VALUE


def test_linking_is_role_gated():
	source = (ROOT / "crm_enhancements" / "reconciliation.py").read_text(encoding="utf-8")
	assert "LINKER_ROLES" in source
	assert "PermissionError" in source


# ---------------------------------------------------------------------------
# WP-5: the industry rule must not be declarative
# ---------------------------------------------------------------------------


def _property_setters():
	with open(ROOT / "fixtures" / "property_setter.json", encoding="utf-8") as fh:
		return json.load(fh)


def _custom_fields():
	with open(ROOT / "fixtures" / "custom_field.json", encoding="utf-8") as fh:
		return json.load(fh)


def test_industry_is_never_made_mandatory_declaratively():
	"""`reqd` / `mandatory_depends_on` are evaluated on EVERY save by EVERY caller.

	`quickbooks_online/core/mapping.py` inserts Customers with
	`ignore_permissions=True` but NOT `ignore_mandatory`, so a declarative rule
	would fail validation on every synced customer and park it in manual review —
	on the system that is the book of record until the January accounting go-live.
	"""
	offenders = [
		ps.get("name")
		for ps in _property_setters()
		if ps.get("doc_type") == "Customer"
		and ps.get("field_name") == "industry"
		and ps.get("property") in ("reqd", "mandatory_depends_on")
	]
	assert not offenders, (
		"a Property Setter makes Customer.industry mandatory: "
		+ ", ".join(offenders)
		+ " — this breaks the QuickBooks customer sync. Enforcement belongs in "
		"data_quality.enforce_industry, which exempts background jobs."
	)


def test_the_industry_hook_exempts_the_paths_that_would_break():
	source = (ROOT / "crm_enhancements" / "data_quality.py").read_text(encoding="utf-8")
	tree = ast.parse(source)
	gate = next(
		n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "enforce_industry"
	)
	dumped = ast.dump(gate)
	for guard in ("_bulk_context", "_is_background", "ignore_mandatory", "is_commercial"):
		assert guard in dumped, f"enforce_industry no longer checks {guard}"


def test_residential_accounts_are_never_asked_for_an_industry():
	assert "Residential" not in data_quality.COMMERCIAL_TYPES
	assert "Individual" not in data_quality.COMMERCIAL_TYPES
	assert "Residential" in data_quality.RESIDENTIAL_TYPES


def test_commercial_types_cover_the_legacy_spelling():
	"""'Company' stays in the list even after the retype patch, so a row created by
	an old integration is still covered."""
	assert set(data_quality.COMMERCIAL_TYPES) == {"Commercial", "Company", "Partnership"}


def test_background_detection_is_request_based():
	# The stub sets frappe.request = None, i.e. a background job.
	assert data_quality._is_background() is True


# ---------------------------------------------------------------------------
# WP-5: assisted, never automatic
# ---------------------------------------------------------------------------


def test_bulk_assign_has_a_closed_field_allowlist():
	"""The endpoint takes a fieldname from the client and interpolates it into a
	write. The set has to be closed by construction."""
	assert "industry" in data_quality.ASSIGNABLE_FIELDS
	assert "customer_group" in data_quality.ASSIGNABLE_FIELDS
	for forbidden in ("name", "owner", "docstatus", "disabled", "customer_primary_contact"):
		assert forbidden not in data_quality.ASSIGNABLE_FIELDS


def test_bulk_assign_never_overwrites_an_existing_value():
	source = (ROOT / "crm_enhancements" / "data_quality.py").read_text(encoding="utf-8")
	tree = ast.parse(source)
	fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "bulk_assign")
	assert "already_set" in ast.dump(fn), (
		"bulk_assign must skip rows that already carry a value — a bulk action that "
		"silently reassigns existing data undoes a careful afternoon's work"
	)


def test_nothing_infers_a_value():
	"""Assisted, not automatic, was an explicit requirement. No fuzzy matching, no
	name-based guessing anywhere in the hygiene module."""
	source = (ROOT / "crm_enhancements" / "data_quality.py").read_text(encoding="utf-8").lower()
	for smell in ("difflib", "fuzz", "levenshtein", "guess", "infer_industry"):
		assert smell not in source, f"data_quality.py references {smell!r} — it must not infer values"


def test_bulk_assign_is_role_gated():
	assert data_quality.REVIEWER_ROLES == {"System Manager", "Sales Manager"}


# ---------------------------------------------------------------------------
# The retype patch
# ---------------------------------------------------------------------------


def test_retype_patch_is_scoped_to_the_full_signature():
	"""Scoped to type AND both gaps, so a genuine 'Company' with real data is
	never touched — the mitigation for an otherwise irreversible retype."""
	source = (ROOT / "patches" / "retype_legacy_company_customers.py").read_text(encoding="utf-8")
	assert "industry IS NULL OR industry = ''" in source
	assert "customer_group IS NULL OR customer_group = ''" in source


def test_retype_patch_records_the_affected_ids_before_writing():
	"""Company and Commercial are indistinguishable afterwards. Without the log the
	change is one-way."""
	source = (ROOT / "patches" / "retype_legacy_company_customers.py").read_text(encoding="utf-8")
	tree = ast.parse(source)
	execute = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "execute")

	body = [ast.dump(stmt) for stmt in execute.body]
	log_at = next(i for i, s in enumerate(body) if "_record_for_reversal" in s)
	write_at = next(i for i, s in enumerate(body) if "UPDATE" in s or "sql" in s)
	assert log_at < write_at, "the reversal log must be written BEFORE the update"


def test_retype_patch_is_registered():
	patches_txt = (ROOT / "patches.txt").read_text(encoding="utf-8")
	assert "erpnext_enhancements.patches.retype_legacy_company_customers" in patches_txt


# ---------------------------------------------------------------------------
# The Customer form layout rewrite
# ---------------------------------------------------------------------------


def _customer_field_order():
	row = next(
		ps for ps in _property_setters()
		if ps.get("doc_type") == "Customer" and ps.get("property") == "field_order"
	)
	return json.loads(row["value"])


def test_the_four_requested_fields_are_in_the_first_section():
	"""customer_type, territory, custom_parent_account and custom_description were
	asked for 'up near the top of the page'."""
	order = _customer_field_order()
	first_section_end = order.index("custom_attribution_section")
	head = order[:first_section_end]
	for fieldname in ("customer_type", "territory", "custom_parent_account", "custom_description"):
		assert fieldname in head, f"{fieldname} is not in the first section"


def test_stripe_fields_have_their_own_section():
	order = _customer_field_order()
	assert "custom_stripe_section" in order
	stripe_at = order.index("custom_stripe_section")
	for fieldname in ("custom_stripe_customer_id", "custom_stripe_autopay_enabled", "custom_service_hold"):
		assert order.index(fieldname) > stripe_at, f"{fieldname} is outside the Stripe section"

	# ...and are no longer wedged into the identity block.
	assert order.index("custom_stripe_customer_id") > order.index("customer_primary_contact")


def test_the_stripe_section_is_defined_in_setup_not_fixtures():
	"""Those fields are is_system_generated, so the fixture export filters them out.
	stripe_payments/setup.py is their only source of truth."""
	setup = (ROOT / "stripe_payments" / "setup.py").read_text(encoding="utf-8")
	assert "custom_stripe_section" in setup

	names = {c["name"] for c in _custom_fields()}
	assert "Customer-custom_stripe_section" not in names, (
		"the Stripe section must not also be in fixtures — two sources of truth for "
		"one field is how they drift"
	)


def test_attribution_section_does_not_split_the_identity_block():
	"""v1.241.0 anchored it on custom_lead_source, which put a Section Break in the
	middle of the first section and cut it in two."""
	order = _customer_field_order()
	assert order.index("custom_attribution_section") > order.index("custom_lead_source") + 1

	field = next(
		c for c in _custom_fields()
		if c.get("dt") == "Customer" and c.get("fieldname") == "custom_attribution_section"
	)
	assert field["insert_after"] != "custom_lead_source"


def test_the_layout_rewrite_dropped_no_fields():
	"""A field missing from field_order is stranded at the bottom of the last tab."""
	order = _customer_field_order()
	assert len(order) == len(set(order)), "field_order contains duplicates"
	for required in ("basic_info", "customer_name", "customer_group", "industry",
	                 "naming_series", "section_break_objq", "connections_tab"):
		assert required in order, f"{required} was lost from the Customer field_order"


# ---------------------------------------------------------------------------
# reports
# ---------------------------------------------------------------------------


def test_new_reports_have_explicit_roles():
	for slug in ("account_data_quality", "closed_won_reconciliation", "named_account_targets"):
		path = ROOT / "crm_enhancements" / "report" / slug / f"{slug}.json"
		with open(path, encoding="utf-8") as fh:
			report = json.load(fh)
		roles = {r["role"] for r in report.get("roles", [])}
		assert roles, f"{slug} has no roles — it would default to All"
		assert "All" not in roles
		assert report["module"] == "CRM Enhancements"


def test_target_list_has_no_guessed_scale_filter():
	"""The event-planner scale taxonomy is undecided. A filter built against a
	guessed field would quietly match nothing."""
	assert "scale" not in data_quality.ASSIGNABLE_FIELDS
	report = (ROOT / "crm_enhancements" / "report" / "named_account_targets" / "named_account_targets.py")
	source = report.read_text(encoding="utf-8")
	tree = ast.parse(source)
	filterable = next(
		n for n in tree.body
		if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", None) == "FILTERABLE"
	)
	keys = [k.value for k in filterable.value.keys]
	assert "scale" not in keys, "a scale filter was added before the taxonomy was decided"
