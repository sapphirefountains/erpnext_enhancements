"""Process Document Step — child table of Process Document.

One row per mapped process step. It carries the RACI (who is Responsible,
Accountable, Consulted, Informed) alongside *how* the step is enforced in
ERPNext — Workflow Transition, DocPerm, User Permission, Row Filter, Notify, or
Manual. This child table is the machine-readable spec that drives the access
control and approval-workflow configuration described in the Business Process
Mapping program plan; the parent ``Process Document`` holds the human-readable
Mermaid flow. No custom controller logic — it is a pure data row.
"""

from frappe.model.document import Document


class ProcessDocumentStep(Document):
	pass
