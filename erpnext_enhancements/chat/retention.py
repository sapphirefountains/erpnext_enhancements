# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Making the two chat retention settings actually retain anything.

``Chat Settings`` has carried ``relay_job_retention_days`` and
``inbound_event_retention_days`` since Phase 1, both defaulting to 30, both with a
description explaining what they do. **Neither has ever deleted a row.** They are read in
exactly one place — ``validate()``, to check they are not negative — and nowhere else.

Meanwhile ``Chat Relay Job`` and ``Chat Inbound Event`` each carry a ``clear_old_logs(days)``
staticmethod whose docstring says it exists so Frappe's log clearing can call it. Frappe never
calls it, because nothing ever registered either doctype with ``Log Settings``. So the
situation this module fixes is: two operator controls that do nothing, two retention
implementations nothing invokes, and two queue tables with no upper bound.

They are empty today (measured on production 2026-08-11: 0 rows each) because the chat
feature is still in dry-run pilot. This is therefore preventative — it is the last quiet
moment to fix it, which is exactly when a retention rule should be added rather than after
somebody notices the table.

--------------------------------------------------------------------------------------
Why this is NOT done with ``default_log_clearing_doctypes``
--------------------------------------------------------------------------------------

That hook is the obvious answer and it is the wrong one here, for a reason worth writing
down because it is not obvious until you read Frappe's source.

The hook is a **static** number. These two tables have a **dynamic** one on a settings form.
Declaring both means two sources of truth for one fact, and they disagree the moment anybody
touches the form — which is precisely the lying-settings-field trap that ``presence.py``'s
constants were fixed for in v1.270.0. Worse, ``add_default_logtypes`` **appends any
hook-declared doctype whose row is absent**, and it runs from ``LogSettings.validate``, so a
row deleted to mean "never purge this" is silently recreated on the next save. Under the hook,
``0 = never`` could never stick.

So the settings field is the single control, and this module is what makes the framework
agree with it: on every ``Chat Settings`` save, and on every migrate, the ``Logs To Clear``
rows are rewritten to match. Frappe's ``daily_maintenance`` then does the actual deleting
through the ``clear_old_logs`` methods that already exist.

``0`` means **never purge**, matching the convention the rest of that form already uses
(``message_retention_days`` and ``hard_delete_after_days`` both document 0 as "never"), and it
means it durably: the row is removed and nothing puts it back.

Indentation is tabs, per ``CLAUDE.md`` and the Frappe convention this package follows.
"""

from __future__ import annotations

from typing import Any, Final

import frappe
from frappe.utils import cint

#: The chat tables whose retention is driven by a ``Chat Settings`` field, and which field
#: drives each. Closed: a doctype added here must also implement ``clear_old_logs(days)``, or
#: Frappe's ``remove_unsupported_doctypes()`` deletes the row on the next daily run and
#: retention silently stops — which looks exactly like this module not working.
MANAGED_DOCTYPES: Final[dict[str, str]] = {
	"Chat Relay Job": "relay_job_retention_days",
	"Chat Inbound Event": "inbound_event_retention_days",
}

_LOG_SETTINGS: Final[str] = "Log Settings"
_CHILD_FIELD: Final[str] = "logs_to_clear"


def desired_days(settings: Any) -> dict[str, int]:
	"""What each managed table's retention should be, read off ``Chat Settings``.

	**Pure given a settings-shaped object**, so the mapping can be exercised without a bench.
	Values are floored at 0 — a negative is meaningless and ``validate`` already refuses one,
	but this runs from ``after_migrate`` too, where the document may predate that rule.
	"""
	out: dict[str, int] = {}
	for doctype, fieldname in MANAGED_DOCTYPES.items():
		out[doctype] = max(0, cint(getattr(settings, fieldname, None) or 0))
	return out


def sync(settings: Any | None = None) -> dict[str, Any]:
	"""Rewrite the ``Logs To Clear`` rows for the chat tables to match ``Chat Settings``.

	Returns a summary of what changed, so the migrate banner and a manual ``bench execute``
	both say something true rather than "ok".

	**Never raises.** It is called from ``Chat Settings.on_update`` — where an exception would
	stop somebody saving a kill switch during an incident — and from ``after_migrate``, where
	one would brick the deploy pipeline. A failure here costs retention, which is a slow
	problem; both of those are fast ones.
	"""
	summary: dict[str, Any] = {"set": {}, "removed": [], "unchanged": []}
	try:
		if not frappe.db.exists("DocType", "Chat Settings"):
			return summary

		doc = settings if settings is not None else frappe.get_cached_doc("Chat Settings")
		wanted = desired_days(doc)

		log_settings = frappe.get_doc(_LOG_SETTINGS)
		rows = list(getattr(log_settings, _CHILD_FIELD, None) or [])
		by_doctype = {row.ref_doctype: row for row in rows}
		changed = False

		for doctype, days in wanted.items():
			existing = by_doctype.get(doctype)

			if days <= 0:
				# 0 means never, and it has to MEAN never. Removing the row is the only way to
				# say that to Frappe — there is no disable flag on `Logs To Clear` — and it is
				# durable here only because this module does not declare these doctypes in
				# `default_log_clearing_doctypes`; if it did, `add_default_logtypes` would put
				# the row straight back on the next Log Settings save.
				if existing is not None:
					log_settings.remove(existing)
					summary["removed"].append(doctype)
					changed = True
				continue

			if existing is None:
				log_settings.append(_CHILD_FIELD, {"ref_doctype": doctype, "days": days})
				summary["set"][doctype] = days
				changed = True
			elif cint(existing.days) != days:
				existing.days = days
				summary["set"][doctype] = days
				changed = True
			else:
				summary["unchanged"].append(doctype)

		if changed:
			# `Log Settings.validate` runs `remove_unsupported_doctypes()` on save, which drops
			# any row whose controller lacks `clear_old_logs`. That is the check we WANT to run:
			# if either chat controller ever loses the method, the row disappears here rather
			# than three months later when somebody wonders why the table is large.
			log_settings.save(ignore_permissions=True)

		return summary
	except Exception:
		frappe.log_error(
			title="chat log retention sync failed",
			message=(
				"Could not reconcile Logs To Clear with Chat Settings. Chat Relay Job and "
				"Chat Inbound Event will keep growing until this succeeds. Re-run with: bench "
				"--site <site> execute erpnext_enhancements.chat.retention.sync"
			),
		)
		return summary


def ensure_chat_log_retention() -> None:
	"""``after_migrate`` / ``after_install`` backstop.

	Needed because an existing site's ``Chat Settings`` may never be saved again — the fields
	have held their defaults since Phase 1 — so hanging this only off ``on_update`` would mean
	the rows appear on no site that does not happen to edit the form.

	Registered on both hooks for the usual reason: ``after_migrate`` does not run during
	``bench install-app``.
	"""
	summary = sync()
	if summary["set"] or summary["removed"]:
		print(
			f"chat log retention: set={summary['set'] or 'none'} "
			f"removed={summary['removed'] or 'none'}"
		)


def current_rows() -> dict[str, int]:
	"""What ``Log Settings`` currently holds for the managed tables. Diagnostic only.

	Reads the document rather than querying the child table, so it owes the raw-SQL guard
	nothing and cannot drift from what Frappe itself would load.
	"""
	try:
		log_settings = frappe.get_doc(_LOG_SETTINGS)
	except Exception:
		return {}
	return {
		row.ref_doctype: cint(row.days)
		for row in (getattr(log_settings, _CHILD_FIELD, None) or [])
		if row.ref_doctype in MANAGED_DOCTYPES
	}
