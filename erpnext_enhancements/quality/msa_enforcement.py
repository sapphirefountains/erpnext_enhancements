# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Keeping a subcontractor agreement honest after it was signed — WI-075 sub-phase L.

`Project Contract.validate_msa_gate` already refuses a Statement of Work without a **Signed**
MSA. This adds the half it never had: whether that agreement is still **in force**, whether the
rates it published still match the ones the Statement of Work froze, and whether the purchase
orders placed against it name it at all.

Every judgement is in :mod:`erpnext_enhancements.quality.msa`, which imports nothing. This is the
half that queries and warns.

The one thing it will not do
-----------------------------

It never rewrites a Statement of Work's frozen rates. A signed agreement prints its own
`agreement_html`, so a rate re-read live from the MSA would silently change a document somebody
has already signed — and the difference between "the rate we agreed" and "the rate the schedule
says today" is exactly what a dispute is about. Drift is **reported**, for a person to decide.

Why unknown never blocks
-------------------------

None of the sixteen live contracts records an expiry date. Blocking on "no expiry recorded"
would stop every Statement of Work the company can currently issue, on the day this deploys, for
a gap in the record rather than a real lapse. So on every enforcement setting, only a *known,
past* expiry refuses; unknown is said out loud and allowed through.
"""

import frappe
from frappe import _
from frappe.utils import escape_html, nowdate

from erpnext_enhancements import email_style
from erpnext_enhancements.quality import msa
from erpnext_enhancements.quality.doctype.quality_settings.quality_settings import is_enabled

#: Roles told when an agreement is inside the escalation window rather than merely near expiry.
ESCALATION_ROLES = ("Production Manager", "President")

#: Cap on agreements examined per sweep. There will never be many.
SWEEP_LIMIT = 200


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def _setting(field, fallback):
	try:
		value = frappe.db.get_single_value("Quality Settings", field)
		return value if value not in (None, "") else fallback
	except Exception:
		return fallback


def windows():
	"""``(warn_days, escalate_days)``, clamped so a misconfigured pair cannot invert."""
	warn = int(_setting("msa_warn_days", msa.WARN_DAYS) or msa.WARN_DAYS)
	escalate = int(_setting("msa_escalate_days", msa.ESCALATE_DAYS) or msa.ESCALATE_DAYS)
	# An escalation window wider than the warning window would mean the gentler notice never
	# fires -- the state machine would jump straight to escalation and nobody would notice the
	# warning tier had stopped existing.
	return max(warn, escalate), min(warn, escalate)


def enforcement_mode():
	return _setting("msa_expiry_enforcement", "Warn") or "Warn"


# ---------------------------------------------------------------------------
# Project Contract
# ---------------------------------------------------------------------------


def on_contract_validate(doc, method=None):
	"""``Project Contract`` ``validate``: derive an MSA's expiry, and check a SOW against it.

	Runs *after* the controller's own validate, so `validate_msa_gate` has already stamped
	`msa_effective_date` and refused anything unsigned. Never raises except where enforcement is
	deliberately set to Block: a contract that cannot be saved is worse than one carrying a
	warning nobody has acted on yet.
	"""
	try:
		key = (doc.get("template_key") or "").strip()
		if key == "msa":
			_derive_expiry(doc)
			_warn_overlapping_rates(doc)
		elif key == "sow":
			_check_msa_still_in_force(doc)
			_report_rate_drift(doc)
	except frappe.ValidationError:
		raise
	except Exception:
		frappe.log_error(
			title="Quality: MSA checks failed",
			message=f"contract={doc.get('name')}\n\n{frappe.get_traceback()}",
		)


def _derive_expiry(doc):
	"""Stamp the expiry that is actually in force. Derived, never typed."""
	doc.msa_expiry_effective = msa.expires_on(
		doc.get("signed_on") or doc.get("contract_date"),
		doc.get("msa_term_months"),
		override=doc.get("msa_expires_on"),
	)


def _warn_overlapping_rates(doc):
	clashes = msa.overlapping_rate_lines(doc.get("rate_schedule"))
	if not clashes:
		return
	lines = "".join(
		"<li>{} / {} — rows {} and {}</li>".format(
			escape_html(c["classification"]), escape_html(c["rate_type"]), c["rows"][0], c["rows"][1]
		)
		for c in clashes
	)
	frappe.msgprint(
		_("Two rate lines are in force at the same time. Which one applies would depend on which "
		  "row a query read first, and a subcontractor invoice checked against it could be right "
		  "or wrong depending on nothing:")
		+ f"<ul>{lines}</ul>",
		title=_("Overlapping rates"),
		indicator="orange",
	)


def _check_msa_still_in_force(doc):
	if not doc.get("msa_contract"):
		return
	row = frappe.db.get_value(
		"Project Contract",
		doc.msa_contract,
		["name", "signed_on", "contract_date", "msa_term_months", "msa_expires_on"],
		as_dict=True,
	)
	if not row:
		return

	expiry = msa.expires_on(
		row.get("signed_on") or row.get("contract_date"),
		row.get("msa_term_months"),
		override=row.get("msa_expires_on"),
	)
	warn_days, escalate_days = windows()
	state, days = msa.expiry_state(expiry, nowdate(), warn_days, escalate_days)
	message = msa.gate_message(state, days, row["name"], expiry)
	if not message:
		return

	if msa.blocks_issue(state, enforcement_mode()):
		frappe.throw(escape_html(message), title=_("MSA expired"))
	frappe.msgprint(escape_html(message), title=_("Master agreement"), indicator="orange")


def _report_rate_drift(doc):
	"""Say where the frozen snapshot no longer matches the schedule. Never rewrites it."""
	if not doc.get("msa_contract"):
		return
	lines = frappe.get_all(
		"Contract Rate Line",
		filters={"parent": doc.msa_contract, "parenttype": "Project Contract"},
		fields=["classification", "rate_type", "rate", "effective_from", "effective_to"],
		ignore_permissions=True,
	)
	if not lines:
		return

	drift = msa.rate_drift(doc, lines, doc.get("contract_date") or nowdate())
	if not drift:
		return
	rows = "".join(
		"<li>{}: this SOW says <b>{:g}</b>, the agreement now publishes <b>{:g}</b></li>".format(
			escape_html(d["label"]), d["snapshot"], d["current"]
		)
		for d in drift
	)
	frappe.msgprint(
		_("These rates differ from what the master agreement publishes today:")
		+ f"<ul>{rows}</ul>"
		+ _("The figures above are this Statement of Work's own, frozen when it was issued, and "
			"nothing here has changed them — a signed agreement prints its own copy. Update them "
			"deliberately if the newer rates are the ones you meant."),
		title=_("Rates differ from the agreement"),
		indicator="orange",
	)


# ---------------------------------------------------------------------------
# Purchase Order
# ---------------------------------------------------------------------------


def on_purchase_order_validate(doc, method=None):
	"""``Purchase Order`` ``validate``: name the agreement when the supplier has one.

	Advisory by default, for the same reason the expiry check is: refusing to save a purchase
	order would stop somebody buying materials, and an order placed outside an agreement is a
	commercial problem to correct rather than an emergency to prevent.
	"""
	try:
		if not is_enabled():
			return
		supplier = doc.get("supplier")
		if not supplier or doc.get("custom_msa_contract"):
			return
		signed = _signed_msa_for(supplier)
		if not signed:
			return
		frappe.msgprint(
			_("{0} has a signed master agreement ({1}) and this order does not name it. An order "
			  "placed outside the agreement is an order at rates nobody agreed.").format(
				escape_html(supplier), ", ".join(signed[:3])
			),
			title=_("Master agreement not named"),
			indicator="orange",
		)
	except Exception:
		frappe.log_error(
			title="Quality: purchase order MSA check failed",
			message=f"po={doc.get('name')}\n\n{frappe.get_traceback()}",
		)


def _signed_msa_for(supplier):
	try:
		return frappe.get_all(
			"Project Contract",
			filters={
				"template_key": "msa",
				"party_type": "Supplier",
				"party": supplier,
				"status": "Signed",
				"docstatus": 1,
			},
			pluck="name",
			limit=5,
		)
	except Exception:
		return []


def orders_for_project(project):
	"""Purchase-order lines belonging to a project, by the union rule.

	`Purchase Order.project` and `Purchase Order Item.project` disagree on real data and **either
	one alone drops orders**: 40 of 148 live pending lines carry no row project, and 32 of those
	sit under a header that names the job. On PRJ-00566 a row-only match returns 37 rows where
	the union returns 63 — and it under-reports silently.

	The union is expressed in SQL here and in :func:`msa.order_project` in Python; the Python one
	is the tested definition and this must agree with it.
	"""
	return frappe.db.sql(
		"""
		select
			po.name as purchase_order, po.supplier, po.status, po.transaction_date,
			po.custom_msa_contract, po.custom_change_order,
			ifnull(nullif(poi.project, ''), po.project) as project,
			poi.item_code, poi.qty, poi.received_qty, poi.amount
		from `tabPurchase Order Item` poi
		join `tabPurchase Order` po on po.name = poi.parent
		where po.docstatus = 1
		  and ifnull(nullif(poi.project, ''), po.project) = %(project)s
		order by po.transaction_date desc, po.name
		""",
		{"project": project},
		as_dict=True,
	)


# ---------------------------------------------------------------------------
# The expiry sweep
# ---------------------------------------------------------------------------


def sweep_expiring_agreements():
	"""Daily: warn about master agreements coming up for renewal, escalate the near ones.

	Never raises. A missed notice is a nuisance; a scheduler job that dies takes every other
	daily job in its queue with it.
	"""
	if not is_enabled() or not _notifications_enabled():
		return
	try:
		_notify_expiring()
	except Exception:
		frappe.log_error(title="Quality: MSA expiry sweep failed", message=frappe.get_traceback())


def _notify_expiring():
	warn_days, escalate_days = windows()
	today = nowdate()
	warned, escalated, unknown = [], [], []

	for row in frappe.get_all(
		"Project Contract",
		filters={"template_key": "msa", "status": "Signed", "docstatus": 1},
		fields=["name", "party", "party_display", "signed_on", "contract_date",
				"msa_term_months", "msa_expires_on"],
		limit=SWEEP_LIMIT,
	):
		expiry = msa.expires_on(
			row.get("signed_on") or row.get("contract_date"),
			row.get("msa_term_months"),
			override=row.get("msa_expires_on"),
		)
		state, days = msa.expiry_state(expiry, today, warn_days, escalate_days)
		entry = {
			"name": row["name"],
			"party": row.get("party_display") or row.get("party") or "",
			"expiry": expiry,
			"days": days,
		}
		if state == msa.STATE_ESCALATE or state == msa.STATE_EXPIRED:
			escalated.append(entry)
		elif state == msa.STATE_WARN:
			warned.append(entry)
		elif state == msa.STATE_UNKNOWN:
			unknown.append(entry)

	if not (warned or escalated or unknown):
		return
	_send_digest(warned, escalated, unknown)


def _send_digest(warned, escalated, unknown):
	recipients = set()
	for role in ESCALATION_ROLES if escalated else ():
		recipients.update(_role_users(role))
	recipients.update(_role_users("Purchasing Manager"))
	recipients.update(_role_users("Quality Manager"))
	recipients.discard("Administrator")
	recipients.discard("Guest")
	if not recipients:
		return

	# Escalations lead, because a digest people skim is a digest whose first block has to be the
	# one that matters.
	blocks = []
	if escalated:
		blocks.append(
			email_style.callout(_("These need renewing now."), tone="danger")
			+ email_style.bullets([_line(e) for e in escalated])
		)
	if warned:
		blocks.append(
			email_style.h(_("Coming up"))
			+ email_style.bullets([_line(e) for e in warned])
		)
	if unknown:
		blocks.append(
			email_style.h(_("No expiry recorded"))
			+ email_style.p(
				_("Whether these are still in force cannot be checked. That is a gap in the "
				  "record rather than a lapse — set a term or an expiry date on each.")
			)
			+ email_style.bullets([f"{e['name']} — {e['party']}" for e in unknown])
		)

	subject = _("Subcontractor agreements: {0} need attention").format(
		len(escalated) + len(warned) + len(unknown)
	)
	try:
		frappe.sendmail(
			recipients=sorted(recipients),
			subject=subject,
			message=email_style.wrap("".join(blocks), title=subject, eyebrow=_("Quality")),
		)
	except Exception:
		frappe.log_error(title="Quality: MSA expiry digest failed", message=frappe.get_traceback())


def _line(entry):
	if entry["days"] is not None and entry["days"] < 0:
		return _("{0} — {1} — expired {2} days ago ({3})").format(
			entry["name"], entry["party"], abs(entry["days"]), entry["expiry"]
		)
	return _("{0} — {1} — expires in {2} days ({3})").format(
		entry["name"], entry["party"], entry["days"], entry["expiry"]
	)


def _role_users(role):
	try:
		holders = frappe.get_all(
			"Has Role", filters={"role": role, "parenttype": "User"}, pluck="parent"
		)
		if not holders:
			return []
		return frappe.get_all(
			"User",
			filters={"name": ["in", holders], "enabled": 1, "user_type": "System User"},
			pluck="name",
		)
	except Exception:
		return []


def _notifications_enabled():
	try:
		return bool(frappe.db.get_single_value("Quality Settings", "notifications_enabled"))
	except Exception:
		return False
