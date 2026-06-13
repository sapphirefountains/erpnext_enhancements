"""Create the "Desk Shortcuts" Custom HTML Block from its repo source files.

Same source-of-truth-with-manual-sync model as the Task Dashboard / Morning
Briefing blocks (``patches/seed_task_dashboard_block.py``): the block's
HTML/JS/CSS live in the repo-root ``Custom HTML Block/`` folder; this patch
creates the record **only if it doesn't exist yet**, so UI-side edits survive
re-migrations.

Unlike those blocks, placement is automated — ``patches.place_desk_shortcuts_on_home``
adds it to the Home workspace. The block renders ``frappe.boot.ee_desk_shortcuts``
(see ``erpnext_enhancements.api.desk_shortcuts``); it shows nothing until shortcut
rows are seeded (``patches.seed_desk_shortcuts``) and a user is allowed to see them.
"""

import os

import frappe

BLOCK_NAME = "Desk Shortcuts"


def execute():
	if frappe.db.exists("Custom HTML Block", BLOCK_NAME):
		return

	repo_root = os.path.dirname(frappe.get_app_path("erpnext_enhancements"))
	source_dir = os.path.join(repo_root, "Custom HTML Block")

	def read(filename):
		path = os.path.join(source_dir, filename)
		with open(path, encoding="utf-8") as f:
			return f.read()

	try:
		html = read("desk_shortcuts.html")
		script = read("desk_shortcuts.js")
		style = read("desk_shortcuts.css")
	except OSError:
		frappe.log_error(
			f"Desk Shortcuts block sources not found under {source_dir} — block not created.\n"
			f"{frappe.get_traceback()}",
			"seed_desk_shortcuts_block",
		)
		return

	doc = frappe.new_doc("Custom HTML Block")
	doc.name = BLOCK_NAME
	doc.private = 0
	doc.html = html
	doc.script = script
	doc.style = style
	doc.insert(ignore_permissions=True)
