# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""One row per offsite backup run.

Written entirely by :mod:`erpnext_enhancements.offsite_backup.backup` through the
ORM with ``ignore_permissions=True`` — hence ``in_create`` and no ``create``
permission in the doctype: nobody hand-writes a backup record.

The row is more than a record. Its ``Running`` status *is* the concurrency guard
that stops two multi-GB dumps overlapping, which is why the run commits it before
doing any work, and why ``reconcile_stale_runs()`` exists to clear one that a
killed worker left behind.
"""

from frappe.model.document import Document


class OffsiteBackupLog(Document):
	pass
