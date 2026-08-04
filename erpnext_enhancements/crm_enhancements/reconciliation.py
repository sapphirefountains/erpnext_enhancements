# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""WP-7: reconnecting closed-won Opportunities to the Projects that delivered them.

200 Opportunities are marked Closed Won with no linked Project. Until they are
linked, revenue cannot be attributed to the deal that won it — which is the exact
thing WP-1 exists to make possible, so this is the other half of the same job.

What the data actually looks like (2026-08-04):

* **200** closed-won opportunities with no `Project.custom_opportunity` back-link
* **199 of 200** are `opportunity_from = "Customer"`, so `party_name` is a real
  Customer id — matching by customer is reliable, not a fuzzy name join
* **138 of 200** belong to a customer who already owns at least one Project
* they span **2014-03-04 to 2026-07-10**, so most of the volume is history and
  only the recent tail affects live reporting

## The one rule: never auto-link

A wrong link corrupts revenue attribution, which is precisely the failure WP-1
was commissioned to fix — so an automated fuzzy match here would actively
manufacture the problem the programme is trying to solve. `score_candidates`
therefore *ranks* and explains; it never writes. `link_opportunity_to_project`
writes exactly one link, for one opportunity, chosen by a person.

The scoring exists to put the obvious matches at the top of a reviewer's screen,
not to make the decision. That is why every candidate carries a `reasons` list —
a reviewer should be able to see *why* something scored 90 and disagree with it.

## Scoring

Customer identity is a **precondition**, not a score component: a Project for a
different customer is not a weaker match, it is not a match. Within a customer,
candidates are scored on:

| Signal | Weight | Why |
|---|---|---|
| Date proximity | 50 | A project normally starts within weeks of the deal closing |
| Value proximity | 30 | Opportunity amount vs project estimated/actual value |
| Not already linked | 20 | A project already tied to another opportunity is a poor candidate |

A single 100 means "same customer, dates line up, values line up, project is
free". It still does not link anything.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate

#: Days between opportunity close and project start that still counts as a full
#: date match. A fountain build is quoted and scheduled within a quarter; beyond
#: that the two are probably different jobs for the same customer.
DATE_FULL_MATCH_DAYS = 45

#: Beyond this, date proximity contributes nothing.
DATE_NO_MATCH_DAYS = 365

#: Relative value difference that still counts as a full value match.
VALUE_FULL_MATCH_RATIO = 0.10

#: Beyond this relative difference, value proximity contributes nothing.
VALUE_NO_MATCH_RATIO = 1.00

WEIGHT_DATE = 50
WEIGHT_VALUE = 30
WEIGHT_UNLINKED = 20

#: Below this, a candidate is not worth showing — it is same-customer and nothing
#: else, which a reviewer can find themselves.
MIN_SCORE_TO_SUGGEST = 20

#: Roles allowed to create a link. Kept in code, not Settings, so it cannot be
#: widened from the UI: this writes revenue attribution.
LINKER_ROLES = {"System Manager", "Sales Manager", "Projects Manager"}


def unlinked_won_opportunities(filters=None):
	"""Closed-won Opportunities with no Project pointing at them."""
	filters = frappe._dict(filters or {})
	conditions = [
		"o.status = 'Closed Won'",
		"NOT EXISTS (SELECT 1 FROM `tabProject` p WHERE p.custom_opportunity = o.name)",
	]
	values = {}

	if filters.get("from_date"):
		conditions.append("o.transaction_date >= %(from_date)s")
		values["from_date"] = filters.from_date
	if filters.get("to_date"):
		conditions.append("o.transaction_date <= %(to_date)s")
		values["to_date"] = filters.to_date
	if filters.get("customer"):
		conditions.append("o.party_name = %(customer)s")
		values["customer"] = filters.customer

	return frappe.db.sql(
		f"""
		SELECT
			o.name,
			o.party_name AS customer,
			o.customer_name,
			o.opportunity_from,
			o.transaction_date,
			o.custom_date_closed_won,
			o.opportunity_amount,
			o.opportunity_owner
		FROM `tabOpportunity` o
		WHERE {" AND ".join(conditions)}
		ORDER BY COALESCE(o.custom_date_closed_won, o.transaction_date) DESC
		LIMIT 1000
		""",
		values,
		as_dict=True,
	)


def _candidate_projects(customer):
	"""Every Project for a customer, with what we need to score it."""
	if not customer:
		return []
	return frappe.db.sql(
		"""
		SELECT
			p.name,
			p.project_name,
			p.status,
			p.custom_opportunity,
			COALESCE(p.custom_project_start_date, p.expected_start_date, p.creation) AS start_date,
			COALESCE(p.custom_project_estimated_amount, p.total_sales_amount, 0) AS value
		FROM `tabProject` p
		WHERE p.customer = %(customer)s
		ORDER BY start_date DESC
		LIMIT 200
		""",
		{"customer": customer},
		as_dict=True,
	)


