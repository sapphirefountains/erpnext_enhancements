"""Take the ``/`` out of the Drive shadow ``File.file_name`` values that have one.

**A ``File`` row whose ``file_name`` contains a path separator is a row Frappe itself
would never store, and it breaks inserts of *other* files.** ``File.set_file_name``
runs ``re.sub(r"/", "", file_name)`` on every insert and save, so the only way to get
one in is to go around the controller — which the shadow walk's name repair does, by
design: it writes through ``frappe.db.set_value`` precisely so a rename in Drive costs
no hooks, no timeline comment and no ``modified`` bump. Drive item names legitimately
contain slashes ("Fountain Repair / Fill Valve", "General Repairs 9/18/2025"), so from
v1.475.0 — when the walk started repairing names — every such item wrote one of these.
151 of them by 2026-09-18.

What makes them more than cosmetic is where Frappe reads them back.
``File.validate_duplicate_entry`` runs on every insert, and for a link-only shadow
(no bytes, so ``content_hash`` stays NULL) its filter degenerates to
``{"content_hash": None, "is_private": 1}`` — 34,437 production rows, of which
``frappe.db.get_value`` returns the **newest** (a direction-less ``order_by`` is DESC
in Frappe's query engine). Frappe loads that unrelated row and calls
``exists_on_disk()`` on it, which calls ``get_full_path()``, which throws
``File name cannot have /``. So one poisoned row fails every subsequent hashless
private ``File`` insert — and on 2026-09-18 that is exactly what it did: a shadow
written at 11:02 became the newest such row, and from 12:02 the hourly shadow sync
created nothing at all. Self-sealing, too: the poisoned row could only stop being the
newest if an insert succeeded.

``_shadow_display_name`` now replaces ``/`` with ``SHADOW_SLASH_REPLACEMENT`` (U+2215
DIVISION SLASH — reads as a slash, is not a path separator, survives
``set_file_name``), and the shadow insert sets ``ignore_duplicate_entry_error`` so it
no longer consults unrelated rows at all. This patch repairs what the old writer left
behind, so the rows stop being landmines for anything else that calls
``get_full_path()`` on them — the Desk file preview and download among them.

--------------------------------------------------------------------------------------
Why this and not "wait for the walk"
--------------------------------------------------------------------------------------

The walk *would* repair most of them on its own: it recomputes every name and rewrites
a stored one that differs, so the fixed ``_shadow_display_name`` reaches them on the
next full rotation. But not all — a shadow whose Drive item has since been moved or
deleted is flagged ``Stale`` and never renamed, and the rotation is time-boxed and
wraps, so "next" can be hours. The rows are harmful while they wait.

Keyed on the rule the writer applies, not on what the data looks like: a shadow name
the *current* ``_shadow_display_name`` could not have produced is one with a ``/`` in
it, because that function no longer emits one. Scoped to shadows (``custom_drive_file_id``
set) — every one of the 151 is a shadow, and a slash on some other app's ``File`` row
would be that app's business, not this patch's.

Safe to run twice: the second run matches nothing.
"""

import frappe

from erpnext_enhancements.google_drive.drive_sync import SHADOW_SLASH_REPLACEMENT


def execute() -> None:
	repair_slashed_drive_shadow_names()


def repair_slashed_drive_shadow_names() -> int:
	"""Rewrite every Drive shadow ``file_name`` holding a ``/``. Returns how many."""
	# The stamp is a Custom Field: on a fresh database this patch can run before the
	# fixtures that create it, and a filter on a column that does not exist raises —
	# which would abort bench migrate, i.e. the deploy.
	if not frappe.db.has_column("File", "custom_drive_file_id"):
		return 0

	rows = frappe.get_all(
		"File",
		filters={
			"custom_drive_file_id": ["is", "set"],
			"file_name": ["like", "%/%"],
		},
		fields=["name", "file_name"],
	)
	for row in rows:
		# db-level, like the walk's own name repair: these rows are already attached
		# and already logged, and a save here would add a timeline comment apiece.
		frappe.db.set_value(
			"File",
			row.name,
			"file_name",
			row.file_name.replace("/", SHADOW_SLASH_REPLACEMENT),
			update_modified=False,
		)
	return len(rows)
