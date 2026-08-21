"""Project attribution on purchasing lines (WI-014 follow-through).

``Purchase Order Item.project`` is mandatory here — a Property Setter fixture
(`Purchase Order Item-project-reqd`) turned it on so material *and* subcontract
cost lands on the job it was bought for. Two things made that requirement
painful in day-to-day purchasing, and this module fixes the first of them:

* **ERPNext does not push the Purchase Order's header ``project`` down to its
  item rows.** So a PO that plainly names its job on the header still refused to
  save until someone re-picked the same project on every line — on prod, 44 of
  the 204 lines sitting under a PO *with* a header project were blank.
  :func:`cascade_project_to_items` fills the blanks from the header.
* **Non-job spend had nowhere to land** (office supplies, shop consumables,
  fuel). That is solved by the five standing overhead projects seeded in
  ``patches.seed_overhead_projects`` — the purchaser picks one on the PO header
  and, thanks to the cascade above, every line inherits it.

Blank rows only, never an overwrite: a PO that legitimately spans two jobs keeps
whatever each line already says. The client mirrors this in
``public/js/purchase_order_project.js`` — that is the copy that matters in the
Desk, because the mandatory check runs in the browser before the request is ever
sent. This server copy covers the paths no form script sees: the REST API, data
import, and documents mapped in from a Material Request.
"""

import frappe
from frappe.utils import flt

#: Project Type that marks a project as a non-job overhead bucket. Deliberately
#: its own type rather than reusing ``Internal`` — the 13 Internal projects are
#: specific R&D jobs (IDP000-IDP016) and overhead must stay separable from R&D in
#: reporting. It also keeps these out of the Projects Dashboard, which lists only
#: Build/Design/Events/Service/Delivery.
OVERHEAD_PROJECT_TYPE = "Overhead"


def cascade_project_to_items(doc, method=None):
	"""``before_validate`` hook: fill blank item-row projects from the header.

	Runs before Frappe's mandatory-field check (``run_before_save_methods`` fires
	``before_validate`` ahead of ``_validate``), so a row left blank by an API
	caller is filled rather than rejected.
	"""
	project = (doc.get("project") or "").strip()
	if not project:
		return

	for row in doc.get("items") or []:
		if not (row.get("project") or "").strip():
			row.project = project


def purchase_order_projects(doc):
	"""Every distinct project on one Purchase Order: header first, then row order.

	The header ``Purchase Order.project`` and the row ``Purchase Order Item.project`` can
	disagree, which is the reason :func:`cascade_project_to_items` exists and the reason every
	report here matches on the union rather than on either alone. Two things that print the
	job on the same document must not arrive at it two different ways, so the PDF filename and
	the print format both call this.

	**A list, not a string, and that is the load-bearing part.** On production today every
	submitted order resolves to exactly one project, and all 61 orders with a blank header
	have blank rows too — so a function returning a single value would look correct on every
	document that exists. It would be wrong the first time somebody buys for two jobs on one
	order, silently, by naming one of them. Returning the set makes the caller decide what to
	do about it where a reader can see the decision.

	Takes a document rather than a name: both callers already hold one, and re-reading the
	rows would be a second query for data in front of us.
	"""
	projects = []
	for value in [doc.get("project"), *(row.get("project") for row in doc.get("items") or [])]:
		value = (value or "").strip()
		if value and value not in projects:
			projects.append(value)
	return projects


#: A Purchase Order with nothing left to receive. Same rule as
#: ``api/pickup_routing.py::_is_outstanding`` — the *number*, not the label: ``Closed``
#: can hide an order whose goods never turned up, and ``To Bill`` means they are already
#: here. Kept identical on purpose; two different answers to "is there anything still to
#: collect" on the same Project form would be worse than either answer alone.
SETTLED_PO_STATUSES = ("Closed", "Delivered")


@frappe.whitelist()
def get_receivable_purchase_orders(project):
	"""Purchase Orders on this project that still have goods to receive.

	Backs the Project form's "+ Purchase Receipt" button, which until now opened a
	blank Purchase Receipt carrying nothing but the project — no supplier, no order, no
	lines — leaving the receiver to retype a delivery that ERPNext already knew about.

	Matches on the **union** of the header ``Purchase Order.project`` and the item-row
	``Purchase Order Item.project``, the same rule the pick-up routing map uses. The two
	agree on every order on this site today (70 of 70) — but only because
	:func:`cascade_project_to_items` fills blank item rows on save, and it fills *blanks
	only*, on *save only*. An order written before that hook existed, or re-pointed by a
	path that bypasses it, can still carry one and not the other; that is exactly the
	state the pick-up routing map found on 44 of 204 lines (CHANGELOG v1.190.0). The
	union costs one extra query and cannot be wrong.
	"""
	project = (project or "").strip()
	if not project:
		return []

	# Whitelisted and login-only, so gate on Project read — this returns every PO's
	# supplier and grand_total for the job, and without the check any authenticated user
	# could read them by project name. Same gate as the sibling pickup_routing endpoints.
	frappe.get_doc("Project", project).check_permission("read")

	names = set(
		frappe.get_all("Purchase Order", filters={"project": project}, pluck="name")
	)
	for row in frappe.get_all(
		"Purchase Order Item", filters={"project": project}, fields=["parent"], distinct=True
	):
		if row.get("parent"):
			names.add(row["parent"])

	if not names:
		return []

	orders = frappe.get_all(
		"Purchase Order",
		filters={"name": ["in", list(names)], "docstatus": 1},
		fields=["name", "supplier", "transaction_date", "per_received", "grand_total", "currency", "status"],
		order_by="transaction_date desc, name desc",
	)
	return [
		po
		for po in orders
		if po.status not in SETTLED_PO_STATUSES and flt(po.per_received) < 100
	]


@frappe.whitelist()
def get_overhead_projects():
	"""Return the active overhead buckets, alphabetically.

	The client script uses this to point a purchaser at the non-job buckets when
	a PO has no project at all. Returns ``[{name, project_name}, ...]`` — empty
	on a site where ``patches.seed_overhead_projects`` has not run.
	"""
	return frappe.get_all(
		"Project",
		filters={"project_type": OVERHEAD_PROJECT_TYPE, "status": "Active"},
		fields=["name", "project_name"],
		order_by="project_name asc",
	)
