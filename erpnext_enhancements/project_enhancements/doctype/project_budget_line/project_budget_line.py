# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""One category line of a project budget -- WI-075 sub-phase M.

A child table with no controller logic of its own. Everything that decides anything about these
rows lives on the parent: the Project derives its total from them, and `Budget Reallocation` moves
money between them. A child controller that also had opinions would be a third place the same
rules could disagree.

The class exists because Frappe resolves a controller by name and force-deletes the DocType when
the import fails -- silently, inside `bench migrate`, which on this repo is the deploy. The name
is derived as `doctype.replace(" ", "").replace("-", "")`, so `Project Budget Line` must be
`ProjectBudgetLine` exactly. `tests/test_doctype_controller_names.py` fails the build on a
mismatch.
"""

from frappe.model.document import Document


class ProjectBudgetLine(Document):
	pass
