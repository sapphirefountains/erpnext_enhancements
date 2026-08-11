# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Phase 4's install step: the two ``Notification Type`` records, and the presence retune.

Two entry points, and both are required. :func:`execute` is the ``patches.txt`` line and may
raise. :func:`ensure_chat_phase4_notifications` is the ``after_migrate`` / ``after_install``
backstop and never raises, because ``after_migrate`` runs on every deploy and a raise there
does not report one bad default — it bricks the deploy pipeline until somebody edits
``hooks.py``.

**Why the backstop is not paranoia.** ``bench install-app`` calls
``set_all_patches_as_completed()``, which writes a ``Patch Log`` row for every line of
``patches.txt`` **without executing any of it**. A fresh site therefore records this patch as
done and never runs it. That is not hypothetical here: on 2026-08-09 ``add_chat_indexes``
burned its entry against a schema that did not exist yet, and the composite indexes existed
on no site until the backstop was added.

--------------------------------------------------------------------------------------
Part one: the Notification Type records
--------------------------------------------------------------------------------------

``Notification Log.type`` is a **Link** to a real ``Notification Type`` record in v16 —
confirmed against the deployed build, which holds exactly five (Alert, Assignment, Energy
Point, Mention, Share). Inserting a row with ``type = "Chat Mention"`` before that record
exists is a link-validation failure, so **every chat notification would fail to insert** and
the only evidence would be an Error Log entry per message.

Deliberately an installer rather than a fixture: removing a fixture record is a two-step
dance (drop it from the JSON *and* write a patch that deletes it), and these two names are
public API the moment they ship — ``notification_skip_email_types`` is keyed by name, so
renaming one silently re-enables email for it.

--------------------------------------------------------------------------------------
Part two: retuning presence, and why it is conditional
--------------------------------------------------------------------------------------

Phase 3 shipped ``presence_heartbeat_seconds = 30`` / ``presence_ttl_seconds = 75``, copied
from ``collab/live_form_sync.js``. Phase 4 changed the shipped defaults to 20 / 55, because
**30 s is exactly the Google Cloud load balancer's idle-connection cut** and the only reason
realtime works on this site is socket.io's 25 s ping beating it by five seconds (ADR §H.3.1).

Changing the DocField default is not enough, and this is the trap. Frappe synthesises a
Single's DocField defaults only while ``tabSingles`` holds no row for it — and production's
row exists and holds 30 and 75 today (measured 2026-08-11). So a new default applies to
nothing that already exists, and the site would keep running on the old pair with the new
numbers written in the code, which is the worst of both.

It is conditional on the stored value still being Phase 3's: an operator who has deliberately
chosen something else has made a decision, and a patch that overwrote it would be a migration
silently disagreeing with a human. Only the untouched default moves.

