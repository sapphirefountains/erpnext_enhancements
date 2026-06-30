"""Create the "Morning Briefing" Custom HTML Block from its repo source files.

Same source-of-truth-with-manual-sync model as the Task Dashboard block
(``patches/seed_task_dashboard_block.py``): the block's HTML/JS/CSS live in
the repo-root ``Custom HTML Block/`` folder; this patch creates the record
**only if it doesn't exist yet**, so UI-side edits survive re-migrations.

After the migrate, place the block once: edit the target Workspace and add
the "Morning Briefing" Custom HTML Block. Data comes from
``erpnext_enhancements.api.briefing.get_morning_briefing`` (and renders a
"disabled" notice until the feature is switched on in Settings).
"""

import os

import frappe

BLOCK_NAME = "Morning Briefing"


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
		html = read("morning_briefing.html")
		script = read("morning_briefing.js")
		style = read("morning_briefing.css")
	except OSError:
		frappe.log_error(
			f"Morning Briefing block sources not found under {source_dir} — block not created.\n"
			f"{frappe.get_traceback()}",
			"seed_morning_briefing_block",
		)
		return

	doc = frappe.new_doc("Custom HTML Block")
	doc.name = BLOCK_NAME
	doc.private = 0
	doc.html = html
	doc.script = script
	doc.style = style
	doc.insert(ignore_permissions=True)
