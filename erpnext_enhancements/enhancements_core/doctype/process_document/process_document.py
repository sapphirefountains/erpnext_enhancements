"""Process Document doctype controller.

Mermaid.js-based process documentation: ``title`` (unique, document name),
``mermaid_code`` (Markdown Editor) and a read-only ``diagram`` HTML field the
form script renders into (public/js/process_document.js, wired via
``doctype_js`` in hooks.py). Ported from a DB-only custom DocType; no custom
controller logic.
"""

from frappe.model.document import Document


class ProcessDocument(Document):
	pass