Indentation is tabs, per ``CLAUDE.md`` and the Frappe convention this package follows.
"""

from __future__ import annotations

from typing import Any, Final

import frappe

#: The pair Phase 3 shipped. Only a value still equal to one of these is moved.
_PHASE3_DEFAULTS: Final[dict[str, str]] = {
	"presence_heartbeat_seconds": "30",
	"presence_ttl_seconds": "75",
}

#: What Phase 4 ships instead. Keep in step with ``chat/notifications/policy.py`` — the
#: constants there are the source of truth and these are the migration of the stored copy.
_PHASE4_VALUES: Final[dict[str, str]] = {
	"presence_heartbeat_seconds": "20",
	"presence_ttl_seconds": "55",
}

#: New in Phase 4, so absent from every existing ``tabSingles`` row. Seeded rather than left
#: blank because a blank reads as 0, and a blur grace of 0 means "notify the instant anybody
#: looks away" — a defensible choice, but not one anybody made.
_PHASE4_SEEDS: Final[dict[str, str]] = {
	"blur_grace_seconds": "120",
}


def execute() -> None:
	"""The patch. May raise; it runs once, under ``bench migrate``, where a failure is loud."""
	created = _install_notification_types()
	moved = _retune_presence()
	seeded = _seed_new_settings()

	# stdout rather than a log: this runs under `bench migrate`, where the operator is
	# watching, and "nothing to do" is as useful to see as a list of changes.
	print(
		"chat phase 4: notification types created="
		f"{created or 'none'}, presence retuned={moved or 'none'}, seeded={seeded or 'none'}"
	)


def ensure_chat_phase4_notifications() -> None:
	"""The ``after_migrate`` / ``after_install`` backstop. **Never raises.**

	Registered on both because ``after_migrate`` does not run during ``bench install-app``.
	Idempotent by construction: every branch below is a "create if absent" or a "change only
	if it still holds the value we shipped", so running it on every deploy forever costs three
	reads.
	"""
	try:
		execute()
	except Exception:
		# `raise ... from None` is not used here because nothing is re-raised at all — but the
		# reason is the same one recorded in `default_chat_settings`: a bare re-raise out of a
		# migrate frame publishes the frame locals to the Error Log, and this frame holds a
		# settings document.
		frappe.log_error(
			title="chat phase 4 notification setup failed",
			message="Notification types and/or presence retune did not apply; see the trace above "
			"this row. Chat notifications will fail to insert until the Notification Type "
			"records exist, because Notification Log.type is a Link.",
		)


# --------------------------------------------------------------------------- parts


def _install_notification_types() -> list[str]:
	"""Delegate to the module that owns the names, so there is one list rather than two."""
	from erpnext_enhancements.chat.notifications.bell import install_notification_types

	return install_notification_types()


def _retune_presence() -> dict[str, str]:
	"""Move the two presence constants off Phase 3's pair, and only off Phase 3's pair."""
	moved: dict[str, str] = {}
	stored = _singles()
	if stored is None:
		return moved

	for field, phase3 in _PHASE3_DEFAULTS.items():
		current = stored.get(field)
		if current is None:
			# The field has never been written on this site, so the DocField default — already
			# Phase 4's number — applies on the next read. Nothing to migrate.
			continue
		if str(current).strip() != phase3:
			continue
		_set(field, _PHASE4_VALUES[field])
		moved[field] = _PHASE4_VALUES[field]

	return moved


def _seed_new_settings() -> dict[str, str]:
	"""Write the Phase 4 fields that did not exist when this site's Single was materialised."""
	seeded: dict[str, str] = {}
	stored = _singles()
	if stored is None:
		return seeded

	for field, value in _PHASE4_SEEDS.items():
		# Only a genuinely absent key. A stored "0" is a deliberate operator choice — see
		# `default_chat_settings._is_set`, which learned the same lesson: tabSingles stores
		# everything as text, so a chosen zero arrives as "0" and must not read as unset.
		if field in stored:
			continue
		_set(field, value)
		seeded[field] = value

	return seeded


def _singles() -> dict[str, Any] | None:
	"""Every stored ``Chat Settings`` field, or ``None`` if the Single does not exist yet.

	Read from ``tabSingles`` directly rather than through ``get_single_value``, because the
	distinction this patch turns on is **absent versus written**, and ``get_single_value``
	collapses both to the DocField default.
	"""
	try:
		if not frappe.db.exists("DocType", "Chat Settings"):
			return None
		return dict(frappe.db.get_singles_dict("Chat Settings") or {})
	except Exception:
		return None


def _set(field: str, value: str) -> None:
	"""One settings field, written without running ``validate``.

	``db_set``-style rather than a loaded-and-saved document on purpose: ``ChatSettings.save``
	runs the whole budget, retention and secret-material rule set, and a migration that fails
	because an unrelated field is misconfigured is a migration that blocks the deploy for a
	reason nobody can act on from the error.
	"""
	frappe.db.set_value("Chat Settings", "Chat Settings", field, value, update_modified=False)
