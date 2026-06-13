"""Enhancement Desk Shortcut — admin-managed desk Home shortcut tiles.

Each row is one icon tile rendered on the **Home** workspace by the "Desk
Shortcuts" Custom HTML Block. The per-user visible set is computed in
``erpnext_enhancements.api.desk_shortcuts.get_visible_shortcuts_for_user`` and
shipped to the client via ``boot.py`` (``frappe.boot.ee_desk_shortcuts``); this
controller only normalizes/validates a single row.

Visibility here is **cosmetic** — every target page enforces its own role
permissions, so a user who reaches a page directly still gets "not permitted".
"""

import frappe
from frappe import _
from frappe.model.document import Document


class EnhancementDeskShortcut(Document):
	def validate(self):
		self.shortcut_label = (self.shortcut_label or "").strip()
		if self.icon:
			self.icon = self.icon.strip()
		if self.doc_view:
			self.doc_view = self.doc_view.strip()

		if self.link_type == "URL":
			if not (self.url or "").strip():
				frappe.throw(_("URL is required when Link Type is URL."))
			self.url = self.url.strip()
			self.link_to = None
			self.doc_view = None
		else:
			if not (self.link_to or "").strip():
				frappe.throw(_("Link To is required for {0} shortcuts.").format(self.link_type))
			self.link_to = self.link_to.strip()
			self.url = None
