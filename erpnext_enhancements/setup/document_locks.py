"""Clear stale Role Profile document locks before fixture sync.

Registered in ``before_migrate`` (hooks.py), so this runs in Frappe's
``pre_schema_updates`` — i.e. *before* fixture sync in ``post_schema_updates``.

Why this exists
---------------
``fixtures/role_profile.json`` re-imports our Role Profiles on every migrate.
Frappe core's ``RoleProfile.on_update`` responds by calling::

    self.queue_action("update_all_users", enqueue_after_commit=True, queue="long")

``queue_action`` FILE-locks the document
(``sites/<site>/locks/<sha224("Role Profile:<name>")>.lock``) and defers the
"re-save every user carrying this profile" work to the long queue. That lock is
released only when the background job actually runs (its ``unlock()``).

On this deploy pipeline the job never runs: Cloud Build FLUSHDBs *both* Redis
instances (cache + queue) and restarts the bench immediately after
``bench migrate``, destroying the just-enqueued ``update_all_users`` jobs before
any worker can pick them up — so their lock files are orphaned on disk. Frappe
auto-expires a lock after 3h (``DOCUMENT_LOCK_EXPIRY``), so they normally clear
themselves; but if a *second* migrate runs inside that 3h window (a re-deploy,
or a manual ``bench migrate``), the re-import hits the still-fresh lock and
``RoleProfile.on_update`` raises ``DocumentLockedError``, aborting the entire
migrate — which leaves the site 503 / "no healthy upstream" behind the load
balancer until someone clears the lock by hand.

Sweeping these locks at the very start of every migrate makes the migrate
self-healing regardless of the FLUSHDB race or the 3h expiry window. It only
touches Role Profile records (the exact rows fixture sync is about to
re-insert), so it cannot disturb an unrelated in-flight background job.

``Document.unlock()`` deletes the lock via Frappe's own ``get_signature()`` and
``file_lock.delete_lock`` swallows a missing-file ``OSError`` — so calling it is
safe (and version-proof) whether or not a lock is currently present.
"""

import frappe


def clear_stale_role_profile_locks():
	"""``before_migrate`` entry point: drop any Role Profile document locks.

	No-op on a healthy site (nothing locked) and on fresh installs (no Role
	Profile doctype / no rows yet). Guarded per-record so one unreadable row can
	never abort the migrate this hook exists to protect.
	"""
	if not frappe.db.exists("DocType", "Role Profile"):
		return

	swept = 0
	for name in frappe.get_all("Role Profile", pluck="name"):
		try:
			doc = frappe.get_doc("Role Profile", name)
			locked = doc.is_locked  # fresh (<3h) lock that would crash fixture sync
			doc.unlock()  # also sweeps expired-but-present lock files
			if locked:
				swept += 1
		except Exception:
			# Never let lock-cleanup break the migrate it is meant to unblock.
			frappe.log_error(f"clear_stale_role_profile_locks: {name}")

	if swept:
		print(f"[erpnext_enhancements] cleared {swept} stale Role Profile lock(s) before fixture sync")
