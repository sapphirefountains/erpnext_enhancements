# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Remove what v1.570.0's Desk-native Review Room left behind (WI-079 slice 5, v1.571.0).

v1.570.0 stored every concept screen in Long Text fields of four doctypes (Design Option,
Design Screen, Design Part, Design Review Track) and showed them on a Desk Page. Frappe v16
sanitizes Long Text on save, so every screen lost its CSS `gap` and most lost their SVG geometry
on the way in. v1.571.0 stores a review's content as one private JSON File and shows it at
/review, outside the Desk. This patch removes the old storage and the Desk furniture:

* the four content DocTypes, their rows and their tables. `remove_orphan_doctypes()` deletes the
  DocType rows by itself on migrate, but nothing drops a table (see `delete_chat_module`);
* the Design Review Lifecycle Workflow, which v1.571.0's fixtures no longer manage, and the four
  Workflow Action Masters and three Workflow States it added, each only when nothing else uses it.
  Open and Closed are generic names that another workflow may use, so the check is not optional;
* Page review-room and Workspace Design Reviews, and the Desk icon and sidebar v16 makes for it;
* every Design Review with no content File and nothing recorded on it. On production that is the
  one review imported under v1.570.0: verified 2026-10-01 with no participants, votes, verdicts,
  notes or decisions. Its content has nowhere to live now, so it is re-imported at /review.

`post_model_sync`. Every step is guarded and the patch cannot raise: a raising patch aborts
`bench migrate`, which on this repo is the deploy. Safe to run twice.
"""

import frappe

OLD_DOCTYPES = ("Design Option", "Design Screen", "Design Part", "Design Review Track")
WORKFLOW = "Design Review Lifecycle"
ACTIONS = ("Open for Review", "Close Review", "Record as Decided", "Reopen Review")
STATES = ("Open", "Closed", "Decided")
PAGE = "review-room"
WORKSPACE = "Design Reviews"
ACTIVITY = ("Design Note", "Design Vote", "Design Verdict", "Design Decision")


def execute():
	for step in (_doctypes, _workflow, _desk, _empty_reviews):
		try:
			step()
		except Exception:
			frappe.db.rollback()
			print(f"rebuild_design_review_storage: {step.__name__} failed; skipped")
			frappe.log_error(title=f"rebuild_design_review_storage: {step.__name__}")
		else:
			frappe.db.commit()


def _doctypes():
	for doctype in OLD_DOCTYPES:
		table = f"tab{doctype}"
		if frappe.db.table_exists(doctype):
			rows = frappe.db.sql(f"select count(*) from `{table}`")[0][0]
			print(f"rebuild_design_review_storage: dropping {table} ({rows} rows)")
		if frappe.db.exists("DocType", doctype):
			frappe.delete_doc("DocType", doctype, force=True, ignore_permissions=True, ignore_missing=True)
		frappe.db.sql_ddl(f"drop table if exists `{table}`")


def _unused(link_doctype, field, value):
	return not frappe.db.exists(link_doctype, {field: value})


def _workflow():
	if frappe.db.exists("Workflow", WORKFLOW):
		frappe.delete_doc("Workflow", WORKFLOW, force=True, ignore_permissions=True)
		print(f"rebuild_design_review_storage: deleted Workflow {WORKFLOW}")
	for action in ACTIONS:
		if frappe.db.exists("Workflow Action Master", action) and _unused("Workflow Transition", "action", action):
			frappe.delete_doc("Workflow Action Master", action, force=True, ignore_permissions=True)
	for state in STATES:
		if (
			frappe.db.exists("Workflow State", state)
			and _unused("Workflow Document State", "state", state)
			and _unused("Workflow Transition", "state", state)
			and _unused("Workflow Transition", "next_state", state)
		):
			frappe.delete_doc("Workflow State", state, force=True, ignore_permissions=True)


def _desk():
	if frappe.db.exists("Page", PAGE):
		frappe.delete_doc("Page", PAGE, force=True, ignore_permissions=True)
	if frappe.db.exists("Workspace", WORKSPACE):
		frappe.delete_doc("Workspace", WORKSPACE, force=True, ignore_permissions=True)
	# v16 makes a Desktop Icon and a Workspace Sidebar for a workspace. Neither exists on v15, and the
	# sidebar's name is the workspace's on the sites checked; anything else is left for a person.
	for doctype, filters in (("Workspace Sidebar", {"name": WORKSPACE}), ("Desktop Icon", {"label": WORKSPACE})):
		if not frappe.db.exists("DocType", doctype):
			continue
		for name in frappe.get_all(doctype, filters=filters, pluck="name"):
			frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)


def _empty_reviews():
	if not frappe.db.table_exists("Design Review") or not frappe.db.has_column("Design Review", "content_file"):
		return
	for name in frappe.get_all("Design Review", filters={"content_file": ["is", "not set"]}, pluck="name"):
		if any(frappe.db.exists(doctype, {"review": name}) for doctype in ACTIVITY if frappe.db.table_exists(doctype)):
			print(f"rebuild_design_review_storage: kept {name}: it has activity but no content")
			continue
		frappe.delete_doc("Design Review", name, force=True, ignore_permissions=True)
		print(f"rebuild_design_review_storage: deleted empty review {name}; re-import it at /review")
