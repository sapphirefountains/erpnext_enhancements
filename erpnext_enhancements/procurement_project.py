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
