"""Version-controlled Custom HTML Blocks (Projects-module dashboard widgets).

Entry point :func:`sync_custom_html_blocks` is registered in ``after_migrate``
(hooks.py). The dashboard widgets author their HTML/JS/CSS in the in-package
``erpnext_enhancements/custom_html_blocks/`` folder, and this seeder **upserts**
each one on every
migrate: missing blocks are created, and a block whose ``html``/``script``/
``style`` has drifted from the repo source is overwritten. The repo is the
source of truth — same philosophy as fixtures/ and setup/process_documents.py —
so a `bench migrate` after an edit to the source files redeploys the change
(UI-side edits to these blocks do **not** survive a migrate; edit the files).

This supersedes the older insert-only seed patches
(``patches/seed_task_dashboard_block``, ``seed_morning_briefing_block``,
``seed_desk_shortcuts_block``), which only created a block if it was missing.
Those patches are harmless once a block exists; this seeder now keeps every
block current.

It also places the blocks on the **Home** workspace (idempotent append) so the
landing page shows all of them, and surfaces the **KPI Cockpit** on Home and on
each department dashboard (see ``KPI_DEPARTMENT_DASHBOARDS``) so the numbers show
up where each team already works. Blocks created on the site under names *not*
listed here are left alone, and nothing is ever deleted.

The source files live INSIDE the Python package, so they ship with it on every
install and partial sync (the legacy repo-root ``Custom HTML Block/`` folder is
still honoured as a fallback for older checkouts). If the source folder is
missing entirely the seeder logs and skips rather than failing the migrate.
"""

import json
import os

import frappe

# name -> source-file prefix under the in-package "custom_html_blocks/" folder.
# Order is the order blocks are appended to Home.
BLOCKS = [
	("Desk Shortcuts", "desk_shortcuts"),
	("Projects Dashboard", "projects_dashboard"),
	("Task Dashboard", "task_dashboard"),
	("Morning Briefing", "morning_briefing"),
	# KPI Cockpit has its own "KPI Dashboards" workspace (its fixture); it is
	# additionally surfaced on Home and each department dashboard below, where the
	# block auto-locks to a department by route (role-gated per department).
	("KPI Cockpit", "kpi_cockpit"),
	# Finance Dashboard operational widgets — placed only on the Finance Dashboard
	# workspace (see FINANCE_DASHBOARD_BLOCKS), each gated by its own toggle.
	("Finance New Jobs", "finance_new_jobs"),
	("Finance Who's Working", "finance_whos_working"),
	("Finance Bank Balances", "finance_bank_balances"),
	("Finance Weather", "finance_weather"),
	("Finance Astrology", "finance_astrology"),
	("Finance Calendar", "finance_calendar"),
]

# Subset of BLOCKS appended to the Home workspace. KPI Cockpit reaches Home via
# the department-dashboard loop below (not here); any other block absent here is
# placed by its own workspace fixture instead.
HOME_BLOCKS = {"Desk Shortcuts", "Projects Dashboard", "Task Dashboard", "Morning Briefing"}

HOME_WORKSPACE = "Home"

# The KPI Cockpit (with its department picker) lands on Home and on each
# department workspace below, where it auto-locks to that department by route
# (see custom_html_blocks/kpi_cockpit.js). The department workspaces ship as
# role-gated standard workspaces of the KPI Dashboards module
# (kpi_dashboards/workspace/) so they group with the app's other module
# sidebars; their JSONs already carry these blocks, and this append is the
# backstop that restores a block if a site's workspace lost it. Keep this tuple
# in sync with those workspace names.
KPI_COCKPIT = "KPI Cockpit"
KPI_DEPARTMENT_DASHBOARDS = (
	"Finance Dashboard",
	"Sales Dashboard",
	"Operations Dashboard",
	"Design Dashboard",
	"Production Dashboard",
	"Marketing Dashboard",
	"Product Dashboard",
	"Executive Dashboard",
)

# Operational widgets placed only on the Finance Dashboard workspace (shipped
# in its module workspace JSON; this append is the backstop if a block goes
# missing). Each block is additionally role-gated + toggle-gated server-side,
# so a placed-but-disabled block just renders a muted notice.
FINANCE_DASHBOARD = "Finance Dashboard"
FINANCE_DASHBOARD_BLOCKS = (
	"Finance New Jobs",
	"Finance Who's Working",
	"Finance Bank Balances",
	"Finance Weather",
	"Finance Astrology",
	"Finance Calendar",
)


def _source_dir():
	"""Directory holding the block source triplets.

	Prefers the in-package ``custom_html_blocks/`` folder (ships with the package
	on every install and partial sync); falls back to the legacy repo-root
	``Custom HTML Block/`` folder for older checkouts that still keep it there.
	"""
	app_path = frappe.get_app_path("erpnext_enhancements")
	in_package = os.path.join(app_path, "custom_html_blocks")
	if os.path.isdir(in_package):
		return in_package
	return os.path.join(os.path.dirname(app_path), "Custom HTML Block")


def _read(source_dir, prefix, ext):
	path = os.path.join(source_dir, f"{prefix}.{ext}")
	with open(path, encoding="utf-8") as f:
		return f.read()


