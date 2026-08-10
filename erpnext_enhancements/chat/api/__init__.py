# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The chat HTTP surface the SPA runs on — Phase 3's server half.

Phase 1 shipped one whitelisted method (``api/chat.py:get_settings_public``) and Phase 2
shipped six operational ones (the inbound webhook, attachment download, relay retry, two
provisioning starters, document-room creation). **There was no read API at all** — no
``get_rooms``, no ``get_messages``, no ``send_message``. So Phase 3 does not wire a UI to
an existing API; it builds the API.

Why this package lives under ``chat/`` and not in ``api/``
==========================================================

``tests/test_chat_rawsql_guard.py`` walks ``erpnext_enhancements/chat/**/*.py`` with
``ast`` and fails the build on any raw query against a conversation-bearing table that
does not AND in :func:`chat.permissions.membership_filter_sql`. This package is *made of*
raw queries — keyset paging and search are exactly the two places somebody reaches for
``frappe.db.sql`` — so it has to sit inside the directory the guard scans. Putting the
read surface in ``api/chat.py`` would have moved the single most leak-prone code in the
feature outside its only automated guard. ``api/chat.py`` keeps the feature-flag endpoint
and gains nothing else.

The rule every module here follows
==================================

**One membership decision per request, taken once, at the top.** Every whitelisted method
begins with :func:`_common.require_room` (or :func:`_common.require_session`), which
resolves the caller, refuses Guest, checks the pilot gate, and — for room-scoped calls —
asserts active membership through the same helper the permission hooks use. Nothing below
that line re-derives who the caller is, and nothing below it takes a room name on trust.

**Raw SQL carries its own filter.** Not because the hooks are unreliable but because they
are not consulted: ``frappe.db.sql`` and ``frappe.get_all`` both bypass the permission
stack entirely. The filter is ANDed in from ``permissions.membership_filter_sql`` rather
than hand-written, so the raw paths and the hook paths cannot drift.

**Ordering is ``seq``, never a timestamp.** Not ``creation``, not Google's ``createTime``.
``seq`` is immutable, allocated under the room's row lock, and has no timezone. A row with
``late_arrival = 1`` renders in ``seq`` order with a "recovered" affordance rather than
being re-sorted into place. (The Phase 3 brief §4.13 says "keyed on ``creation``"; the
schema and the Phase 2 handoff both say ``seq``, and the schema wins — see
``chat/README.md`` for the divergence note.)

**Deleted rows keep their body.** ``is_deleted = 1`` rows retain ``text`` on purpose:
Google's tombstone is content-free, so ERPNext is the only copy and Phase 6's audit needs
it. Every read path here therefore filters ``is_deleted`` itself and returns a tombstone
shape — the row will happily hand you a deleted body if you ask for it.

Indentation is tabs, per ``CLAUDE.md`` and the Frappe convention this package follows.
"""
