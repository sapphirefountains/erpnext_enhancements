"""Create the "Task Dashboard" Custom HTML Block from its repo source files.

The block's HTML/JS/CSS live version-controlled in the repo-root
``Custom HTML Block/`` folder (``task_dashboard.{html,js,css}``) — same
source-of-truth-with-manual-sync model as the existing "Projects Dashboard"
block documented in that folder's README. This patch reads those files and
creates the Custom HTML Block record **only if it doesn't exist yet**, so
later UI-side edits are never overwritten by re-migrations; to deploy edits
made in the repo afterwards, paste them into the block in the UI (or bump
this logic deliberately).

After the migrate, the block still needs to be placed once: edit the target
Workspace (e.g. Home or the TV workspace) and add the "Task Dashboard"
Custom HTML Block. The data comes from
``erpnext_enhancements.api.task_dashboard.get_task_dashboard_data``.

Requires a git-cloned app (``bench get-app``) where the repo root ships next
to the package — true on every bench; if the folder is missing the patch
logs and skips rather than failing the migrate.
"""

import os

import frappe

BLOCK_NAME = "Task Dashboard"


def execute():
	if frappe.db.exists("Custom HTML Block", BLOCK_NAME):
		return

	from erpnext_enhancements.setup.custom_html_blocks import _source_dir

	source_dir = _source_dir()

	def read(filename):
		path = os.path.join(source_dir, filename)
		with open(path, encoding="utf-8") as f:
			return f.read()

	try:
		html = read("task_dashboard.html")
		script = read("task_dashboard.js")
		style = read("task_dashboard.css")
	except OSError:
		frappe.log_error(
			f"Task Dashboard block sources not found under {source_dir} — block not created.\n"
			f"{frappe.get_traceback()}",
			"seed_task_dashboard_block",
		)
		return

	doc = frappe.new_doc("Custom HTML Block")
	doc.name = BLOCK_NAME
	doc.private = 0
	doc.html = html
	doc.script = script
	doc.style = style
	doc.insert(ignore_permissions=True)
