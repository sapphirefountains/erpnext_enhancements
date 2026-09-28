# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Knowledge Articles Due for Review (WI-080 PR 4): which published articles someone has to check.

POL-0001 has every article reviewed on a schedule (six months unless the author set another
interval). ``publish`` and ``confirm_still_accurate`` restart ``review_by``; this lists every
**published** article whose ``review_by`` has passed or falls within the window (30 days by
default), plus any with no review date at all, with the process owner who has to look at it. The
process owner, or a KB Approver, then presses **Confirm Still Accurate** on the article (the review
clock restarts) or **Start Revision** (the article changes); both are PR 3's buttons, so nothing
here writes.

Roles: KB Author and KB Approver (the report JSON). ``ref_doctype`` is Knowledge Article, which
every staff user may ``report`` on; the role list is what keeps the report to the KB roles.

It reads the published article only, never the Version doctype, in one bound query whose column
list is :data:`reporting.DUE_FIELDS`. The rules are ``reporting.review_due`` and
``reporting.due_rows``, pure and tested bench-free (``tests/test_knowledge_base_entry_points.py``).
"today" is the site's date (``nowdate``), the calendar ``review_by`` was computed in.
"""

import frappe
from frappe import _
from frappe.utils import getdate, nowdate

from erpnext_enhancements.knowledge_base import constants, reporting

#: The one query. The status is a bound parameter; the columns are ``reporting.DUE_FIELDS``, and the
#: test pins the two agree. Rows are ordered and filtered in Python (``reporting.due_rows``).
ARTICLES_SQL = (
	"select " + ", ".join(reporting.DUE_FIELDS) + " from `tabKnowledge Article` "
	"where status = %(status)s order by name"
)


def execute(filters=None):
	filters = filters or {}
	days = reporting.within_days(filters.get("within_days"))
	articles = frappe.db.sql(ARTICLES_SQL, {"status": reporting.ARTICLE_PUBLISHED}, as_dict=True)
	rows = reporting.due_rows(
		articles, getdate(nowdate()), days, process_owner=filters.get("process_owner") or None
	)
	message = None
	if not rows:
		message = _("No published article is due for review in the next {0} days.").format(days)
	return get_columns(), rows, message


def get_columns():
	return [
		{
			"fieldname": "kb_number",
			"label": _("KB Number"),
			"fieldtype": "Link",
			"options": constants.ARTICLE_DOCTYPE,
			"width": 110,
		},
		{"fieldname": "title", "label": _("Title"), "fieldtype": "Data", "width": 260},
		{"fieldname": "state", "label": _("State"), "fieldtype": "Data", "width": 130},
		{"fieldname": "review_by", "label": _("Review By"), "fieldtype": "Date", "width": 110},
		{"fieldname": "days_left", "label": _("Days Left"), "fieldtype": "Int", "width": 90},
		{
			"fieldname": "process_owner",
			"label": _("Process Owner"),
			"fieldtype": "Link",
			"options": "User",
			"width": 200,
		},
		{"fieldname": "department_block", "label": _("Department"), "fieldtype": "Data", "width": 150},
		{
			"fieldname": "review_every_months",
			"label": _("Review Every (Months)"),
			"fieldtype": "Int",
			"width": 110,
		},
		{
			"fieldname": "last_reviewed_on",
			"label": _("Last Reviewed On"),
			"fieldtype": "Datetime",
			"width": 160,
		},
		{
			"fieldname": "last_reviewed_by",
			"label": _("Last Reviewed By"),
			"fieldtype": "Link",
			"options": "User",
			"width": 200,
		},
	]
