"""Place the "Desk Shortcuts" Custom HTML Block on the Home workspace.

Idempotent: if Home's content already references the block, this does nothing —
so re-migrations and later manual edits are preserved. It **appends** to (never
overwrites) the existing Home layout, inserting the block just after the
onboarding block if present (else at the very top) so the custom tool icons are
prominent without dropping ERPNext's onboarding.

The block type/shape (``custom_block`` + ``data.custom_block_name``) matches how
Frappe stores Custom HTML Blocks placed via the workspace editor. The block hides
itself when the user has no visible tiles, so an empty slot is never shown in
practice (Time Kiosk and Project Dashboard are visible to everyone).
"""

import json

import frappe

WORKSPACE = "Home"
BLOCK_NAME = "Desk Shortcuts"
BLOCK_ID = "edsDeskShortcuts"


def execute():
	if not frappe.db.exists("Workspace", WORKSPACE):
		return
	# Only place a block that actually exists, else the workspace renders an
	# empty frame.
	if not frappe.db.exists("Custom HTML Block", BLOCK_NAME):
		return

	content = frappe.db.get_value("Workspace", WORKSPACE, "content")

	try:
		blocks = json.loads(content or "[]")
		if not isinstance(blocks, list):
			blocks = []
	except (ValueError, TypeError):
		blocks = []

	# Idempotent: skip if our block is already present.
	for b in blocks:
		if isinstance(b, dict) and (b.get("data") or {}).get("custom_block_name") == BLOCK_NAME:
			return

	block = {
		"id": BLOCK_ID,
		"type": "custom_block",
		"data": {"custom_block_name": BLOCK_NAME, "col": 12},
	}

	insert_at = 0
	if blocks and isinstance(blocks[0], dict) and blocks[0].get("type") == "onboarding":
		insert_at = 1
	blocks.insert(insert_at, block)

	# Write the column directly (not doc.save) so saving a standard/public
	# workspace can't trigger a JSON file export in developer-mode benches.
	frappe.db.set_value("Workspace", WORKSPACE, "content", json.dumps(blocks))
	frappe.clear_cache()
