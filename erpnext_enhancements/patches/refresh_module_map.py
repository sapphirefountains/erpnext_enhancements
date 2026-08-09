"""Rebuild the app → modules map once, in the same process as the migrate that installs Chat.

**This is deliberately redundant with a hook, and both ship.** The permanent fix is
``setup/module_map.py`` registered in ``before_migrate`` (hooks.py): it runs on *every*
migrate and therefore protects module #30 as well as Chat. Frappe runs ``before_migrate``
hooks in ``pre_schema_updates``, which is strictly before this section of ``patches.txt``,
so on a healthy migrate this patch finds the map already correct and does nothing at all.

It ships anyway for one reason: a hook is a line in a list that a future edit can drop, and
this patch is the belt-and-braces guarantee that the deploy carrying the fix rebuilds the map
**inside** ``run_schema_updates`` — the same ``@atomic`` phase as ``frappe.model.sync.sync_all()``,
which is the call that must see the ``chat`` entry. ``pre_model_sync`` is the only patch
section that can help: ``post_model_sync`` runs after sync_all and would be a whole migrate
too late (which is precisely how Training installed 12 minutes and one redeploy late on
2026-08-01).

Why the module map and not a ``Module Def`` row: a ``Module Def`` is created *by*
``DocType.on_update`` → ``make_module_and_roles`` when the first DocType of a module imports,
so inserting one here would produce a Module Def with still no DocTypes — a worse diagnostic
state than the failure it is meant to fix, because the obvious symptom disappears while the
schema stays missing. And a declarative ``<module>/module_def/<name>.json`` cannot work at
all: ``module_def`` is not in ``frappe.model.sync.IMPORTABLE_DOCTYPES``, and even hooked in it
would be discovered by walking the module folder — i.e. from inside the module that is being
skipped. The full reasoning is in ``setup/module_map.py``.

Safe to run twice; safe on a fresh site (``bench install-app`` refreshes the map itself before
``sync_for``, and marks this patch executed without running it).
"""

from erpnext_enhancements.setup.module_map import refresh_app_module_map


def execute() -> None:
	"""Delete the cached module snapshot and re-read every app's ``modules.txt``."""
	refresh_app_module_map()
