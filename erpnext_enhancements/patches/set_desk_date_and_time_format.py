"""Desk dates read ``10/14/2026`` and Desk times read ``14:00`` (form kit P2-A).

Nik's call: the site's date format moves from ``mm-dd-yyyy`` to ``mm/dd/yyyy`` and its time
format from ``HH:mm:ss`` to ``HH:mm``. Frappe v16 offers no 12-hour Desk time (only
``HH:mm:ss`` and ``HH:mm``), so Desk times stay 24-hour; print, email and SMS show
``10/14/2026 2:00 PM`` through ``utils/normalize.py`` instead (``utils/date_display.py``).

--------------------------------------------------------------------------------------
Why this is more than ``set_single_value``
--------------------------------------------------------------------------------------

The two formats are read from **two different places**, and a patch that writes only one
leaves the site disagreeing with itself:

* The Desk's JavaScript reads ``frappe.sys_defaults.date_format``, which ``frappe/boot.py``
  fills from ``frappe.defaults.get_defaults()``, i.e. **``tabDefaultValue``**, not from
  System Settings. Writing only ``tabSingles`` changes nothing a person sees.
* Server-side ``formatdate`` / ``format_datetime`` read ``get_locale_value``, which tries the
  user's Language record and then ``frappe.db.get_default(key)``, which is
  ``tabDefaultValue`` again.

System Settings' own ``on_update`` is what keeps the two in step: ``set_defaults()`` calls
``frappe.db.set_default`` for each changed field and ``clear_system_settings_cache()`` drops
the cached document. This patch reproduces exactly those two steps by hand.

**It does not save the Single through its controller**, which is the other way to get them.
Saving System Settings deletes and re-inserts every one of its field rows, and
``SystemSettings.validate`` enforces unrelated rules: a password policy needing a score, a
session-expiry format, the SMS gateway needed before two-factor by SMS, the "at least one
social login before disabling the password login" rule, and ``validate_snapshot_reports``,
which dereferences ``get_doc_before_save()``. A site whose settings tripped any of them
would fail this patch, and a patch that raises aborts ``bench migrate``, which on this repo
is the deploy. Writing the two fields and their defaults directly cannot trip any of that.

--------------------------------------------------------------------------------------
Only a value that is still the old one
--------------------------------------------------------------------------------------

A field is changed only while it still holds the format this patch replaces. Anything else
(someone already chose ``dd/mm/yyyy``, or the field has no stored value) is left alone, so
the patch never overrides a decision. System Settings has a ``tabSingles`` row for the
field whenever it holds the old value, so ``set_single_value`` cannot half-materialise a
never-saved Single here.

The same swap is applied to the stragglers that would otherwise keep a person on the old
format: per-user ``tabDefaultValue`` rows (``Language.update_user_defaults`` writes them) and
Language records, **only where they hold the old value**.

Never raises, and safe twice: the second run finds the new values and changes nothing.
"""

import frappe

SETTINGS = "System Settings"

#: (field, old value, new value). Each is handled on its own, so one that has already moved
#: does not stop the other.
CHANGES = (
	("date_format", "mm-dd-yyyy", "mm/dd/yyyy"),
	("time_format", "HH:mm:ss", "HH:mm"),
)

_GLOBAL_PARENT = "__default"


def execute():
	try:
		set_desk_date_and_time_format()
	except Exception:
		_log_quietly("set_desk_date_and_time_format")


def set_desk_date_and_time_format() -> list[str]:
	"""Move each field that still holds its old value; returns the field names changed."""
	changed = []
	for field, old, new in CHANGES:
		try:
			if _switch(field, old, new):
				changed.append(field)
		except Exception:
			_log_quietly(f"set_desk_date_and_time_format: {field}")

	if changed:
		try:
			# The boot payload, the defaults and the System Settings document are all cached.
			frappe.clear_cache()
		except Exception:
			_log_quietly("set_desk_date_and_time_format: clear_cache")
	return changed


def _switch(field: str, old: str, new: str) -> bool:
	if frappe.db.get_single_value(SETTINGS, field) != old:
		return False

	# 1. The document (what server code and the System Settings form read).
	frappe.db.set_single_value(SETTINGS, field, new)
	# 2. The global default (what the Desk's boot payload reads); System Settings'
	#    own on_update does this for every changed field.
	frappe.db.set_default(field, new)

	# 3. Stragglers holding the old value, each best effort and on its own.
	try:
		frappe.db.sql(
			"update `tabDefaultValue` set defvalue = %s where defkey = %s and defvalue = %s and parent != %s",
			(new, field, old, _GLOBAL_PARENT),
		)
	except Exception:
		_log_quietly(f"set_desk_date_and_time_format: user defaults for {field}")
	try:
		frappe.db.set_value("Language", {field: old}, field, new)
	except Exception:
		_log_quietly(f"set_desk_date_and_time_format: Language {field}")
	return True


def _log_quietly(where: str) -> None:
	try:
		frappe.log_error(title="Desk date format patch failed", message=f"{where}\n{frappe.get_traceback()}")
	except Exception:
		pass
