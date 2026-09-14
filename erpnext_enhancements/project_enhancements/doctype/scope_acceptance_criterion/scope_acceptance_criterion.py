# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""One measurable thing an inspector can pass or fail.

Deliberately controller-free. Everything that could live here — minting the stable
``criterion_key``, refusing duplicates, refusing rows that cannot be inspected as written —
lives on the parent instead, in ``ProjectScopeOfWork``, because a child row's own ``validate``
does not fire when the parent is saved through some paths and a rule enforced in two places
is a rule enforced in neither.

The logic itself is in ``project_enhancements/scope_criteria.py``, which imports no ``frappe``
and is therefore tested on every push. There is no Frappe integration-test job in CI.
"""

from frappe.model.document import Document


class ScopeAcceptanceCriterion(Document):
	pass
