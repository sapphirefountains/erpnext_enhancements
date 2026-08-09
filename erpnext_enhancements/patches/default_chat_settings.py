"""Seed `Chat Settings` **dormant** — the whole system ships inert and is switched on by hand.

Nothing in the chat module should do anything on the day it deploys. `enabled = 0` closes
the master gate, `dry_run_mode = 1` means every Google call is synthesised locally with zero
I/O even if the gate is opened by accident, `restrict_to_whitelist = 1` plus an **empty**
allow-list means nobody is in the pilot, and the retention dials are off so no purge can run
against data that does not exist yet. Turning chat on is then four deliberate ticks by a
System Manager, in that order, and each of them is visible in the document's version history.

**Why a patch at all, when the DocFields already carry these defaults.** A `default` applies
at document *creation*. Two ways that is not enough here:

* On a site where somebody opened and saved the settings page before this patch shipped, the
  Single already exists and a new field comes up blank rather than defaulted — the same trap
  `default_handoff_settings` and `default_po_approval_threshold` exist for. Blank on a
  `Check` reads as 0, which is the safe direction for `enabled` and the **unsafe** direction
  for `dry_run_mode` and `restrict_to_whitelist`.
* Frappe materialises a Single's defaults only while `tabSingles` holds **no** row for it
  (`Document.load_from_db` falls back to `new_doc` in that case). The moment anything writes
  one field, every *unwritten* field reads `None` instead of its declared default — so a
  partial write would leave `message_byte_limit`, the retry budget and the Triton tier
  budgets all reading zero. That is why this patch saves the **document** rather than poking
  individual `tabSingles` rows: one save materialises the whole default set consistently.

Blank-fill only. An operator who has deliberately ticked something is never clobbered, so a
re-run — or a re-run after the pilot has started — is a no-op. Safe to run twice.

**Two entry points, for the same reason `add_chat_indexes` has two.** :func:`execute` is the
patch and is allowed to fail the migrate; :func:`ensure_chat_settings` is registered in
``hooks.py``'s ``after_migrate`` and logs instead. A patch is not guaranteed to run: ``bench
install-app`` calls ``set_all_patches_as_completed``, so on a fresh site this patch is written
straight to Patch Log and never executes — and on 2026-08-09 it *did* execute, before the
DocType existed, which spends the Patch Log entry just as permanently. Both roads end at an
unmaterialised Single, and an unmaterialised Single is a loaded gun: Frappe synthesises the
DocField defaults only while ``tabSingles`` holds **no** row, so the first partial write flips
every unwritten field to ``None`` — ``dry_run_mode`` and ``restrict_to_whitelist`` then read 0,
which is the unsafe direction for both.
"""

import frappe

#: The dormancy set, in the order a human would tick them going the other way.
#: Everything else on the Single keeps its DocField default; these four are named here
#: because getting one of them wrong is the difference between "ships inert" and "ships
#: talking to Google".
DORMANT_DEFAULTS: dict[str, int] = {
	# The master gate. `chat.doctype.chat_settings.chat_settings.is_chat_enabled()` reads
	# this AND `not dry_run_mode`; every path that talks to Google is required to consult it.
	"enabled": 0,
	# The zero-I/O mode. `chat.gchat.dryrun` synthesises visibly-fake responses
	# (`spaces/DRYRUN-…`) so Phase 2's reconciliation can detect and skip them.
	"dry_run_mode": 1,
	# Pilot gating, enforced server-side by `is_user_allowed()`. With an empty
	# `allowed_users` table this means "nobody except Administrator".
	"restrict_to_whitelist": 1,
	# Retention off. 0 = never purge; the purge jobs do not exist until Phase 6 and a
	# non-zero window here would be a deletion policy nobody chose.
	"message_retention_days": 0,
	"hard_delete_after_days": 0,
}


def execute() -> None:
	"""Blank-fill :data:`DORMANT_DEFAULTS` on `Chat Settings`, materialising the Single."""
	if not frappe.db.exists("DocType", "Chat Settings"):
		# Mid-install, or the DocType was never synced. Nothing to seed and nothing wrong.
		return

	# A Single keeps its values in `tabSingles`, so the usual has_column guard does not
	# apply — read what is actually stored and check the meta for the field's existence.
	stored = frappe.db.get_singles_dict("Chat Settings") or {}
	meta = frappe.get_meta("Chat Settings")

	settings = frappe.get_single("Chat Settings")
	changed = not stored  # never saved: one save materialises every declared default

	for fieldname, value in DORMANT_DEFAULTS.items():
		if not meta.get_field(fieldname):
			continue
		if _is_set(stored, fieldname):
			# A deliberate operator choice. Leaving the pilot switched on through a
			# re-run is correct; silently switching it off would be an outage.
			continue
		settings.set(fieldname, value)
		changed = True

	if not changed:
		return

	# ignore_permissions: patches run as Administrator during migrate, and this Single is
	# System-Manager-only. validate() still runs — the shipped defaults satisfy the budget
	# and retention arithmetic, and if they ever stop doing so the migrate should say so.
	settings.save(ignore_permissions=True)


def ensure_chat_settings() -> None:
	"""The ``after_migrate`` entry point. Same work, **never raises**.

	Runs on every migrate and every deploy is a migrate, so a raise here would not report one
	bad default — it would brick the deploy pipeline until somebody edited ``hooks.py``. The
	patch runs once and may fail loudly; this logs and returns.

	Cost on a healthy site: one ``get_singles_dict`` and no write.
	"""
	try:
		execute()
	except Exception:
		# Never re-raise out of a migrate hook: a bare re-raise from inside a job publishes
		# the frame locals to the Error Log, and this frame holds the whole settings document.
		try:
			frappe.log_error(
				title="default_chat_settings",
				message="ensure_chat_settings could not seed the Chat Settings Single; "
				"it stays unmaterialised and reads DocField defaults until the next migrate",
			)
		except Exception:
			pass


def _is_set(stored: dict, fieldname: str) -> bool:
	"""Has this field already been written to `tabSingles` with a real value?

	`tabSingles` stores everything as text, so a deliberate ``0`` arrives as ``"0"`` — which
	is falsy-looking and must **not** be treated as unset. Only a missing key, ``None`` or
	an empty string count as "never chosen".
	"""
	if fieldname not in stored:
		return False
	value = stored.get(fieldname)
	return value is not None and str(value).strip() != ""