def score_candidates(opportunity, limit=5):
	"""Ranked candidate Projects for one Opportunity. Reads only — never writes.

	``opportunity`` is a dict as returned by :func:`unlinked_won_opportunities`.
	Returns the best ``limit`` candidates, each with a score and the reasons
	behind it, so a reviewer can disagree with the ranking on sight.
	"""
	customer = opportunity.get("customer")
	if not customer:
		# The single opportunity_from = "Lead" case in the backlog, plus anything
		# with a blank party. Nothing to match against; a reviewer handles it.
		return []

	close_date = opportunity.get("custom_date_closed_won") or opportunity.get("transaction_date")
	amount = flt(opportunity.get("opportunity_amount"))

	scored = []
	for project in _candidate_projects(customer):
		score = 0
		reasons = []

		date_points, date_reason = _date_score(close_date, project.get("start_date"))
		score += date_points
		if date_reason:
			reasons.append(date_reason)

		value_points, value_reason = _value_score(amount, flt(project.get("value")))
		score += value_points
		if value_reason:
			reasons.append(value_reason)

		if not project.get("custom_opportunity"):
			score += WEIGHT_UNLINKED
			reasons.append(_("not linked to another opportunity"))
		else:
			reasons.append(_("already linked to {0}").format(project["custom_opportunity"]))

		if score < MIN_SCORE_TO_SUGGEST:
			continue

		scored.append({
			"project": project["name"],
			"project_name": project.get("project_name"),
			"status": project.get("status"),
			"start_date": project.get("start_date"),
			"value": flt(project.get("value")),
			"already_linked": project.get("custom_opportunity") or "",
			"score": min(100, int(round(score))),
			"reasons": reasons,
		})

	scored.sort(key=lambda c: c["score"], reverse=True)
	return scored[: cint(limit) or 5]


def _date_score(close_date, start_date):
	"""Linear falloff from full marks at <= 45 days to nothing at >= 365."""
	if not (close_date and start_date):
		return 0, None
	try:
		gap = abs((getdate(start_date) - getdate(close_date)).days)
	except Exception:
		return 0, None

	if gap <= DATE_FULL_MATCH_DAYS:
		return WEIGHT_DATE, _("starts within {0} days of the close").format(gap)
	if gap >= DATE_NO_MATCH_DAYS:
		return 0, _("starts {0} days from the close").format(gap)

	span = DATE_NO_MATCH_DAYS - DATE_FULL_MATCH_DAYS
	fraction = 1 - ((gap - DATE_FULL_MATCH_DAYS) / span)
	return WEIGHT_DATE * fraction, _("starts {0} days from the close").format(gap)


def _value_score(opportunity_amount, project_value):
	"""Relative-difference falloff. Absent values score nothing rather than
	penalising — a blank amount is missing data, not evidence of a bad match."""
	if not (opportunity_amount and project_value):
		return 0, None

	difference = abs(project_value - opportunity_amount) / max(opportunity_amount, project_value)
	if difference <= VALUE_FULL_MATCH_RATIO:
		return WEIGHT_VALUE, _("value within {0}%").format(int(difference * 100))
	if difference >= VALUE_NO_MATCH_RATIO:
		return 0, _("value differs by {0}%").format(int(difference * 100))

	span = VALUE_NO_MATCH_RATIO - VALUE_FULL_MATCH_RATIO
	fraction = 1 - ((difference - VALUE_FULL_MATCH_RATIO) / span)
	return WEIGHT_VALUE * fraction, _("value differs by {0}%").format(int(difference * 100))


@frappe.whitelist()
def get_candidates(opportunity, limit=5):
	"""Whitelisted read: ranked candidates for one Opportunity, for the report UI."""
	if not frappe.has_permission("Opportunity", "read", doc=opportunity):
		frappe.throw(_("Not permitted to read this Opportunity."), frappe.PermissionError)

	row = frappe.db.get_value(
		"Opportunity",
		opportunity,
		["name", "party_name as customer", "customer_name", "opportunity_from",
		 "transaction_date", "custom_date_closed_won", "opportunity_amount"],
		as_dict=True,
	)
	if not row:
		return []
	return score_candidates(row, limit=limit)


@frappe.whitelist()
def link_opportunity_to_project(opportunity, project):
	"""Write one link, chosen by a person.

	Deliberately singular. There is no "link all high-confidence matches" here and
	there should not be: a wrong link corrupts revenue attribution, and a bulk
	action over a fuzzy score is the fastest way to produce hundreds of them.

	Refuses to overwrite an existing link — repointing a Project at a different
	Opportunity is a correction that should be made deliberately on the Project
	itself, not as a side effect of working through a reconciliation queue.
	"""
	if not LINKER_ROLES.intersection(frappe.get_roles()):
		frappe.throw(_("You are not permitted to link opportunities to projects."), frappe.PermissionError)

	if not frappe.db.exists("Opportunity", opportunity):
		frappe.throw(_("Opportunity {0} not found.").format(opportunity))
	if not frappe.db.exists("Project", project):
		frappe.throw(_("Project {0} not found.").format(project))

	frappe.has_permission("Project", "write", doc=project, throw=True)

	existing = frappe.db.get_value("Project", project, "custom_opportunity")
	if existing:
		if existing == opportunity:
			return {"status": "unchanged", "project": project, "opportunity": opportunity}
		frappe.throw(
			_("{0} is already linked to {1}. Change it on the Project itself if that is wrong.").format(
				project, existing
			)
		)

	# Guard the other direction too: two Projects claiming one Opportunity would
	# double-count its revenue, which is the same corruption from the other side.
	other = frappe.db.get_value("Project", {"custom_opportunity": opportunity}, "name")
	if other:
		frappe.throw(_("{0} is already linked to project {1}.").format(opportunity, other))

	# db.set_value: this is one field on one document, and a full save would fire
	# the project's process-step seeding and Drive provisioning for a back-link.
	# update_modified stays TRUE — a person did this and the trail should show it.
	frappe.db.set_value("Project", project, "custom_opportunity", opportunity)

	frappe.get_doc("Project", project).add_comment(
		"Comment",
		_("Linked to opportunity {0} via the Closed Won Reconciliation report.").format(opportunity),
	)

	return {"status": "linked", "project": project, "opportunity": opportunity}
