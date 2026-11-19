"""One-off remediation: strip the redundant ``PRJ-###`` number that the QBO job
importer prefixed onto Project titles.

Background
----------
A QBO sub-customer / job carries a ``DisplayName`` that mirrors the ERPNext project
it belongs to and is prefixed with that project's number ("PRJ-401 - 4th West
Fountain", "PRJ000062 - Terror Ride Fountain"). When the importer linked a job to its
existing Project, the in-place *update* path applied every mapped value -- including
``project_name`` -- so the job's prefixed DisplayName overwrote the project's clean
title on each re-sync. The result: ~377 projects whose ``project_name`` reads
"PRJ-00581 Myers Mortuary" instead of "Myers Mortuary". The number is already the
Project's ``name``, so it is pure duplication.

The forward fix (``core/mapping.py``: ``_job_project_title`` strips the prefix on
create; ``_protect_existing_project_title`` stops the update path overwriting a set
title) prevents recurrence. This module cleans the titles already written.

What it does
------------
For every Project whose ``project_name`` begins with a ``PRJ-###`` token, it strips
that leading token (and its separator) via the shared ``strip_prj_prefix`` -- the same
transform the importer now uses -- and writes the clean title back. It writes with
``frappe.db.set_value`` (no doc hooks) because this is a pure display-field
denormalisation: it must not fire the Project ``on_update`` notifications / realtime /
process-step side effects 377 times.

Out of scope (reported, never touched): a title that is *only* the number with no name
("PRJ-00614"), where stripping would leave nothing; and a number that appears
mid/end-string rather than as a leading prefix ("Ogden Temple PRJ-00612") -- a
different pattern that may be intentional. These are listed for manual review.

Safety
------
* **Dry-run by default** (``apply=False`` writes nothing; it reports what it would do).
* **Idempotent** -- re-runnable; an already-clean title strips to itself and is skipped.
* **Batched + committed** so a mid-run failure keeps completed work.
* **Per-record guarded** -- one bad row logs an Error and is skipped, never aborting.
* **Title-only** -- it changes ``project_name`` (a display field), never the docname
  (``name``) or any link, so no reference is repointed and nothing cascades.
* **Not** wired to migrate/scheduler. Run it manually, **sandbox first**.

Run it::

    # 1) preview (no writes):
    bench --site <site> execute \\
      erpnext_enhancements.quickbooks_online.core.project_name_remediation.strip_project_name_prefixes
    # 2) apply (after reviewing the dry-run, on sandbox first):
    bench --site <site> execute \\
      erpnext_enhancements.quickbooks_online.core.project_name_remediation.strip_project_name_prefixes \\
      --kwargs "{'apply': True}"
"""

from __future__ import annotations

import frappe

from erpnext_enhancements.quickbooks_online.core.mapping import strip_prj_prefix

QBO_COMMIT_EVERY = 100

# Project titles that begin with a PRJ-### token (the prefixed ones to clean). The
# trailing boundary keeps it anchored to a leading token; MariaDB REGEXP is
# case-insensitive under the default collation.
_LEADING_PRJ_REGEXP = r"^[[:space:]]*PRJ-?[0-9]"


def strip_project_name_prefixes(apply=False, limit=None, verbose=True):
	"""Strip the redundant leading ``PRJ-###`` prefix from Project ``project_name``.

	Args:
		apply: When False (default) this is a DRY RUN -- it computes and reports the
			plan for every affected project but writes nothing. Pass True to write the
			cleaned titles.
		limit: Optionally process at most this many projects (handy for a first
			sandbox run, e.g. ``limit=5``).
		verbose: Print a per-project before/after line in addition to the summary.

	Returns:
		dict: A summary report (counts per outcome + a ``changes`` list of
		``{name, before, after}`` and a ``skipped`` list of unchanged/degenerate
		titles). Also printed for ``bench execute`` visibility.
	"""
	if apply:
		# Writing path is privileged; the dry run is safe for anyone to preview.
		frappe.only_for("System Manager")

	rows = frappe.db.sql(
		"""select name, project_name from `tabProject`
		   where project_name regexp %(pat)s
		   order by name""",
		{"pat": _LEADING_PRJ_REGEXP},
		as_dict=True,
	)
	if limit:
		rows = rows[:limit]

	report = {
		"mode": "apply" if apply else "dry-run",
		"candidates": len(rows),
		"changed": 0,
		"unchanged": 0,
		"errors": 0,
		"changes": [],
		"skipped": [],
	}

	for index, row in enumerate(rows, start=1):
		try:
			before = row.project_name
			after = strip_prj_prefix(before)
			if after == before:
				# Already clean, or a number-only title that strip_prj_prefix refuses to
				# blank ("PRJ-00614"). Nothing to do; record it for visibility.
				report["unchanged"] += 1
				report["skipped"].append({"name": row.name, "project_name": before})
				continue
			report["changed"] += 1
			report["changes"].append({"name": row.name, "before": before, "after": after})
			if verbose:
				print(f"  [{index}/{len(rows)}] {row.name}: {before!r} -> {after!r}")
			if apply:
				# Display-field denormalisation only -- bypass doc hooks (notifications,
				# realtime, process-step transitions) that the Project on_update fires.
				frappe.db.set_value("Project", row.name, "project_name", after, update_modified=False)
				if index % QBO_COMMIT_EVERY == 0:
					frappe.db.commit()
		except Exception:  # one bad row must never abort the batch
			report["errors"] += 1
			frappe.log_error(
				f"Project title remediation failed for {row.get('name')}\n{frappe.get_traceback()}",
				"QBO Project Title Remediation Error",
			)

	if apply:
		frappe.db.commit()

	_print_summary(report)
	return report


def _print_summary(report):
	"""Print a human-readable summary for ``bench execute``."""
	print(f"\n=== QBO project-title remediation ({report['mode']}) ===")
	for key in ("candidates", "changed", "unchanged", "errors"):
		print(f"  {key:12} {report[key]}")
	if report["skipped"]:
		print(f"  (skipped/unchanged: {len(report['skipped'])} -- e.g. number-only titles)")
	if report["mode"] == "dry-run":
		print("  (dry run -- nothing was written; re-run with apply=True to execute)")
