# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""One frozen check and its answer.

Controller-free. Answer validation, out-of-range derivation and progress counting all live on
``Project Quality Inspection``, the parent, because a child row's own validate does not fire on
every path by which the parent is saved — and a rule enforced in two places is a rule enforced
in neither.

Everything above `outcome`, `measured_value`, `notes` and `photo` was copied at generation and
is read-only. It is never re-read from the template it came from; that is the freeze.
"""

from frappe.model.document import Document


class InspectionResult(Document):
	pass
