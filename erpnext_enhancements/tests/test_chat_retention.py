"""Bench-free tests for the chat log-retention sync.

Subject: ``erpnext_enhancements.chat.retention``.

The bug this module fixes is the kind nothing notices: two settings fields that are
validated and then read by nobody, two ``clear_old_logs`` implementations Frappe never
calls, and two queue tables with no upper bound. Nothing errors. There is only a table that
grows, discovered when somebody looks.

So the tests here are mostly about the **wiring being real**, not about arithmetic:

* the field-to-doctype mapping is the one the settings form actually declares — a typo in a
  fieldname reads as 0, which now means "never purge", so a typo silently restores the
  original bug;
* every managed doctype implements ``clear_old_logs`` — because
  ``remove_unsupported_doctypes()`` runs first in ``run_log_clean_up`` and deletes the row of
  any doctype that does not, so registering one without the method is *worse* than not
  registering it: the row appears, vanishes on the next daily run, and retention never
  happens;
* the module does **not** declare these doctypes in ``default_log_clearing_doctypes``, which
  is the whole design decision — a static hook and a settings field are two sources of truth
  for one number, and ``add_default_logtypes`` re-adds a removed row, so "0 = never" could
  never stick.

Plain pytest functions, so this file needs its **own**
``python -m pytest erpnext_enhancements/tests/test_chat_retention.py -q`` step in CI.
"""

from __future__ import annotations

import ast
import json
import pathlib
import types

APP_DIR: pathlib.Path = pathlib.Path(__file__).resolve().parents[1]
RETENTION_PY: pathlib.Path = APP_DIR / "chat" / "retention.py"
SETTINGS_JSON: pathlib.Path = (
	APP_DIR / "chat" / "doctype" / "chat_settings" / "chat_settings.json"
)
HOOKS_PY: pathlib.Path = APP_DIR / "hooks.py"


def _managed() -> dict[str, str]:
	"""``MANAGED_DOCTYPES`` read out of the source with ``ast``.

	Parsed rather than imported: the module imports ``frappe``, and this suite deliberately
	installs no stub — everything it asserts is a fact about the source and the shipped JSON.
	"""
	tree = ast.parse(RETENTION_PY.read_text(encoding="utf-8"))
	for node in ast.walk(tree):
		if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "MANAGED_DOCTYPES":
			return ast.literal_eval(node.value)
		if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "MANAGED_DOCTYPES":
			return ast.literal_eval(node.value)
	raise AssertionError("MANAGED_DOCTYPES not found in chat/retention.py")


def _settings_fields() -> dict[str, dict]:
	data = json.loads(SETTINGS_JSON.read_text(encoding="utf-8"))
	return {f["fieldname"]: f for f in data.get("fields", []) if f.get("fieldname")}


# --- the wiring is real ----------------------------------------------------------


def test_every_managed_doctype_maps_to_a_field_that_exists() -> None:
	"""A typo'd fieldname reads as 0 through ``getattr(..., None) or 0`` — and 0 now means
	NEVER PURGE. So a typo does not fail loudly; it silently restores the exact bug this
	module was written to fix."""
	fields = _settings_fields()
	for doctype, fieldname in _managed().items():
		assert fieldname in fields, f"{doctype} maps to {fieldname!r}, which is not on the form"
		assert fields[fieldname]["fieldtype"] == "Int", fieldname


def test_every_managed_doctype_has_a_controller_that_implements_clear_old_logs() -> None:
	"""``remove_unsupported_doctypes()`` runs FIRST in ``run_log_clean_up`` and deletes the
	``Logs To Clear`` row of any doctype whose controller has no ``clear_old_logs``.

	Registering one without the method is therefore worse than not registering it: the row is
	created, silently removed on the next daily run, and retention never happens — with this
	module sitting in the tree looking like it works.
	"""
	for doctype in _managed():
		folder = doctype.lower().replace(" ", "_")
		controller = APP_DIR / "chat" / "doctype" / folder / f"{folder}.py"
		assert controller.exists(), f"no controller at {controller}"

		tree = ast.parse(controller.read_text(encoding="utf-8"))
		methods = {
			node.name
			for cls in ast.walk(tree)
			if isinstance(cls, ast.ClassDef)
			for node in cls.body
			if isinstance(node, ast.FunctionDef)
		}
		assert "clear_old_logs" in methods, (
			f"{doctype} is registered for retention but its controller does not implement "
			"clear_old_logs(days); Frappe will delete its Logs To Clear row on the next daily "
			"run and retention will silently never happen"
		)


