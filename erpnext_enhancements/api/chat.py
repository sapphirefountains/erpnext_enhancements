# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The chat HTTP surface — one function in Phase 1, and that is the design.

Phase 1 ships no UI at all, so the only thing a browser could legitimately ask for is
*"is this feature on?"*. :func:`get_settings_public` answers that and nothing else. The
real surface — history paging, the room list, member list, mention autocomplete, search,
``mark_read``, ``set_typing`` — is Phase 3's, and every one of those calls has a
permission story that has to be written with it rather than retrofitted.

**Feature flags only.** ``Chat Settings`` holds Google identifiers — project ids,
service-account email addresses, the Pub/Sub topic, the byte-exact interaction endpoint
URL — and none of them belong in a response any authenticated user can fetch. They are
not secrets (there is no credential anywhere in this design; see
``chat/doctype/chat_settings/chat_settings.py``), but a service-account address and a
topic name are reconnaissance, and there is no reason for a browser to hold them. The
allowlist below is therefore a positive list of ``Check`` fields: a field added to
``Chat Settings`` tomorrow is invisible here until somebody deliberately names it.

Indentation is 4 spaces, matching the majority of ``api/`` (see ``api/README.md`` for
which files in this package are tabs).
"""

import frappe
from frappe.utils import cint

# The flags a client may see, each because a client behaves differently when it flips.
# Deliberately excluded, with reasons, so the exclusions survive review:
#   restrict_to_whitelist   pilot configuration; who is in the pilot is not the client's
#                           business, and the server enforces it on every call anyway
#   pause_*                 incident state. Phase 3 can surface "outbound is paused" as a
#                           banner if it earns it; leaking the incident switches before
#                           there is a UI to use them is noise
#   import_mode_enabled     a migration tool, never steady state
#   store_retrieval_query_text, log_message_bodies
#                           privacy/audit posture, meaningful only to an auditor
#   every identifier field  see the module docstring
_PUBLIC_FLAGS: tuple[str, ...] = (
    "enabled",
    "dry_run_mode",
    "google_sync_enabled",
    "relay_outbound_enabled",
    "relay_inbound_enabled",
    "mirror_attachments",
    "threading_enabled",
    "triton_enabled",
    "document_spaces_enabled",
    "org_structure_mirroring_enabled",
)


@frappe.whitelist()
def get_settings_public() -> dict[str, bool]:
    """Return the chat feature flags as booleans.

    Returns:
        dict: one key per flag in :data:`_PUBLIC_FLAGS`, every value a ``bool``.

    Never raises and never returns a partial shape. A site that has this code but has not
    run ``bench migrate`` yet has no ``Chat Settings`` row, and a boot call that 500s
    because a feature is not installed is worse than one that reports the feature off —
    the same reasoning as ``api/desk_shortcuts.py``. Chat ships dormant, so all-off is
    also the truthful answer in that state.

    Not guest-accessible (plain ``@frappe.whitelist()``), and Guest is refused explicitly
    as well: this reveals nothing dangerous, but an unauthenticated caller has no chat and
    an endpoint that answers Guest is one somebody later hangs data off.
    """
    if frappe.session.user in ("", None, "Guest"):
        return _all_off()

    try:
        values = frappe.db.get_singles_dict("Chat Settings") or {}
    except Exception:
        # Pre-migrate, or the Single has never been saved. Report the feature off rather
        # than breaking the caller's boot.
        return _all_off()

    return {flag: bool(cint(values.get(flag))) for flag in _PUBLIC_FLAGS}


def _all_off() -> dict[str, bool]:
    """The full flag shape, all false — so callers never have to test for a missing key."""
    return {flag: False for flag in _PUBLIC_FLAGS}