def sync_custom_html_blocks():
	"""Create/refresh every repo-defined Custom HTML Block, then place them on
	Home and the KPI Cockpit on each department dashboard."""
	source_dir = _source_dir()
	synced = []

	for name, prefix in BLOCKS:
		try:
			html = _read(source_dir, prefix, "html")
			script = _read(source_dir, prefix, "js")
			style = _read(source_dir, prefix, "css")
		except OSError:
			frappe.log_error(
				f"Custom HTML Block source for {name!r} not found under {source_dir} — skipped.\n"
				f"{frappe.get_traceback()}",
				"sync_custom_html_blocks",
			)
			continue

		if frappe.db.exists("Custom HTML Block", name):
			# Drift check: rewrite only the fields that changed (repo wins).
			current = frappe.db.get_value(
				"Custom HTML Block", name, ["html", "script", "style"], as_dict=True
			)
			updates = {}
			if (current.html or "") != html:
				updates["html"] = html
			if (current.script or "") != script:
				updates["script"] = script
			if (current.style or "") != style:
				updates["style"] = style
			if updates:
				frappe.db.set_value("Custom HTML Block", name, updates)
		else:
			doc = frappe.new_doc("Custom HTML Block")
			doc.name = name
			doc.private = 0
			doc.html = html
			doc.script = script
			doc.style = style
			doc.insert(ignore_permissions=True)

		synced.append(name)

	changed = False

	home_blocks = [name for name in synced if name in HOME_BLOCKS]
	if home_blocks and _append_custom_blocks(HOME_WORKSPACE, home_blocks):
		changed = True

	# Surface the KPI Cockpit on Home (keeps the department picker) and on each
	# role-gated department workspace, where the block auto-locks to that
	# department by route. Idempotent append — a department's own added blocks
	# are preserved. Only once the block itself synced OK.
	if KPI_COCKPIT in synced:
		for workspace in (HOME_WORKSPACE, *KPI_DEPARTMENT_DASHBOARDS):
			if _append_custom_blocks(workspace, [KPI_COCKPIT]):
				changed = True

	# Finance Dashboard operational widgets — placed only on the Finance Dashboard
	# workspace, in FINANCE_DASHBOARD_BLOCKS order (a missing workspace is skipped).
	finance_blocks = [name for name in FINANCE_DASHBOARD_BLOCKS if name in synced]
	if finance_blocks and _append_custom_blocks(FINANCE_DASHBOARD, finance_blocks):
		changed = True

	if changed:
		frappe.clear_cache()


def _merge_blocks(blocks, block_names):
	"""Idempotently append one ``custom_block`` widget per name. No DB IO:
	returns ``(blocks, changed)``. A block already present (matched by
	``custom_block_name``) is left untouched, so re-running never duplicates."""
	if not isinstance(blocks, list):
		blocks = []

	present = {
		(b.get("data") or {}).get("custom_block_name")
		for b in blocks
		if isinstance(b, dict) and b.get("type") == "custom_block"
	}

	changed = False
	for name in block_names:
		if name in present:
			continue
		blocks.append(
			{
				"id": "ee_chb_" + frappe.scrub(name),
				"type": "custom_block",
				"data": {"custom_block_name": name, "col": 12},
			}
		)
		present.add(name)
		changed = True

	return blocks, changed


def _append_custom_blocks(workspace_name, block_names):
	"""Append any missing block to ``workspace_name``'s content (idempotent).

	Returns True if anything changed. A missing workspace is skipped silently
	(sync order or a partially-set-up site may leave one absent). The column is
	written directly (not ``doc.save``) so saving a standard/public workspace
	can't trigger a JSON file export in developer-mode benches."""
	if not frappe.db.exists("Workspace", workspace_name):
		return False

	content = frappe.db.get_value("Workspace", workspace_name, "content")
	try:
		blocks = json.loads(content or "[]")
	except (ValueError, TypeError):
		blocks = []

	blocks, changed = _merge_blocks(blocks, block_names)
	if changed:
		frappe.db.set_value("Workspace", workspace_name, "content", json.dumps(blocks))
	if _ensure_custom_block_rows(workspace_name, block_names):
		changed = True
	return changed


def _ensure_custom_block_rows(workspace_name, block_names):
	"""Insert any missing "Workspace Custom Block" child row (idempotent).

	The v16 desk renders a content ``custom_block`` only when the Workspace also
	carries a matching child row — the server builds the block payload from
	``doc.custom_blocks``, not from content (frappe desktop.py get_custom_blocks),
	so a content-only placement silently renders an empty div. Runs even when the
	content already lists the block (pre-v1.146.0 placements wrote content only).
	Rows are inserted directly for the same no-``doc.save`` reason as above.
	Returns True if a row was added."""
	existing = {
		row.custom_block_name
		for row in frappe.get_all(
			"Workspace Custom Block",
			filters={"parenttype": "Workspace", "parent": workspace_name},
			fields=["custom_block_name"],
		)
	}

	added = False
	next_idx = len(existing)
	for name in block_names:
		if name in existing:
			continue
		next_idx += 1
		frappe.get_doc(
			{
				"doctype": "Workspace Custom Block",
				"parenttype": "Workspace",
				"parentfield": "custom_blocks",
				"parent": workspace_name,
				"custom_block_name": name,
				"label": name,
				"idx": next_idx,
			}
		).insert(ignore_permissions=True)
		existing.add(name)
		added = True

	return added
