"""Month-End Close Task — child table of Month-End Close.

One row per close-checklist step (reconcile bank, post accruals, review aging,
approve statements, ...) with its responsible person and status. Pure data row;
completion is stamped by the parent Month-End Close controller.
"""

from frappe.model.document import Document


class MonthEndCloseTask(Document):
	pass
