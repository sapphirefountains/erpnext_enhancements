# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Normalize on save: every new or edited value in a registered field is stored the one way.

Wired in ``hooks.py`` as ``doc_events["*"]["before_validate"]``, so it runs on the save of
every document and returns at once for any doctype the registry
(``utils/normalize_fields.py``) does not name. The rules are ``utils/normalize.py``'s; this
module only decides *when* they may write. ``docs/data-normalization.md`` is the prose version.

**When it writes, and why each condition is there:**

* **A draft save only — ``docstatus`` 0 and ``_action`` "save".** Frappe v16
  (``Document.run_before_save_methods``) runs ``before_validate`` for exactly two actions:
  ``save`` and ``submit``. ``check_docstatus_transition`` sets ``_action`` before it, and on a
  submit the incoming ``docstatus`` is already 1. Update-after-submit and cancel never reach
  ``before_validate`` at all — and if a future Frappe ran it there, the ``docstatus`` test
  still stops us, because a write to a field that is not ``allow_on_submit`` raises "Not
  allowed to change … after submission" in ``_validate_update_after_submit``. A submitted or
  cancelled record is a record of what was true; it is never rewritten (the registry's rule).
  The save that *creates* a record is a draft save (``insert`` sets ``_action`` "save" too).
* **Not while a site or app is being installed** (``frappe.flags.in_install``). Nothing an
  install saves was typed by a person, and ``doc_events`` fire during ERPNext's own test
  bootstrap before this app's custom fields exist (CLAUDE.md). A migrate, a patch or a Data
  Import that saves a document *is* normalized: storing the one form is harmless there and
  is what every later lookup expects.
* **Never when the kill switch is on.** ``bench --site <site> set-config
  disable_value_normalization 1`` turns this off live: ``frappe.conf`` is read per request.
  The same switch stops ``boot`` shipping the Desk registry, which turns the Desk display off.

**What it writes.** Only a value the rule is sure of: ``result.ok and result.changed``. A
value that cannot be normalized is left exactly as typed — never rejected, never guessed — and
the Data Normalization Preview report lists it for a person.

**It never raises.** A save must always go through; a normalization bug costs a tidy value,
never a record. Each field and the document as a whole are guarded, and an unexpected error
is logged at most once per request, by doctype, field and exception type only — never the
value, which is somebody's phone number or email address.
"""

import frappe

from erpnext_enhancements.utils import normalize
from erpnext_enhancements.utils.normalize_fields import (
	CHILD_TABLE_PARENTS,
	CHILD_TABLES,
	COUNTRY_FIELD,
	FIELDS,
	PHONE,
)

#: The site_config key that turns normalization off — this hook and the Desk display both.
KILL_SWITCH = "disable_value_normalization"

#: Parent doctypes that can carry a registered child row, computed once at import from plain
#: data (no meta), so the fast path below is one set lookup.
_CHILD_PARENTS = frozenset(parent for parents in CHILD_TABLE_PARENTS.values() for parent in parents)

#: Every doctype whose save can hold a registered value.
WATCHED_DOCTYPES = frozenset(FIELDS) | _CHILD_PARENTS

_LOGGED_FLAG = "sf_value_normalization_logged"


def is_disabled():
	"""True when ``disable_value_normalization`` is set in site_config. Never raises."""
	try:
		return bool((frappe.conf or {}).get(KILL_SWITCH))
	except Exception:
		return False


def desk_registry():
	"""``{"fields": {doctype: {fieldname: "phone"}}}`` for ``frappe.boot.sf_normalize``, or None.

	What ``public/js/normalize/desk_display.js`` matches a docfield against to show a phone as
	``(801) 555-0100`` and to normalize what is typed into it. Phones only: they are the one
	kind the Desk shows differently from how it is stored, and every other kind is stored
	exactly as it is shown. ``None`` when the kill switch is on, and the Desk then installs
	nothing. Never raises: boot runs on every Desk page.
	"""
	try:
		if is_disabled():
			return None
		fields = {}
		for doctype, registered in FIELDS.items():
			phones = {fieldname: kind for fieldname, kind in registered.items() if kind == PHONE}
			if phones:
				fields[doctype] = phones
		return {"fields": fields}
	except Exception:
		return None


def normalize_doc(doc, method=None):
	"""``before_validate`` for every doctype: store each registered value in its one form.

	O(1) for a doctype the registry does not cover; see the module docstring for when it
	writes. Never raises.
	"""
	try:
		if getattr(doc, "doctype", None) not in WATCHED_DOCTYPES:
			return
		if not _may_write(doc):
			return
		_normalize_fields(doc, doc.doctype)
		if doc.doctype in _CHILD_PARENTS:
			for row in _children(doc):
				row_doctype = getattr(row, "doctype", None)
				if row_doctype in CHILD_TABLES and row_doctype in FIELDS:
					_normalize_fields(row, row_doctype)
	except Exception as exc:
		_log_once(getattr(doc, "doctype", None), None, exc)


def _may_write(doc):
	if is_disabled():
		return False
	if getattr(frappe.flags, "in_install", None):
		return False
	try:
		docstatus = int(doc.get("docstatus") or 0)
	except (TypeError, ValueError):
		return False
	if docstatus != 0:
		return False
	# Absent on a document that never went through save(); "save" on a draft save or insert.
	return getattr(doc, "_action", None) in (None, "save")


def _children(doc):
	get_all_children = getattr(doc, "get_all_children", None)
	if not callable(get_all_children):
		return []
	return get_all_children() or []


def _normalize_fields(doc, doctype):
	country_field = COUNTRY_FIELD.get(doctype)
	country = doc.get(country_field) if country_field else None
	for fieldname, kind in FIELDS.get(doctype, {}).items():
		try:
			value = doc.get(fieldname)
			# A field this site does not have reads None, like an empty one: nothing to do.
			if not isinstance(value, str) or not value:
				continue
			result = normalize.normalize(kind, value, country)
			if result.ok and result.changed:
				doc.set(fieldname, result.value)
		except Exception as exc:
			_log_once(doctype, fieldname, exc)


def _log_once(doctype, fieldname, exc):
	"""One quiet Error Log per request, naming no value. Never raises."""
	try:
		if frappe.flags.get(_LOGGED_FLAG):
			return
		frappe.flags[_LOGGED_FLAG] = True
		where = f"{doctype}.{fieldname}" if fieldname else str(doctype)
		frappe.log_error(
			title="Value normalization skipped",
			message=(
				f"{where}: {type(exc).__name__}. The record saved with the value as typed; "
				"utils/normalize_hook.py logs this once per request and never the value."
			),
		)
	except Exception:
		pass
