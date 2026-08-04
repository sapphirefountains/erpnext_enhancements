# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Closed Won Reconciliation — won deals with no Project, and what might match.

The work queue for the 200 closed-won Opportunities carrying no
`Project.custom_opportunity` back-link. Until they are linked, revenue cannot be
attributed to the deal that won it.

Each row shows the opportunity plus its **best** candidate Project and the score
behind it. "Show candidates" on a row opens the full ranked list with the reasons
for each, because the top suggestion being wrong is expected and a reviewer needs
to see the runners-up without leaving the report.

**Nothing here links anything by itself.** Scoring ranks and explains; a person
presses the button. A wrong link corrupts revenue attribution, which is the exact
problem WP-1 exists to fix, so an auto-linker would manufacture the very defect
the programme is trying to remove. See `crm_enhancements/reconciliation.py`.

Newest first, because a 2014 deal has no bearing on current reporting and a 2026
one does. The default date filter starts at 2024-01-01 for the same reason — the
whole 2014-2026 backlog is available, but opening the report should show you the
part that matters.
"""

import frappe
from frappe import _
from frappe.utils import flt

from erpnext_enhancements.crm_enhancements.reconciliation import (
	score_candidates,
	unlinked_won_opportunities,
)


def execute(filters=None):
	filters = frappe._dict(filters or {})
	rows = unlinked_won_opportunities(filters)

	data = []
	for row in rows:
		candidates = score_candidates(row, limit=5)
		best = candidates[0] if candidates else None

		if filters.get("only_with_candidates") and not best:
			continue
		if filters.get("min_score") and (not best or best["score"] < flt(filters.min_score)):
			continue

		data.append({
			"opportunity": row["name"],
			"customer": row.get("customer"),
			"customer_name": row.get("customer_name"),
			"closed_on": row.get("custom_date_closed_won") or row.get("transaction_date"),
			"amount": flt(row.get("opportunity_amount")),
			"owner_user": row.get("opportunity_owner"),
			"candidate": best["project"] if best else "",
			"candidate_name": best["project_name"] if best else "",
			"score": best["score"] if best else 0,
			"why": "; ".join(best["reasons"]) if best else _("no project for this customer"),
			"candidate_count": len(candidates),
		})

	return get_columns(), data, _message(rows, data), get_chart(data)


def get_columns():
	return [
		{"label": _("Opportunity"), "fieldname": "opportunity", "fieldtype": "Link", "options": "Opportunity", "width": 160},
		{"label": _("Customer"), "fieldname": "customer", "fieldtype": "Link", "options": "Customer", "width": 200},
		{"label": _("Closed"), "fieldname": "closed_on", "fieldtype": "Date", "width": 110},
		{"label": _("Amount"), "fieldname": "amount", "fieldtype": "Currency", "width": 130},
		{"label": _("Best Candidate"), "fieldname": "candidate", "fieldtype": "Link", "options": "Project", "width": 160},
		{"label": _("Score"), "fieldname": "score", "fieldtype": "Int", "width": 70},
		{"label": _("Why"), "fieldname": "why", "fieldtype": "Data", "width": 380},
		{"label": _("Options"), "fieldname": "candidate_count", "fieldtype": "Int", "width": 80},
		{"label": _("Owner"), "fieldname": "owner_user", "fieldtype": "Link", "options": "User", "width": 160},
	]


def _message(rows, data):
	notes = [
		_("Select a row and press <b>Show Candidates</b> to see the full ranked list with reasons. "
		  "Nothing is linked automatically — a wrong link corrupts revenue attribution, which is the "
		  "problem this report exists to fix.")
	]
	no_candidate = len([d for d in data if not d["candidate"]])
	if no_candidate:
		notes.append(
			_("{0} of {1} shown have no candidate at all — their customer owns no Project. "
			  "Those need a Project creating, not linking.").format(no_candidate, len(data))
		)
	return "<br><br>".join(notes)


def get_chart(data):
	buckets = {_("Strong (80+)"): 0, _("Possible (50-79)"): 0, _("Weak (<50)"): 0, _("None"): 0}
	for row in data:
		score = row["score"]
		if not row["candidate"]:
			buckets[_("None")] += 1
		elif score >= 80:
			buckets[_("Strong (80+)")] += 1
		elif score >= 50:
			buckets[_("Possible (50-79)")] += 1
		else:
			buckets[_("Weak (<50)")] += 1
	labels = [k for k in buckets if buckets[k]]
	return {
		"data": {"labels": labels, "datasets": [{"name": _("Opportunities"), "values": [buckets[k] for k in labels]}]},
		"type": "percentage",
	}
