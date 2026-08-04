# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Named Account Targets — build an outreach list from the account book.

Filter the customer base by industry, territory, customer type, account status and
value stream, and export the result. The point is a list somebody can actually
work from: a name, who owns it, how to reach them, and when it was last touched.

**Sorted by staleness, not alphabetically.** "Last activity, oldest first" puts
the accounts nobody has spoken to in two years at the top, which is the list worth
having. An alphabetical export is a directory, not a target list.

## The scale filter is deliberately absent

Leadership agreed to classify event planners by **scale** rather than geography —
the reasoning being that a "local event planner" label breaks the moment the
company operates outside Utah, and location should come from the territory field
and the address instead. Where that taxonomy lives (Industry, Customer Group, or a
new Select) and what its values are is **not yet decided**, so there is no scale
filter here.

It is a one-line addition to `FILTERABLE` once the decision lands. Building it
against a guessed field would mean either migrating the data twice or shipping a
filter that quietly matches nothing.

## Data quality caveat, stated in the report itself

1,292 of 1,621 customers have no industry and 1,118 have no value stream. An
industry-filtered list therefore misses most of the book, and the message at the
top of the report says so with live numbers rather than leaving the user to
discover it. Work `Account Data Quality` first.
"""

import frappe
from frappe import _

#: Scalar filters, mapped to their column. A closed set — these interpolate into
#: SQL, so the allowlist is the control.
FILTERABLE = {
	"industry": "c.industry",
	"territory": "c.territory",
	"customer_type": "c.customer_type",
	"customer_group": "c.customer_group",
	"custom_account_status": "c.custom_account_status",
	# "scale": pending the event-planner taxonomy decision — see the docstring.
}


def execute(filters=None):
	filters = frappe._dict(filters or {})
	data = get_data(filters)
	return get_columns(), data, _message(filters, data), None


def get_columns():
	return [
		{"label": _("Account"), "fieldname": "name", "fieldtype": "Link", "options": "Customer", "width": 230},
		{"label": _("Industry"), "fieldname": "industry", "fieldtype": "Link", "options": "Industry Type", "width": 170},
		{"label": _("Territory"), "fieldname": "territory", "fieldtype": "Link", "options": "Territory", "width": 140},
		{"label": _("Type"), "fieldname": "customer_type", "fieldtype": "Data", "width": 110},
		{"label": _("Status"), "fieldname": "custom_account_status", "fieldtype": "Data", "width": 100},
		{"label": _("Value Streams"), "fieldname": "value_streams", "fieldtype": "Data", "width": 160},
		{"label": _("Owner"), "fieldname": "account_manager", "fieldtype": "Link", "options": "User", "width": 160},
		{"label": _("Phone"), "fieldname": "phone", "fieldtype": "Data", "width": 130},
		{"label": _("Email"), "fieldname": "email", "fieldtype": "Data", "width": 200},
		{"label": _("Last Activity"), "fieldname": "last_activity", "fieldtype": "Date", "width": 120},
		{"label": _("Projects"), "fieldname": "projects", "fieldtype": "Int", "width": 90},
	]


def get_data(filters):
	conditions = ["c.disabled = 0"]
	values = {}

	for key, column in FILTERABLE.items():
		if filters.get(key):
			conditions.append(f"{column} = %({key})s")
			values[key] = filters.get(key)

	if filters.get("value_stream"):
		# SINGULAR child table, PLURAL master — see crm_enhancements/README.md.
		conditions.append(
			"EXISTS (SELECT 1 FROM `tabValue Stream` vs "
			"WHERE vs.parent = c.name AND vs.parenttype = 'Customer' "
			"  AND vs.value_stream = %(value_stream)s)"
		)
		values["value_stream"] = filters.value_stream

	if filters.get("no_recent_activity_days"):
		conditions.append(
			"(c.custom_last_activity_date IS NULL "
			" OR c.custom_last_activity_date < DATE_SUB(CURDATE(), INTERVAL %(stale_days)s DAY))"
		)
		values["stale_days"] = int(filters.no_recent_activity_days)

	return frappe.db.sql(
		f"""
		SELECT
			c.name,
			c.industry,
			c.territory,
			c.customer_type,
			c.custom_account_status,
			c.account_manager,
			c.custom_accounts_phone_number AS phone,
			c.custom_accounts_email_address AS email,
			c.custom_last_activity_date AS last_activity,
			(SELECT GROUP_CONCAT(vs.value_stream ORDER BY vs.idx SEPARATOR ', ')
			   FROM `tabValue Stream` vs
			  WHERE vs.parent = c.name AND vs.parenttype = 'Customer') AS value_streams,
			(SELECT COUNT(*) FROM `tabProject` p WHERE p.customer = c.name) AS projects
		FROM `tabCustomer` c
		WHERE {" AND ".join(conditions)}
		ORDER BY c.custom_last_activity_date IS NOT NULL, c.custom_last_activity_date ASC, c.name ASC
		LIMIT 2000
		""",
		values,
		as_dict=True,
	)


def _message(filters, data):
	notes = [_("{0} accounts. Oldest activity first — the top of this list is who nobody has "
	           "spoken to. Use Menu → Export for a working copy.").format(len(data))]

	if filters.get("industry"):
		missing = frappe.db.count("Customer", {"industry": ["in", ["", None]], "disabled": 0})
		if missing:
			notes.append(
				_("<b>Filtering by industry misses {0} accounts</b> that have no industry set at all. "
				  "Work the Account Data Quality report before trusting this as a complete list.").format(missing)
			)
	if filters.get("value_stream"):
		notes.append(_("1,118 of 1,621 accounts carry no value stream — same caveat."))

	notes.append(
		_("<i>No scale filter yet: the event-planner scale taxonomy (large corporate / small corporate / "
		  "small family) has not been decided, so there is no field to filter on. Location comes from "
		  "Territory and the address, by design.</i>")
	)
	return "<br><br>".join(notes)
