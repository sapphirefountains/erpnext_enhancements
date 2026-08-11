# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Chat Retrieval Audit Room — one room touched by one privileged read.

``was_participant`` is the field the parent log exists for: a non-empty set of
``was_participant = 0`` rows is precisely the oversight event decision #12 makes
visible. It is resolved per room **at read time** by ``chat/audit.py`` and stored,
never re-derived later — membership changes, and a row that has to be recomputed
against today's roster answers a different question every time it is read.

No immutability guard of its own. A child table is governed entirely by its
parent, and the parent refuses every update and every delete; reaching a child row
without loading its parent means bypassing the ORM altogether, which is what the
source scan in ``tests/test_chat_audit_immutability.py`` and the ``chain_hash``
(which signs these rows, not just the parent's fields) are for.

**This file's existence is load-bearing, which is not obvious.** Frappe's
``load_doctype_module`` is called for *every* DocType during ``bench migrate`` —
child tables included — and raises ``ModuleNotFoundError`` when the controller is
absent. Shipping the JSON without it aborted the v1.268.0 migrate partway, leaving
this DocType registered with no table for its parent, and blocked every subsequent
deploy of any change until the module appeared. ``tests/test_doctype_modules.py``
now asserts the sibling ``.py`` exists for exactly this reason.
"""

from frappe.model.document import Document


class ChatRetrievalAuditRoom(Document):
	pass
