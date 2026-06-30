"""Version-controlled Custom HTML Blocks (Projects-module dashboard widgets).

Entry point :func:`sync_custom_html_blocks` is registered in ``after_migrate``
(hooks.py). The four dashboard widgets — Projects Dashboard, Task Dashboard,
Morning Briefing and Desk Shortcuts — author their HTML/JS/CSS in the repo-root
``Custom HTML Block/`` folder, and this seeder **upserts** each one on every
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

Requires a git-cloned app (``bench get-app``) where the repo root ships next to
the package; if the source folder is missing the seeder logs and skips rather
than failing the migrate.
"""

import json
import os

import frappe

# name -> source-file prefix under the repo-root "Custom HTML Block/" folder.
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
]

# Subset of BLOCKS appended to the Home workspace. KPI Cockpit reaches Home via
# the department-dashboard loop below (not here); any other block absent here is
# placed by its own workspace fixture instead.
HOME_BLOCKS = {"Desk Shortcuts", "Projects Dashboard", "Task Dashboard", "Morning Briefing"}

HOME_WORKSPACE = "Home"

# The KPI Cockpit (with its department picker) also lands on Home and on each
# department dashboard, where it auto-locks to that department by route (see
# "Custom HTML Block/kpi_cockpit.js"). These dashboards are site-created during
# the module reorg and may be absent on a given site — placement skips them
# silently rather than failing the migrate.
KPI_COCKPIT = "KPI Cockpit"
KPI_DEPARTMENT_DASHBOARDS = (
	"Finance Dashboard",
	"Sales Dashboard",
	"Operations Dashboard",
	"Design Dashboard",
	"Production Dashboard",
	"Marketing Dashboard",
	"Executive Dashboard",
)


def _source_dir():
	repo_root = os.path.dirname(frappe.get_app_path("erpnext_enhancements"))
	return os.path.join(repo_root, "Custom HTML Block")


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

	# Surface the KPI Cockpit where people already work: Home (keeps the
	# department picker) and each department dashboard (the block auto-locks to
	# its department by route). Only once the block itself synced OK.
	if KPI_COCKPIT in synced:
		for workspace in (HOME_WORKSPACE, *KPI_DEPARTMENT_DASHBOARDS):
			if _append_custom_blocks(workspace, [KPI_COCKPIT]):
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

	Returns True if the content changed. A missing workspace is skipped silently
	(the department dashboards are site-created and need not exist on every site).
	The column is written directly (not ``doc.save``) so saving a standard/public
	workspace can't trigger a JSON file export in developer-mode benches."""
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
	return changed