def test_the_managed_doctypes_are_not_also_declared_in_the_static_hook() -> None:
	"""The design decision, asserted so it cannot be undone by somebody being helpful.

	``default_log_clearing_doctypes`` is a static number; these two have a dynamic one on a
	settings form. Declaring both means two sources of truth, and the hook wins in a way that
	cannot be argued with: ``add_default_logtypes`` appends any hook-declared doctype whose row
	is absent, and it runs from ``LogSettings.validate`` — so a row removed to mean "never
	purge" comes straight back on the next save.
	"""
	tree = ast.parse(HOOKS_PY.read_text(encoding="utf-8"))
	declared: dict = {}
	for node in tree.body:
		if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == (
			"default_log_clearing_doctypes"
		):
			declared = ast.literal_eval(node.value)
	overlap = set(declared) & set(_managed())
	assert not overlap, (
		f"{sorted(overlap)} is declared BOTH in default_log_clearing_doctypes and managed by "
		"chat/retention.py. Pick one: the hook's static value will fight the settings field, "
		"and a hook-declared row is recreated whenever it is removed, so 0-means-never breaks."
	)


def test_the_sync_is_wired_into_both_install_hooks() -> None:
	"""``after_migrate`` does not run during ``bench install-app``, and this site's Chat
	Settings may never be saved again — the fields have held their defaults since Phase 1. So
	the backstop has to be on both, or the rows appear on no site that does not edit the form.
	"""
	source = HOOKS_PY.read_text(encoding="utf-8")
	target = "erpnext_enhancements.chat.retention.ensure_chat_log_retention"
	assert source.count(target) == 2, (
		f"expected {target} in BOTH after_install and after_migrate, found "
		f"{source.count(target)} occurrence(s)"
	)


# --- the mapping itself ----------------------------------------------------------


def _desired_days(settings, managed: dict[str, str]) -> dict[str, int]:
	"""A local re-implementation would defeat the point, so this drives the real one.

	``desired_days`` touches no Frappe API — it is ``getattr`` and ``cint`` — so it can be
	exercised directly under a two-symbol stub rather than the full frappe stub the other
	chat suites install.
	"""
	import sys

	if "frappe" not in sys.modules:
		frappe = types.ModuleType("frappe")
		frappe.conf = {}
		utils = types.ModuleType("frappe.utils")
		utils.cint = lambda value, default=0: int(value or 0)
		frappe.utils = utils
		sys.modules["frappe"] = frappe
		sys.modules["frappe.utils"] = utils

	from erpnext_enhancements.chat import retention

	assert retention.MANAGED_DOCTYPES == managed
	return retention.desired_days(settings)


def test_zero_is_carried_through_as_zero_and_not_defaulted() -> None:
	"""0 means never purge, and the rest of this settings form already uses that convention
	(``message_retention_days``, ``hard_delete_after_days``). A helpful ``or 30`` here would
	silently start deleting rows somebody chose to keep."""
	managed = _managed()
	settings = types.SimpleNamespace(**{field: 0 for field in managed.values()})
	assert set(_desired_days(settings, managed).values()) == {0}


def test_a_missing_field_reads_as_zero_rather_than_raising() -> None:
	"""``doc_events`` and ``after_migrate`` both run during ERPNext's own test bootstrap,
	before this app's fields exist. The house rule is ``getattr(obj, "field", None) or ""``,
	and a retention sync that raised there would turn a fresh install into a failed migrate."""
	managed = _managed()
	assert set(_desired_days(types.SimpleNamespace(), managed).values()) == {0}


def test_a_negative_is_floored_rather_than_used() -> None:
	"""``validate`` refuses a negative, but this also runs from ``after_migrate`` against a
	document that predates that rule. A negative cutoff would be a date in the FUTURE, which
	deletes the entire table."""
	managed = _managed()
	settings = types.SimpleNamespace(**{field: -5 for field in managed.values()})
	assert set(_desired_days(settings, managed).values()) == {0}


def test_values_are_read_per_doctype_and_not_shared() -> None:
	"""Two tables, two independent numbers. A copy-paste that read one field for both would
	pass every test above."""
	managed = _managed()
	fields = list(managed.values())
	settings = types.SimpleNamespace(**{fields[0]: 7, fields[1]: 45})
	got = _desired_days(settings, managed)
	assert sorted(got.values()) == [7, 45]
