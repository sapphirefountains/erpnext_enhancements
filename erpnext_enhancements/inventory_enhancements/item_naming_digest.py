# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Monday's list of last week's new Items that fail the naming rules.

Nik, 2026-09-24 (TASK-2026-02238): the weekly list goes to the Purchasing Agent, who owns
the Item Naming Schema. It is the other half of :mod:`item_naming_guard`. The guard refuses
two defects at the moment of saving; everything else is advice, and advice that nobody
reads is the state the catalogue was in when the SOP was written. This is the reader.

Scheduled ``0 7 * * 1`` (Monday 07:00, site time) in ``hooks.py``.

**One definition of compliant.** Rows are judged by :func:`item_naming_rules.audit` over
the whole catalogue, the same call the Item Naming Audit report and the KPI make, and then
restricted to the week's items (:func:`item_naming_rules.restrict_to`). Auditing the week
alone would miss a new item named exactly like an old one. A second, simpler definition
here would disagree with the report by a row one week and nobody could say which was right.

**Recipients** are ``Inventory Scanner Settings.naming_digest_recipients``: email addresses
separated by commas or new lines. The field has no default. ``patches/seed_naming_digest_recipient``
writes the Purchasing Agent's address once, only where the field has never been stored.
Blank sends nothing, and so does a week with nothing to report: an email that says "all
clear" every Monday trains its reader to delete it unread, and the one week it matters goes
the same way.
"""

import re

import frappe
from frappe import _
from frappe.utils import add_days, format_date, get_fullname, get_url_to_form, get_url_to_report, now_datetime

from erpnext_enhancements import email_style
from erpnext_enhancements.inventory_enhancements import item_naming_rules as rules

SETTINGS_DOCTYPE = "Inventory Scanner Settings"
RECIPIENTS_FIELD = "naming_digest_recipients"

#: How far back "new" reaches. Seven days, run weekly: every item appears in exactly one
#: digest unless a run is missed, and a missed run is visible as a missing Monday email.
WINDOW_DAYS = 7

#: Rows in the email. Past this it says how many more there are and links the report.
DIGEST_ROW_LIMIT = 50

REPORT_NAME = "Item Naming Audit"

_SEPARATORS = re.compile(r"[,;\n\r]+")


def send_weekly_digest():
	"""Email the week's failing new Items to the configured recipients. Never raises."""
	try:
		recipients = parse_recipients(frappe.db.get_single_value(SETTINGS_DOCTYPE, RECIPIENTS_FIELD))
	except Exception:
		# The field arrives with a migrate. Read before that migrate finishes, it does not exist
		# and get_single_value raises; that is a week without a digest, not an error to page on.
		return
	if not recipients:
		return

	now = now_datetime()
	since = add_days(now, -WINDOW_DAYS)
	try:
		from erpnext_enhancements.inventory_enhancements import item_naming

		recent = frappe.get_all(
			"Item",
			filters={"creation": [">=", since]},
			fields=["item_code", "owner"],
			order_by="creation asc",
		)
		if not recent:
			return
		corpus, _meta = item_naming.read_corpus()
		audit_rows = rules.audit(corpus, brands=item_naming.read_brands())
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Item naming digest: audit failed")
		return

	owners = {row.get("item_code"): row.get("owner") for row in recent}
	failing = select_rows(audit_rows, owners)
	if not failing:
		return
	counts = week_counts(audit_rows, owners)

	week_ending = format_date(now)
	shown = failing[:DIGEST_ROW_LIMIT]
	body = email_style.kpis(
		[
			{"label": _("New Items"), "value": str(counts["new"])},
			{"label": _("Pass"), "value": str(counts["passing"])},
			{"label": _("To fix"), "value": str(len(failing)), "tone": "warning"},
		]
	)
	body += email_style.p(
		_(
			"Items created in the seven days to {0} that do not meet the Item Naming Schema. Each can "
			"be corrected in place on the Item form, where Naming → Check naming lists the same "
			"findings. From {1}, every new Item is expected to pass (POL-0602)."
		).format(week_ending, format_date(rules.NAMING_GO_LIVE))
	)
	if len(failing) > DIGEST_ROW_LIMIT:
		body += email_style.note(
			_("Showing the first {0} of {1}, worst first.").format(DIGEST_ROW_LIMIT, len(failing))
		)
	body += email_style.table(
		[_("Item"), _("Item Name"), _("Created by"), _("What to fix")],
		table_rows(shown, owners, url_for=lambda code: get_url_to_form("Item", code), name_for=_full_name),
	)
	body += email_style.button(get_url_to_report(REPORT_NAME), _("Open the Item Naming Audit"))

	try:
		frappe.sendmail(
			recipients=recipients,
			subject=_("Item naming: {0} new Item(s) to fix, week ending {1}").format(
				len(failing), week_ending
			),
			message=email_style.wrap(
				body,
				title=_("New Items to rename"),
				eyebrow=_("Week ending {0}").format(week_ending),
			),
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Item naming digest: send failed")


def _full_name(user):
	if not user:
		return ""
	try:
		return get_fullname(user) or user
	except Exception:
		return user


# --- pure helpers (no I/O; tests/test_item_naming_digest.py) ---------------------


def parse_recipients(value):
	"""Email addresses from a comma-, semicolon- or newline-separated string, in order.

	Duplicates are dropped case-insensitively, and anything without an ``@`` or with a space
	in it is dropped rather than handed to ``sendmail``, which would refuse the whole send over
	one typo.
	"""
	out = []
	seen = set()
	for part in _SEPARATORS.split(value or ""):
		address = part.strip()
		if not address or "@" not in address or " " in address:
			continue
		key = address.lower()
		if key in seen:
			continue
		seen.add(key)
		out.append(address)
	return out


def select_rows(audit_rows, codes):
	"""The audit rows for ``codes`` that do not PASS, tombstones excluded, worst first.

	``codes`` is any container of item codes (the week's new items). A ``(deleted)`` code is
	a QuickBooks migration artefact, not somebody's new item, so it is never listed.
	"""
	rows = [
		row
		for row in rules.restrict_to(audit_rows, codes)
		if not row.get("is_tombstone") and row.get("verdict") != rules.VERDICT_PASS
	]
	rows.sort(key=rules.audit_sort_key)
	return rows


def week_counts(audit_rows, codes):
	"""``{"new", "passing"}`` for the week's live items, from :func:`item_naming_rules.summarise`."""
	summary = rules.summarise(rules.restrict_to(audit_rows, codes))
	return {"new": summary["live_rows"], "passing": summary["passing_live_rows"]}


def what_to_fix(findings):
	"""The STOP and FIX messages, worst first, as one cell of text.

	NOTEs are left out: they never move the verdict, and the one that exists
	(``code_name_disagrees``) has a known false-positive class. A digest that lists them
	teaches its reader to skim past the ones that matter.
	"""
	order = {rules.STOP: 0, rules.FIX: 1}
	kept = [f for f in findings or () if f.get("severity") in order]
	kept.sort(key=lambda f: order[f["severity"]])
	return " ".join(str(f.get("message") or f.get("code") or "") for f in kept)


def table_rows(rows, owners, url_for, name_for):
	"""Four cells per row: a link to the Item, its name, who created it, what to fix.

	The first cell is a ``(url, label)`` pair, which the email table renders as a link.
	"""
	out = []
	for row in rows:
		code = row.get("item_code") or ""
		out.append(
			[
				(url_for(code), code),
				row.get("item_name") or "",
				name_for(owners.get(code)) if owners else "",
				what_to_fix(row.get("findings")),
			]
		)
	return out
