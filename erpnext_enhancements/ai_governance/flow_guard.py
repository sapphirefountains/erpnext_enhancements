"""Frappe Flow and the Studio User role stay off until this site opts in (v1.836.0).

frappe 16.60 added two modules: **Flow**, an AI assistant that runs inside the framework, and
**Studio**, a page builder. Neither goes through this app's controls:

- **Flow's assistant reads and writes records as the person chatting with it, in-process.** Its
  record reads call frappe's query functions directly, so they never pass the field-level read
  checks this app puts on the HTTP read routes (``fieldlevel_filters.py``, ``fieldlevel_read.py``),
  which only wrap requests. Its writes are approved, if at all, by that same person inside Flow's
  chat panel (a session can be set to approve everything, and a scheduled Flow Trigger always
  does), so they never reach the AI write gate (``assistant_tools/_gate.py``) or the AI Action Log.
- **A Studio User can publish pages, scripts included, on this site's address**, pages open to
  guests among them, which makes the role as strong as System Manager in practice.

A Flow chat cannot run until the site has a **Flow Model**, and a model needs a **Flow Provider**
(the account and API key of an LLM vendor). Once an enabled model exists, frappe's own
``after_migrate`` builds its built-in Assistant agent with every built-in tool, writes included.
So this module refuses, while **ERPNext Enhancements Settings > AI Governance > Allow Frappe Flow
and Studio User** is off (the default):

1. creating a Flow Provider or a Flow Model (``refuse_flow_setup``, ``before_insert``), which keeps
   every Flow chat and trigger inert; and
2. giving a user, or a Role Profile, the **Flow User** or **Studio User** role
   (``refuse_new_flow_roles``, ``validate``). A record that already holds one keeps it and saves
   normally; only an addition is refused.

Turning the switch off again does not remove a model, provider or role added while it was on.

It never runs during ``bench migrate``, an install or a patch: a hook that raises there aborts the
migrate, which on this repo is the deploy. Nothing frappe ships creates a model or grants these
roles during a migrate today, so nothing is lost; such a case is logged instead.

Read at frappe v16.60.2: ``frappe/flow/tools/builtins.py`` (the tools), ``frappe/flow/utils/
safe_exec.py`` (``execute``'s sandbox), ``frappe/flow/daemon/callback.py`` (tools run as the session
owner), ``frappe/flow/assistant/assistant.py`` (``sync_builtin_assistant`` needs an enabled model),
``frappe/flow/permissions.py`` and the Flow and Studio DocType permissions.
"""

import frappe

SETTINGS_DOCTYPE = "ERPNext Enhancements Settings"
SETTING_FIELD = "allow_frappe_flow"

#: Without one of these, no Flow chat or trigger can run: an agent needs a model, a model a provider.
SETUP_DOCTYPES = ("Flow Provider", "Flow Model")

#: frappe's roles for the two modules. Flow User opens Flow's chat; Studio User writes Studio pages.
GUARDED_ROLES = frozenset({"Flow User", "Studio User"})

#: The doctypes whose ``roles`` table grants a role: a user directly, or every user on a profile.
ROLE_HOLDERS = ("User", "Role Profile")

#: Where a raise would abort ``bench migrate`` (the deploy) or an install.
_DEPLOY_FLAGS = ("in_migrate", "in_install", "in_patch")


def flow_allowed() -> bool:
	"""Whether the site opted in. Off when the setting is unset, unreadable or not yet a column.

	``get_single_value`` (never ``get_value("Singles", ...)``: tabSingles has no ``creation``
	column). A never-saved field reads None, which is off, the safe answer and the default.
	"""
	try:
		return bool(int(frappe.db.get_single_value(SETTINGS_DOCTYPE, SETTING_FIELD) or 0))
	except Exception:
		return False


def _deploying() -> bool:
	flags = getattr(frappe, "flags", None)
	return any(getattr(flags, flag, False) for flag in _DEPLOY_FLAGS)


def _note_skipped(what: str) -> None:
	"""A deploy went ahead with something the switch would refuse: say so without failing it."""
	try:
		frappe.logger("erpnext_enhancements").warning(
			f"flow_guard: {what} during migrate or install, while {SETTING_FIELD} is off; left in place"
		)
	except Exception:
		pass


def _refuse(message: str) -> None:
	frappe.throw(
		message
		+ " "
		+ frappe._(
			"A System Manager can allow it in ERPNext Enhancements Settings > AI Governance > "
			"Allow Frappe Flow and Studio User."
		),
		frappe.PermissionError,
		title=frappe._("Frappe Flow is off on this site"),
	)


def refuse_flow_setup(doc, method=None) -> None:
	"""``before_insert`` on Flow Provider and Flow Model: refused while the switch is off."""
	if doc.doctype not in SETUP_DOCTYPES or flow_allowed():
		return
	if _deploying():
		_note_skipped(f"a {doc.doctype} was created")
		return
	_refuse(
		frappe._(
			"Frappe Flow's assistant reads and writes records outside this app's AI write gate and "
			"field-level read checks, so it cannot be connected to a model here."
		)
	)


def _roles(doc) -> set:
	return {row.get("role") for row in (doc.get("roles") or []) if row.get("role")}


def added_guarded_roles(doc) -> set:
	"""The Flow and Studio roles this save adds; a role the record held before is not counted."""
	guarded = _roles(doc) & GUARDED_ROLES
	if not guarded:
		return set()
	before = doc.get_doc_before_save() if hasattr(doc, "get_doc_before_save") else None
	return guarded - (_roles(before) if before else set())


def refuse_new_flow_roles(doc, method=None) -> None:
	"""``validate`` on User and Role Profile: no new Flow User or Studio User while the switch is off.

	Reads the setting only when the save adds one of the two roles, so every other User save (and
	ERPNext's test bootstrap, before this app's fields exist) costs one set intersection.
	"""
	if doc.doctype not in ROLE_HOLDERS:
		return
	added = added_guarded_roles(doc)
	if not added or flow_allowed():
		return
	if _deploying():
		_note_skipped(f"{', '.join(sorted(added))} was given on a {doc.doctype}")
		return
	_refuse(
		frappe._(
			"{0} cannot be given here: Flow's assistant works outside this app's AI write gate and "
			"field-level read checks, and a Studio User can publish pages with scripts on this site."
		).format(", ".join(sorted(added)))
	)
