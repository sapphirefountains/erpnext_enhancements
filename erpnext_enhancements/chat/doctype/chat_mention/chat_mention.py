# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Chat Mention — a child table of Chat Message, and the correct use of one.

Small, bounded by human patience, only ever read as ``WHERE parent = %s``. That
is the whole test for whether something should be a child table, and mentions
pass it where messages fail it.

``user`` is a ``Link``, not ``Data``. It costs one link-validation query per
mention on insert and buys rename participation and referential validity — which
matters because a mention is the *input to a notification*, and a dangling
mention is a notification that silently goes nowhere.

``start_index`` and ``length`` are spans into ``Chat Message.text_plain``. They
exist so the client renders the mention without re-parsing the body, and so an
inbound Google mention survives faithfully: Google delivers mentions as
``annotations`` of type ``USER_MENTION`` alongside a separate ``argumentText``.
**Parse the annotation. Never regex the raw text** — the raw text is user input
and a mention-shaped string in a code block is not a mention.

Phase 1 scope: schema only. No permissions array of its own; a child table is
governed entirely by its parent, and its parent has none.
"""

from frappe.model.document import Document


class ChatMention(Document):
	pass
