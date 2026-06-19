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
landing page shows all of them. Blocks created on the site under names *not*
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
]

HOME_WORKSPACE = "Home"


def _source_dir():
	repo_root = os.path.dirname(frappe.get_app_path("erpnext_enhancements"))
	return os.path.join(repo_root, "Custom HTML Block")


def _read(source_dir, prefix, ext):
	path = os.path.join(source_dir, f"{prefix}.{ext}")
	with open(path, encoding="utf-8") as f:
		return f.read()


def sync_custom_html_blocks():
	"""Create/refresh every repo-defined Custom HTML Block, then place on Home."""
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

	if synced:
		_place_blocks_on_home(synced)


def _place_blocks_on_home(block_names):
	"""Append any missing block to the Home workspace content (idempotent)."""
	if not frappe.db.exists("Workspace", HOME_WORKSPACE):
		return

	content = frappe.db.get_value("Workspace", HOME_WORKSPACE, "content")
	try:
		blocks = json.loads(content or "[]")
		if not isinstance(blocks, list):
			blocks = []
	except (ValueError, TypeError):
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

	if changed:
		# Write the column directly (not doc.save) so saving a standard/public
		# workspace can't trigger a JSON file export in developer-mode benches.
		frappe.db.set_value("Workspace", HOME_WORKSPACE, "content", json.dumps(blocks))
		frappe.clear_cache()
