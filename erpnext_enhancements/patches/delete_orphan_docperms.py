"""Delete the permission rows whose DocType does not exist (v1.537.2).

Why
---
Opening the files on an Opportunity failed for every user except Administrator with
"DocType Attendance Request not found". The site has neither HRMS nor Helpdesk installed.

The files panel lists them with ``frappe.client.get_list("File", ...)``, and every File list
query runs Frappe's ``permission_query_conditions`` hook for File:
``frappe/core/doctype/file/file.py`` ``get_permission_query_conditions``. For a
non-Administrator System User it takes ``frappe.permissions.get_doctypes_with_read(user)``, the
``parent`` of every ``DocPerm`` and ``Custom DocPerm`` row at permlevel 0 with ``read`` for the
user's roles. Those roles include ``All``, which every signed-in user holds. It then passes that
list to ``_split_doctypes_by_owner_constraint`` and ``_split_doctypes_by_user_permissions``, and
both call ``frappe.get_meta(doctype)`` on each name. ``get_meta`` on a name with no
``tabDocType`` row raises ``DoesNotExistError`` ("DocType X not found"), so the whole query
fails. Administrator returns before any of this.

That per-name ``get_meta`` is new in Frappe 16.35.0 (commit 0a770d9716, "honor if_owner on
attached doctype in File list permission hook"). Up to 16.34.0 the same list was only joined
into an ``attached_to_doctype IN (...)`` string, where a name that matched nothing did no harm.
That is why these rows, last modified in February 2026, caused no trouble until this upgrade.

Production held 161 such ``Custom DocPerm`` rows across 40 doctypes when measured on
2026-09-25: Attendance, Attendance Request, Leave Application, Expense Claim, Salary Slip,
HD Ticket and 34 more HRMS doctypes. It held no orphan ``DocPerm`` rows. The ``All`` role holds
a read row on ``HD Ticket``, so every signed-in System User hit at least one of them. Which name
the error shows depends on set order.

What it does
------------
It reads every ``tabDocType`` name, then the ``parent`` of every ``Custom DocPerm`` row and of
every ``DocPerm`` row whose ``parenttype`` is ``DocType``. A parent that matches no DocType name
is an orphan. The comparison ignores case, accents and trailing spaces, as the tables'
``utf8mb4_unicode_ci`` / PAD SPACE collation does. So it errs towards keeping a row: a parent
that matches a DocType only under another spelling is kept (production has none), and the
DELETE's ``IN`` match, which runs under that collation, does not reach a row of a DocType that
exists. Rows with a blank parent are also left alone, because ``get_doctypes_with_read``
already skips them.

Orphan rows are removed with ``frappe.db.delete(<table>, {"parent": ("in", orphans)})``, which
is a plain DELETE with bound parameters. ``delete_doc`` is not used: it would write a Deleted
Document and run link checks for rows that belong to no DocType. When anything was deleted,
``frappe.clear_cache()`` runs so the permission caches drop the rows.

Why deleting is safe
--------------------
A permission row only grants access to documents of its own DocType. These DocTypes do not
exist, so the rows grant nothing. The only code that reads them is the code that breaks on them.

Keeping them would also do harm later. Any ``Custom DocPerm`` row on a DocType replaces that
DocType's standard ``DocPerm`` rows entirely (``get_valid_perms``). If HRMS or Helpdesk were
installed later, it would create its own standard perms, and these leftover rows would silently
override them.

This is the "delete" half of the two-step rule in CLAUDE.md. The repo is the source of truth,
and a row that should not exist needs a patch to remove it. These rows were never managed by
this app's fixtures: the ``Custom DocPerm`` fixture names only Material Request and Purchase
Order. So there is no JSON to remove, and this patch is the only step needed.

It never raises. A patch that raises aborts ``bench migrate``, which on this repo is the deploy,
and leaves a half-finished install (v1.395.0). A failure is written to the Error Log and the
patch returns. It is safe to run twice: the second run finds no orphans and deletes nothing.
"""

import unicodedata
from collections import Counter

import frappe

# (permission table, query listing the parent of every row in scope, its bound params, the
# same scope as a delete filter). DocPerm is a child table and is scoped to DocType parents;
# Custom DocPerm is a standalone doctype with no parenttype column.
PERM_TABLES = (
	("Custom DocPerm", "select parent from `tabCustom DocPerm`", (), {}),
	(
		"DocPerm",
		"select parent from `tabDocPerm` where parenttype = %s",
		("DocType",),
		{"parenttype": "DocType"},
	),
)


def execute() -> None:
	try:
		delete_orphan_docperms()
	except Exception:
		frappe.log_error(title="delete_orphan_docperms: failed")


def _key(name) -> str:
	"""Fold a DocType name roughly as ``utf8mb4_unicode_ci`` / PAD SPACE compares it.

	Case, accents and trailing spaces are ignored. Folding more than the database does only
	keeps more rows; folding less could let the collation-matched DELETE reach a real DocType.
	"""
	decomposed = unicodedata.normalize("NFKD", str(name))
	return "".join(c for c in decomposed if not unicodedata.combining(c)).rstrip(" ").casefold()


def delete_orphan_docperms() -> dict[str, int]:
	"""Delete permission rows whose parent DocType does not exist; return rows deleted per table."""
	existing = {_key(name) for (name,) in frappe.db.sql("select name from `tabDocType`")}
	if _key("DocType") not in existing:
		# DocType is itself a DocType. A read that lacks it is broken, and deleting against it
		# would treat every permission row on the site as an orphan.
		print("delete_orphan_docperms: tabDocType read looks incomplete; nothing deleted")
		return {}

	deleted: dict[str, int] = {}
	for table, query, params, scope in PERM_TABLES:
		try:
			orphans = Counter(
				parent
				for (parent,) in frappe.db.sql(query, params)
				if parent and _key(parent) not in existing
			)
			if not orphans:
				continue
			frappe.db.delete(table, {**scope, "parent": ("in", sorted(orphans))})
			frappe.db.commit()
			deleted[table] = sum(orphans.values())
			print(
				f"delete_orphan_docperms: deleted {deleted[table]} {table} row(s) for "
				f"{len(orphans)} missing DocType(s): {', '.join(sorted(orphans))}"
			)
		except Exception:
			try:
				frappe.db.rollback()
			except Exception:
				pass
			frappe.log_error(title=f"delete_orphan_docperms: {table} cleanup failed")

	if deleted:
		try:
			frappe.clear_cache()
		except Exception:
			frappe.log_error(title="delete_orphan_docperms: clear_cache failed")
	else:
		print("delete_orphan_docperms: no orphan permission rows")
	return deleted
