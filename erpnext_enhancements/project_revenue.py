"""ERPNext's revenue, cost and margin totals on Project: Accounts Manager and System Manager only (v1.845.3).

**The rule** (owner decision, 2026-11-19, applied to the KPI dashboards in v1.836.6): a figure
that states the company's revenue or margin goes only to a caller holding Accounts Manager or
System Manager. On 2026-11-27 the owner applied it to the eight totals ERPNext keeps on every
Project, cost totals included ("All eight"): ``PROTECTED_FIELDS`` below.

**How.** Each of the eight sits at ``PERMLEVEL`` (3) through a fixture Property Setter
(``Project-<field>-permlevel``), and ``patches/grant_project_revenue_visibility`` gives the two
roles ``read`` and ``write`` at that level. Level 1 on Project is no use: ERPNext grants Desk
User read there, which every desk user holds. Level 2 is already the labor-forecast level
(``patches/grant_labor_forecast_visibility``, held by five cost roles). Level 3 was free on
production (Custom DocPerm and DocPerm read 2026-11-27). Frappe then strips the eight from every
document it sends a caller without level-3 read (``getdoc``, ``frappe.client.get``, REST reads,
save responses), and this app's ``fieldlevel_read`` and ``fieldlevel_filters`` already hold
version diffs, write responses and filters to the same levels.

**Write too**, because ERPNext computes these values on every save (``Project.validate`` ->
``update_costing``). Frappe resets a field above the caller's write level to its stored value
in ``validate_higher_perm_levels``, which runs *before* ``validate``, so ERPNext's recomputation
still lands on a save by any Projects User. The background writers (``db_set_cost``,
``update_project``) write straight to the database and never meet the reset at all.

**ERPNext's Overview panel** (``project_overview.profitability``) already returns nothing when
``get_permitted_fields`` hides billed or margin from the caller, so the level is enough there.

**ERPNext's Costing tab is not covered by the level**, and that is what this module is for.
``get_project_costing`` does not read the stored totals: it works billed, Sales Order value,
cost and margin out of the invoices, orders, timesheets and stock entries themselves, and hands
them to every user who can read the Project. The tab is hidden on Sapphire's form and its island
loads only when on screen, so nobody meets it by accident, but anyone who can read a Project can
call it. The override below answers it unchanged for a caller who can read the protected fields
and, for anyone else, sends the same payload with every money section set to None and flagged in
``errors``: the shape ERPNext's own island already renders when a section fails, so nothing
throws.

**An override matches a name, the whitelist matches a function.** ERPNext's own test module
(``costing/test_project_costing.py``) imports ``get_project_costing``, so the original is also
reachable under that module's dotted path, which no override matches. ``seal_original`` (a
``before_request`` hook) takes the original off frappe's whitelist while it is overridden, as
``fieldlevel_read.seal_wrapped_originals`` does for frappe's own functions. In-process calls are
unaffected; only HTTP dispatch checks the whitelist.

``tests/test_project_revenue_fields.py`` holds the fixture rows, the patch, the hook wiring and
the payload rule.
"""

import frappe

PARENT = "Project"
#: The permission level the eight sit at. Nobody else holds it on production.
PERMLEVEL = 3
#: Who may read them: kpi_dashboards.money.REVENUE_ROLES. The patch restates them so it never
#: imports this module; the test holds the three equal.
ROLES = ("Accounts Manager", "System Manager")
#: ERPNext v16.60.0's money totals on Project. The owner chose all eight (2026-11-27).
#: Project Dollar Amount, Estimated Cost, Bid Cost and Materials Budget stay open on purpose:
#: project managers work from them, and none states revenue or margin.
PROTECTED_FIELDS = (
	"total_sales_amount",
	"total_billable_amount",
	"total_billed_amount",
	"total_costing_amount",
	"total_purchase_cost",
	"total_consumed_material_cost",
	"gross_margin",
	"per_gross_margin",
)

ORIGINAL = "erpnext.projects.doctype.project.costing.project_costing.get_project_costing"
#: The Costing payload's sections that state an amount. ``reports`` (links to reports the
#: caller may open) and the metadata keys carry none and are left as they are.
COSTING_MONEY_SECTIONS = ("summary", "monthly", "billing", "drivers", "rate_gaps")

_SEALED = set()
_seal_failure_logged = False


def can_read_protected(user=None):
	"""True when ``user`` (default: the session user) may be given the protected totals.

	Asks Frappe's own field rule, so it follows the DocPerm rows rather than a role list here:
	Administrator always, anyone else when one of their roles reads level 3 on Project.
	"""
	user = user or frappe.session.user
	if user == "Administrator":
		return True
	from frappe.model import get_permitted_fields

	permitted = set(get_permitted_fields(PARENT, user=user, permission_type="read"))
	return all(field in permitted for field in PROTECTED_FIELDS)


def without_money(payload):
	"""The Costing payload with every money section set to None and flagged as failed.

	Pure. ``payload`` is ERPNext's ``ProjectCosting.build()`` result and is not changed.
	"""
	if not isinstance(payload, dict):
		return payload
	out = dict(payload)
	errors = dict(out.get("errors") or {})
	for section in COSTING_MONEY_SECTIONS:
		out[section] = None
		errors[section] = 1
	out["errors"] = errors
	out["restricted"] = 1
	return out


@frappe.whitelist(methods=["GET"])
def get_project_costing(project):
	"""ERPNext's ``get_project_costing``, without its amounts for a caller who may not read them.

	ERPNext's own access check runs first, inside the original, so a caller who cannot read the
	Project is refused exactly as before. Parameters mirror ERPNext's; annotations are not copied,
	as in ``fieldlevel_read``.
	"""
	original = frappe.get_attr(ORIGINAL)
	payload = original(project)
	if can_read_protected():
		return payload
	return without_money(payload)


def seal_original():
	"""``before_request``: take ERPNext's own ``get_project_costing`` off the whitelist while it is overridden.

	Only while this site overrides it, so the canonical route keeps resolving to the wrapper above;
	it goes back on when the override is gone. Never raises, because a before_request hook that
	raises fails every request on the site; if it cannot run, the alias stays callable, which is
	ERPNext's stock behaviour, and the failure is logged once per process.
	"""
	global _seal_failure_logged
	try:
		fn = frappe.get_attr(ORIGINAL)
		if frappe.override_whitelisted_method(ORIGINAL) != ORIGINAL:
			if fn in frappe.whitelisted:
				frappe.whitelisted.discard(fn)
				_SEALED.add(fn)
		elif fn in _SEALED:
			frappe.whitelisted.add(fn)
			_SEALED.discard(fn)
	except Exception:
		if not _seal_failure_logged:
			_seal_failure_logged = True
			try:
				frappe.log_error(
					title="Project revenue: sealing get_project_costing failed",
					message=frappe.get_traceback(),
					defer_insert=True,
				)
			except Exception:
				pass
