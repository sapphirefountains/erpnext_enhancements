# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Call Log rows for the missed-calls report, the Inquiries tab and the alerts (v1.757.0).

Times are ``creation``, site-local: Triton writes ``start_time`` in UTC, six or seven hours off the
site's clock (see ``api._calls``). A call is **first-time** when no earlier incoming call came from its
number (``rules.phone_key``).

``respect_permissions`` reads through ``frappe.get_list``, so the Missed Calls report shows a person
only the Call Logs they may already open, and never widens Call Log's own permissions. The alerts
job, which runs as Administrator, reads everything.
"""

import datetime

import frappe

from erpnext_enhancements.marketing.performance import rules


def call_rows(start, end, respect_permissions=False):
	"""Every call from ``start`` to ``end`` (dates, inclusive): ``{name, at, type, status, number, caller,
	first_time}``. ``number`` is the other party: ``from`` for an incoming call, ``to`` for an outgoing one.

	``from`` and ``to`` are SQL reserved words, so the rows are read with backticked SQL; with
	``respect_permissions`` the names the viewer may open come from ``frappe.get_list`` and the rest are
	dropped, so Call Log's own rules decide and nothing here widens them.
	"""
	if not frappe.db.exists("DocType", "Call Log"):
		return []
	until = end + datetime.timedelta(days=1)
	rows = frappe.db.sql(
		"""select name, creation, type, status, `from` as from_number, `to` as to_number, customer
		from `tabCall Log` where creation >= %s and creation < %s order by creation""",
		(start, until),
		as_dict=True,
	)
	if respect_permissions:
		allowed = set(
			frappe.get_list(
				"Call Log",
				filters=[["creation", ">=", start], ["creation", "<", until]],
				pluck="name",
				limit_page_length=0,
			)
		)
		rows = [r for r in rows if r.name in allowed]
	seen = {
		rules.phone_key(n)
		for (n,) in frappe.db.sql(
			"""select `from` from `tabCall Log` where type = 'Incoming' and creation < %s""", (start,)
		)
		if rules.phone_key(n)
	}
	out = []
	for r in rows:
		incoming = r.type == "Incoming"
		number = r.get("from_number") if incoming else r.get("to_number")
		key = rules.phone_key(number)
		first = bool(incoming and key and key not in seen)
		if incoming and key:
			seen.add(key)
		out.append(
			{
				"name": r.name,
				"at": r.creation,
				"type": r.type,
				"status": r.status,
				"number": number,
				"caller": r.customer,
				"first_time": first,
			}
		)
	return out
