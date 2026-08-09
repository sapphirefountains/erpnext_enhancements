# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Chat Room Member — one person's membership of one room.

**A standalone DocType, not a child table of Chat Room, and that is the pivotal
modelling choice in the whole schema.** A child table is loaded in full with its
parent and cannot be indexed on ``user`` — and ``(user, is_active)`` is the
leading index of the single most-run query in the system: *"every room this user
is currently in"*, which the client asks on boot, the unread badge asks
continuously, and the retrieval gate asks to derive ``allowed_rooms``.

**Leaving is soft.** ``is_active = 0`` plus ``left_at`` and ``left_seq``, never a
row delete. Three things fall out of that, all of them wanted: the audit trail
can answer *"was this person in the room when that was said"*; the
``unique(room, user)`` constraint survives a leave-and-rejoin; and the Google
membership resource name survives with it, so a re-add reconciles instead of
blindly re-creating.

**Read state is a high-water mark, not a receipt table.** ``last_read_seq`` is
one integer per ``(user, room)``. The rejected alternative — a row per
``(user, message)`` — is the dominant scaling risk in the design by an order of
magnitude: a 20-person room turns 2,000 message writes a day into ~20,000
receipt writes a day, against a site whose current busiest table takes ~728
writes a day in total.

Phase 1 scope: schema only. The membership diff against Google, the derived-
membership reconciliation and the read-mark advance all belong to later phases.
This controller is deliberately empty — ``(room, user)`` uniqueness is enforced
by a composite index from a patch, not by a ``validate()`` doing a ``SELECT``,
because a check-then-insert is a TOCTOU bug at exactly the moment it matters:
two workers materialising the same per-document room at once.
"""

from frappe.model.document import Document


class ChatRoomMember(Document):
	pass
