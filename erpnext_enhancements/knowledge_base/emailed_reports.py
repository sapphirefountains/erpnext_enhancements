# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""A Knowledge Base report is emailed only by, and as, someone who could run it (WI-080 PR 4 review).

v16 checks a report's roles and its ``ref_doctype`` against the **session** user on every run
(``desk/query_report.py:43-53``). That is the whole gate in the Desk and through an AI tool's
``generate_report``. An Auto Email Report's scheduled send is the one path where the session user is
not the person:

* ``AutoEmailReport.validate`` checks the report count, the addresses, the format and the mandatory
  filters, and never whether anyone may open the report (``auto_email_report.py:75-79``). Its
  DocPerm gives create to System Manager and Report Manager, and on this site Report Manager comes
  with the Design, Finance, Production and Sales Team profiles as well.
* ``send_daily`` runs from the scheduler, whose user is Administrator, and enqueues
  ``process_auto_email_report`` (``auto_email_report.py:327-359``); the job runs as the user who
  enqueued it (``utils/background_jobs.py:179``, ``:252``). ``send_monthly`` calls ``send()``
  directly. ``get_report_content`` passes the Auto Email Report's ``user`` to ``report.get_data``
  (``:140-156``), which uses it for User Permission filters only.
* So ``get_report_doc`` checks the roles against Administrator, who holds every role
  (``permissions.py:546-547``), and the rows go to whatever addresses the Auto Email Report lists.

Without this guard, anyone with Report Manager could have the Integrity report's rows mailed daily
to any address: version names, review states, and owner, submitter and approver ids, which the
Version's DocPerm withholds from everyone without a KB role. (Never draft text: the report selects
none.) The whitelisted *Send Now* and *Download* run as the person and are refused by v16 already.

:func:`guard_auto_email_report` (``doc_events["Auto Email Report"]["before_validate"]``) refuses an
Auto Email Report on a Knowledge Base report, or on a Custom Report built on one (v16 runs a Custom
Report as the report it refers to, ``query_report.get_reference_report``), unless **both** the person
saving it and the user it runs as hold one of that report's roles. Both, because the ``user`` field
can name someone else: a Report Manager naming a KB Approver there is the same leak.

It runs on every Auto Email Report save, so it returns after one ``get_value`` for any other report,
and it never raises for an unrelated row. ``before_validate`` because v16 runs it on every save before
it looks at ``flags.ignore_validate`` (``model/document.py:1403-1408``).

What it does not stop, on purpose: a KB Approver emailing the report to addresses of their choosing,
which is a trusted role's own export (so is a Prepared Report an approver makes by hand, whose stored
result System Managers can read). And an Auto Email Report saved while both people held the role
keeps sending after the role is removed: when a KB role is taken away, delete the Auto Email Reports
on the two reports that name that person (the README's "Roles" section).
"""

import frappe
from frappe import _

#: The module whose reports this guards: both of PR 4's.
MODULE = "Knowledge Base"

#: How far to follow Custom Reports. v16 follows the chain without a bound; nobody builds one this
#: long, and a cycle must not hang a save.
MAX_REFERENCE_DEPTH = 5


def guard_auto_email_report(doc, method=None):
	"""Refuse an Auto Email Report on a Knowledge Base report for anyone who could not run it. See
	the module docstring."""
	report = knowledge_base_report(_read(doc, "report"))
	if not report:
		return

	allowed = set(
		frappe.get_all("Has Role", filters={"parenttype": "Report", "parent": report}, pluck="role") or []
	)
	people = [frappe.session.user, _read(doc, "user")]
	refused = [
		user for user in dict.fromkeys(p for p in people if p) if not allowed & set(frappe.get_roles(user))
	]
	if not refused:
		return

	frappe.throw(
		_(
			"The {0} report can be emailed only by someone who may run it ({1}), and only as such a "
			"person. {2} may not. v16 sends an emailed report as Administrator, so its own role check "
			"would not stop it."
		).format(report, ", ".join(sorted(allowed)) or _("no one"), ", ".join(refused)),
		frappe.PermissionError,
		title=_("Knowledge base reports"),
	)


def knowledge_base_report(name):
	"""The Knowledge Base report ``name`` runs as, following Custom Reports the way v16 does, or None."""
	seen = set()
	while name and name not in seen and len(seen) < MAX_REFERENCE_DEPTH:
		seen.add(name)
		row = frappe.db.get_value("Report", name, ["module", "report_type", "reference_report"], as_dict=True)
		if not row:
			return None
		if row.get("module") == MODULE:
			return name
		if row.get("report_type") != "Custom Report":
			return None
		name = row.get("reference_report")
	return None


def _read(obj, key):
	getter = getattr(obj, "get", None)
	value = getter(key) if callable(getter) else getattr(obj, key, None)
	return value or ""
