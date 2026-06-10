"""Runtime monkeypatches applied to the Frappe framework on worker startup.

Carried in app code (instead of editing ``apps/frappe`` directly) so they
survive ``bench update``. Triggered from the bottom of ``hooks.py`` — Frappe
imports every installed app's ``hooks`` module in every worker the first time it
loads hooks, so ``apply()`` runs once per process — in web, background, and
scheduler workers alike — before any patched code path is reached. Every patch
is idempotent and self-guarding.
"""

import functools

import frappe
import frappe.utils.modules as _frappe_modules


def _patch_get_modules_from_app_none_safe():
	"""Never let a cached ``None`` from one app crash the whole module list.

	``get_modules_from_all_apps`` does ``modules_list += get_modules_from_app(app)``
	with no guard. ``get_modules_from_app`` is ``@redis_cache``-decorated, and that
	decorator *deliberately* returns ``None`` when a ``None`` was previously cached
	for a key (frappe/utils/caching.py — the "Edge Case: None can mean cache miss
	or the result itself is None" branch). So a single poisoned cache entry — e.g.
	a transient empty/failed ``Module Def`` query for an app such as ``telephony``
	— makes ``list += None`` raise ``TypeError: 'NoneType' object is not iterable``
	for the rest of that entry's TTL, taking down everything that lists modules:
	CRM's ``check_app_permission`` (the app switcher), Dashboard / Dashboard Chart
	/ Number Card, and the User / Module Profile forms.

	Wrapping the function so it can never return ``None`` is equivalent to the
	upstream one-liner ``get_modules_from_app(app) or []``, but protects every
	caller rather than only the loop in ``get_modules_from_all_apps``.
	"""
	original = _frappe_modules.get_modules_from_app
	if getattr(original, "_ee_none_safe", False):
		return  # already applied in this process

	@functools.wraps(original)  # carries redis_cache's .clear_cache / .ttl across
	def get_modules_from_app(app):
		return original(app) or []

	get_modules_from_app._ee_none_safe = True
	_frappe_modules.get_modules_from_app = get_modules_from_app


_PATCHES = (_patch_get_modules_from_app_none_safe,)


def apply():
	"""Apply every runtime monkeypatch. Idempotent; safe to call repeatedly.

	A failing patch is logged and skipped, never raised — this runs while Frappe
	is importing ``hooks.py``, and an exception here would break hook loading for
	the whole app.
	"""
	for patch in _PATCHES:
		try:
			patch()
		except Exception:
			try:
				frappe.logger("erpnext_enhancements").warning(
					f"Failed to apply monkeypatch {patch.__name__!r}", exc_info=True
				)
			except Exception:
				pass
