"""Frappe Flow and the Studio User role stay off until this site opts in (v1.836.0).

frappe 16.60 added two modules: **Flow**, an AI assistant that runs inside the framework, and
**Studio**, a page builder. Neither goes through this app's controls:

- **Flow's assistant reads and writes records as the person chatting with it.** This app's
  field-level read checks (``fieldlevel_filters.py``, ``fieldlevel_read.py``) are not applied to
  what Flow reads; frappe's own permission checks are. Its writes are approved, if at all, by that
  same person inside Flow's chat panel (a session can be set to approve everything, and a scheduled
  Flow Trigger always does), so they never reach the AI write gate (``assistant_tools/_gate.py``) or
  the AI Action Log.
- **A Studio User can publish pages, scripts included, on this site's address**, pages open to
  guests among them.

A Flow chat cannot run until the site has an enabled **Flow Model**, and a model needs a **Flow
Provider** (the account and API key of an LLM vendor). frappe's own ``after_migrate`` builds its
built-in Assistant agent, every built-in tool included, only once an enabled model exists. So while
**ERPNext Enhancements Settings > AI Governance > Allow Frappe Flow and Studio User** is off (the
default):

1. **Flow Provider and Flow Model** (``guard_flow_setup``, ``validate``): creating one, or saving
   one enabled, is refused. During ``bench migrate``, an install or a patch, in the setup wizard and
   on a site whose setup is not complete, nothing is refused: the record is saved **disabled**
   instead (a raise there would abort the deploy or the wizard). An existing record saved disabled
   is left alone.
2. **Flow User and Studio User** (``drop_new_flow_roles``, ``validate`` on User and Role Profile):
   a role this save adds is **taken off the record, never refused**. frappe's setup wizard gives the
   first user every role, erpnext gives Administrator every role before tests, and an LDAP sign-in
   syncs roles on login: refusing any of those saves would fail the wizard, the test bench or the
   sign-in over an optional role. A signed-in person editing in the Desk is told which role was not
   given. A role the record already held is kept.

Every record changed this way is logged once to the app log, without its contents.

Known limit: frappe's Flow setup re-enables an existing model with ``frappe.db.set_value``, which
runs no hooks. It only applies to a model that already exists, and no model can be saved enabled
while the switch is off. Turning the switch off again does not disable what was added while it was
on. The guard matches doctype and role names, not permissions: a Custom DocPerm on a Flow or Studio
doctype for another role is not caught here (the fixture test refuses one in the repo).

Read at frappe v16.60.2: ``frappe/flow/tools/builtins.py``, ``frappe/flow/utils/safe_exec.py``,
``frappe/flow/daemon/callback.py``, ``frappe/flow/assistant/assistant.py``, ``frappe/flow/api/
setup.py``, ``frappe/desk/page/setup_wizard/setup_wizard.py`` and the Flow and Studio DocTypes.
"""

import frappe

SETTINGS_DOCTYPE = "ERPNext Enhancements Settings"
SETTING_FIELD = "allow_frappe_flow"

#: Without an enabled one of these, no Flow chat or trigger can run.
SETUP_DOCTYPES = ("Flow Provider", "Flow Model")

#: frappe's roles for the two modules. Flow User opens Flow's chat; Studio User writes Studio pages.
GUARDED_ROLES = frozenset({"Flow User", "Studio User"})

#: The doctypes whose ``roles`` table grants a role: a user directly, or every user on a profile.
ROLE_HOLDERS = ("User", "Role Profile")

#: Where a raise would abort ``bench migrate`` (the deploy) or an install.
_DEPLOY_FLAGS = ("in_migrate", "in_install", "in_patch")


