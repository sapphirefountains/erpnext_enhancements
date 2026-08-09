"""Rebuild the app → modules map before model sync, so a NEW module actually installs.

Registered in ``before_migrate`` (hooks.py), which Frappe runs in
``SiteMigration.pre_schema_updates`` — i.e. before the ``pre_model_sync`` patches and
before ``frappe.model.sync.sync_all()``. That window is the entire point of this module;
moved anywhere later it is useless.

What goes wrong without it
--------------------------
Adding a module to ``modules.txt`` is **not sufficient on an already-installed site**.
Model sync does not read ``modules.txt``. It iterates a snapshot:

``frappe/model/sync.py`` (v16)::

    for module_name in frappe.local.app_modules.get(app_name) or []:
        folder = os.path.dirname(frappe.get_module(app_name + "." + module_name).__file__)
        files = get_doc_files(files=files, start_path=folder)

``frappe.local.app_modules`` is built once, in ``frappe.init()``, by
``frappe.setup_module_map()`` — and that function reads ``modules.txt`` **only when the
Redis key ``app_modules`` is empty**::

    if include_all_apps:
        app_modules = cache.get_value("app_modules")
    ...
    if not app_modules:
        ...  # only here is modules.txt read
        cache.set_value("app_modules", app_modules)

Nothing in ``bench migrate`` rebuilds it. ``SiteMigration.setUp`` calls
``frappe.clear_cache()``, whose no-argument branch deletes site cache keys but does **not**
call ``setup_module_map`` (only ``clear_global_cache()`` does), so ``frappe.local.app_modules``
keeps whatever value ``init`` found for the rest of the process.

So a migrate that starts with a stale snapshot walks the **previous release's** module list.
A module added in the release being deployed is never walked: no DocType JSON is enumerated,
no table is created, no ``Module Def`` is made (``Module Def`` is created by
``DocType.on_update`` → ``make_module_and_roles``, so it is a *consequence* of the DocType
import, not a precondition for it), every post-model-sync patch that touches those tables
burns its Patch Log entry against a missing schema — **and the migrate exits 0.**

That is exactly how v1.261.0 shipped the Chat module's ten DocTypes and installed none of
them on 2026-08-09. Note the deploy order (``infra/cloudbuild-deploy.yaml``):
``reset → bench migrate → bench build → FLUSHDB both redis → restart``. The cache flush
happens **after** the migrate, so at migrate time the ``app_modules`` key holds whatever a
pre-reset process (typically the once-a-minute scheduler tick, which inits with
``include_all_apps=True``) last wrote — the old module list. Whether that key is stale is a
race, which is why Fleet Maintenance and Package Dispatch installed on their first deploy
while Product Configurator (+5 days), Offsite Backup (+1 day), Training (+12 minutes) and
Chat did not.

The fix, and why the delete comes first
---------------------------------------
Three lines, copied from what core itself does in ``frappe/installer.py::install_app``::

    if name not in (frappe.local.app_modules or {}):
        frappe.cache.delete_value("app_modules")
        frappe.setup_module_map(include_all_apps=True)

**Order is load-bearing.** ``setup_module_map`` re-reads the Redis key and only falls back to
``modules.txt`` when it comes back empty, so calling it without deleting first just re-seats
the same stale value.

Deliberately unguarded: if ``frappe.cache`` or ``setup_module_map`` ever changes shape, this
raises and fails the migrate. A silent failure here is the bug this module exists to prevent,
and ``before_migrate`` runs before any schema change, so an abort here is the cheapest
possible moment to fail.

Cost when nothing is wrong: one ``modules.txt`` read per app on the bench, once per migrate.
"""

import frappe

#: The site-cache key holding the all-apps map, read by ``setup_module_map(include_all_apps=True)``.
CACHE_KEY: str = "app_modules"

#: The client-cache key holding the installed-apps-only map, used by web requests and
#: background jobs (``setup_module_map(include_all_apps=False)``). Refreshed as well so a
#: worker that survives the deploy does not keep serving the old module list.
CLIENT_CACHE_KEY: str = "installed_app_modules"


def refresh_app_module_map() -> None:
	"""``before_migrate`` entry point: re-read every app's ``modules.txt``.

	Idempotent and cheap. Prints the modules that became visible, because "the module map
	was stale" is otherwise invisible in a deploy log — and invisibility is the whole
	failure mode.
	"""
	before = _snapshot()

	frappe.cache.delete_value(CACHE_KEY)
	_drop_installed_app_modules()
	frappe.setup_module_map(include_all_apps=True)

	after = _snapshot()
	added = sorted(
		f"{app}.{module}"
		for app, modules in after.items()
		for module in modules - before.get(app, set())
	)
	if added:
		# Loud on purpose: this line means the migrate about to run would NOT have
		# installed these modules' DocTypes without this hook.
		print(
			"[erpnext_enhancements] app module map was stale; rebuilt before model sync. "
			"Newly visible module(s): " + ", ".join(added)
		)


def _snapshot() -> dict[str, set[str]]:
	"""``frappe.local.app_modules`` as ``{app: {scrubbed_module, ...}}``."""
	return {app: set(modules or []) for app, modules in (frappe.local.app_modules or {}).items()}


def _drop_installed_app_modules() -> None:
	"""Delete the client-cache twin of :data:`CACHE_KEY`, if this Frappe has one.

	Guarded, unlike the two lines around it: the request/job map is not what model sync
	reads, so failing to clear it must never abort a migrate. ``frappe.client_cache`` is
	``None`` until the Redis connection is set up, and ``ClientCache.delete_value`` is a
	narrower API than ``RedisWrapper.delete_value`` — neither is worth a deploy over.
	"""
	client_cache = getattr(frappe, "client_cache", None)
	if client_cache is None:
		return
	try:
		client_cache.delete_value(CLIENT_CACHE_KEY)
	except Exception:
		print(f"[erpnext_enhancements] could not clear {CLIENT_CACHE_KEY}; model sync is unaffected")
