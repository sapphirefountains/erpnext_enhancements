# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""One line of somebody's first week.

No logic of its own; the parent derives progress and stamps who ticked what. A
child table still needs this file — `bench migrate` calls `load_doctype_module`
for every DocType including child tables, and a missing controller aborts the
migrate partway, leaving the child registered without its parent and failing
every subsequent deploy the same way. That took production's pipeline down at
v1.268.0, and `tests/test_doctype_modules.py` has asserted it ever since.
"""

from frappe.model.document import Document


class OnboardingChecklistItem(Document):
	pass
