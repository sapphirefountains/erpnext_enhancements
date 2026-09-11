# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Call Routing Target — one phone, softphone, or voicemail box on a routing rule.

No behaviour. ``target_doctype`` is stamped from ``target_type`` by the parent
``Call Routing Rule``'s ``validate`` so ``target_value`` can be a Dynamic Link; resolving
a target to an actual dialable leg lives in ``ai_governance/call_routing.py``, because it
needs the whole rule to write a useful warning.

The controller itself exists because ``tests/test_doctype_modules.py`` requires one beside
every DocType JSON, child tables included. Frappe calls ``load_doctype_module`` for every
DocType during ``bench migrate`` and raises ``ModuleNotFoundError`` when the file is
missing — which aborts the migrate partway and then fails every subsequent deploy the same
way. A child table is the easy one to forget: it needs no logic, so an empty controller
feels redundant right up until the deploy fails.
"""

from frappe.model.document import Document


class CallRoutingTarget(Document):
	pass
