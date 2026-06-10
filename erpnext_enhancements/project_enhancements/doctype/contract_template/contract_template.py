"""Master record: one generatable agreement type (the Jinja HTML body).

The five templates (msa, sow, owner, rental, maintenance) are converted
faithfully from Brian's revised agreement suite (Apr 2026) and seeded
insert-only by the ``seed_contract_templates`` patch from the
version-controlled sources in ``templates/contracts/`` — legal-text edits can
be made directly on the site record without a deploy, and survive
re-migrations. Rendering and generation live in
``project_enhancements/doctype/project_contract/project_contract.py``.
"""

from frappe.model.document import Document


class ContractTemplate(Document):
	pass
