"""Re-run the two chat bootstrap patches that were logged as executed while doing nothing.

On the 2026-08-09 deploy of v1.261.0 the Chat module never reached model sync (the app →
modules map was stale; see ``setup/module_map.py``), so at ``post_model_sync`` time none of
the chat tables and no ``Chat Settings`` DocType existed. Both patches did exactly what their
fail-safe guards say they should:

* ``add_chat_indexes`` logged ``"table missing, skipped index …"`` for all 11 composites and
  created none of them;
* ``default_chat_settings`` hit ``if not frappe.db.exists("DocType", "Chat Settings"): return``
  and seeded nothing — ``tabSingles`` still holds **zero** rows for it.

Neither raised, so both were written to ``Patch Log`` with ``skipped = 0``. Frappe's
``patch_handler.run_all`` filters the pending list against exactly that table::

    executed = set(frappe.get_all("Patch Log", filters={"skipped": 0}, fields="patch", pluck="patch"))
    ...
    if patch and (patch not in executed):
        run_patch(patch)

There is no re-run path. A patch in ``Patch Log`` is spent forever, whatever it did or did not
do.

**Why a new patch module rather than deleting the Patch Log rows.** Deleting rows is a
hand-edit to a site's migration history that no future site shares, so the fix would not exist
on a rebuild; and it would re-run the originals under their original identity, which is a lie
about what happened. A new module is a new Patch Log key, so this runs exactly once per site,
the originals stay byte-identical (a fresh install still runs them once, in the right order),
and the repo carries the whole story.

**Why it calls them instead of copying them.** Both implementations are already idempotent and
blank-fill-only by design — ``add_chat_indexes`` checks ``information_schema.STATISTICS`` before
any DDL, ``default_chat_settings`` never overwrites a value an operator has chosen. Duplicating
either would create a second definition of the dormancy contract and the index set, which is
the thing most worth having exactly one of.

Failure behaviour is inherited on purpose: ``add_chat_indexes.execute`` **raises** if it cannot
create a composite, because a half-applied index set means the design's uniqueness guarantees
(echo suppression, per-room sequence, per-room FIFO) are not actually guaranteed and a human
should look. ``ensure_chat_indexes`` in ``after_migrate`` is the non-raising backstop for every
subsequent migrate.

End state is identical on a fresh site and on production, and running ``bench migrate`` twice
is a no-op both times.
"""

from erpnext_enhancements.patches import add_chat_indexes, default_chat_settings


def execute() -> None:
	"""Create any missing chat composite index, then materialise `Chat Settings` dormant."""
	add_chat_indexes.execute()
	default_chat_settings.execute()