def flow_allowed() -> bool:
	"""Whether the site opted in. Off when the value is unset, unreadable or never saved.

	Read straight from ``tabSingles`` with bound parameters: ``get_single_value`` looks the field up
	in the DocType's meta first and calls ``frappe.throw`` (into ``message_log``) when a patch runs
	before the DocType sync has added it. Never ``get_value("Singles", ...)``: tabSingles has no
	``creation`` column to order by.
	"""
	try:
		rows = frappe.db.sql(
			"select `value` from `tabSingles` where `doctype` = %s and `field` = %s",
			(SETTINGS_DOCTYPE, SETTING_FIELD),
		)
		return bool(rows) and str(rows[0][0] or "").strip() in ("1", "1.0")
	except Exception:
		return False


def _flag(name: str) -> bool:
	return bool(getattr(getattr(frappe, "flags", None), name, False))


def _deploying() -> bool:
	return any(_flag(flag) for flag in _DEPLOY_FLAGS)


def _setting_up() -> bool:
	"""The setup wizard is running, or this site has not finished it."""
	if _flag("in_setup_wizard"):
		return True
	try:
		return not frappe.is_setup_complete()
	except Exception:
		return False


def _log(what: str) -> None:
	"""One line in the app log: what the guard changed, never the record's contents."""
	try:
		frappe.logger("erpnext_enhancements").warning(f"flow_guard: {what} ({SETTING_FIELD} is off)")
	except Exception:
		pass


def _how_to_allow() -> str:
	return frappe._(
		"A System Manager can allow it in ERPNext Enhancements Settings > AI Governance > "
		"Allow Frappe Flow and Studio User."
	)


def guard_flow_setup(doc, method=None) -> None:
	"""``validate`` on Flow Provider and Flow Model: none new and none enabled while the switch is off.

	Refused in an ordinary request; saved disabled (never refused) during a deploy, in the setup
	wizard or before setup is complete.
	"""
	if doc.doctype not in SETUP_DOCTYPES:
		return
	is_new = doc.is_new() if hasattr(doc, "is_new") else False
	enabled = bool(int(doc.get("enabled") or 0))
	if not (is_new or enabled):
		return  # an existing record saved disabled
	if _deploying() or _setting_up():
		if enabled and not flow_allowed():
			doc.enabled = 0
			_log(f"a {doc.doctype} was saved disabled")
		return
	if flow_allowed():
		return
	frappe.throw(
		frappe._(
			"Frappe Flow's assistant works outside this app's AI write gate and field-level read "
			"checks, so it cannot be connected to a model here."
		)
		+ " "
		+ _how_to_allow(),
		frappe.PermissionError,
		title=frappe._("Frappe Flow is off on this site"),
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


def drop_new_flow_roles(doc, method=None) -> None:
	"""``validate`` on User and Role Profile: a Flow User or Studio User role this save adds is taken off.

	Never raises. Reads the setting only when the save adds one of the two roles, so every other
	User save (and ERPNext's test bootstrap, before this app's fields exist) costs one set
	intersection.
	"""
	if doc.doctype not in ROLE_HOLDERS:
		return
	added = added_guarded_roles(doc)
	if not added or flow_allowed():
		return
	rows = doc.get("roles")  # the record's own list: removing from it removes the row
	for row in [row for row in rows if row.get("role") in added]:
		rows.remove(row)
	for idx, row in enumerate(rows, start=1):
		row.idx = idx
	names = ", ".join(sorted(added))
	_log(f"{names} not given on a {doc.doctype}")
	user = getattr(getattr(frappe, "session", None), "user", None) or "Guest"
	if _deploying() or _flag("in_setup_wizard") or user == "Guest":
		return  # nobody at a screen to tell: a deploy, the wizard, or a sign-in syncing roles
	try:
		frappe.msgprint(
			frappe._(
				"Not given: {0}. Frappe Flow and Studio are off on this site, because Flow's "
				"assistant works outside this app's AI write gate and field-level read checks, and a "
				"Studio User can publish pages with scripts on this site."
			).format(names)
			+ " "
			+ _how_to_allow(),
			title=frappe._("Role not given"),
			indicator="orange",
		)
	except Exception:
		pass
