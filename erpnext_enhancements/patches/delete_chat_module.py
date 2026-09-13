# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Delete everything the retired chat module left in the database (ADR 0011, v1.426.0).

Removing the `chat/` package stops the twenty-three DocTypes being *synced*. It does not
remove them from the site, and the framework only does half the job on its own — which is
the whole reason this file exists. Three separate framework behaviours, each verified
against `origin/version-16` rather than the sibling `develop` checkouts:

1. **`bench migrate` deletes the DocType ROWS by itself, and this patch runs first.**
   `post_schema_updates` calls `frappe.model.sync.remove_orphan_doctypes()`, which walks
   every non-custom DocType, calls `get_controller()`, and `delete_doc(..., force=True)`s
   the ones that raise `ImportError`. Patches run in `run_schema_updates`, i.e. **before**
   that sweep, so the rows are still present while this executes. We delete them here
   anyway, for the reason `delete_classic_training_builder` gives: a doctype left behind on
   a site where the sweep did not fire is a desk route that errors on click rather than one
   that is absent. Cheap to be certain.

2. **Nothing, anywhere, drops the TABLE.** `remove_orphan_doctypes`' own docstring says so
   ("Deleting the entry doesn't delete any data. So this is supposed to be non-destructive")
   and `delete_doc`'s DocType branch only clears `tabDocType`, its DocFields and DocPerms.
   So without the `DROP TABLE` pass below, a site keeps twenty-two `tabChat*` tables that no
   DocType describes — invisible to the desk, invisible to a fixture audit, and full of
   employee messages on a site where chat had been used.

3. **Deleting the `Chat Settings` DocType does NOT clear its `tabSingles` rows.**
   `delete_from_table` clears Singles only under `doctype != "DocType" and doctype == name`
   — true when you delete the Single *document*, false when you delete the *DocType* — so
   it takes the `frappe.db.delete("DocType", ...)` branch instead and the ~153 field rows
   survive as an orphan settings object. They must be removed with raw SQL, and it has to be
   raw: `tabSingles` has exactly three columns (`doctype`, `field`, `value`), so the obvious
   `frappe.db.exists("Singles", {...})` pre-check compiles to a query ending `ORDER BY
   creation` and raises `OperationalError (1054)` on every site. `tests/test_singles_table_access.py`
   fails the build on that spelling, and a patch that raises aborts the migrate.

-------------------------------------------------------------------------------------
What is deliberately NOT here
-------------------------------------------------------------------------------------

**No "disable chat first" step, and that is not an oversight.** The obvious belt-and-braces
opener — `frappe.db.set_single_value("Chat Settings", "google_sync_enabled", 0)` — is a
loaded gun on this doctype. Frappe synthesises a Single's DocField defaults only while
`tabSingles` holds *no* row for it, so on a site that never materialised `Chat Settings`
that one call creates the row and writes **None** into every other field: `dry_run_mode`
becomes 0 and `restrict_to_whitelist` becomes 0, which is the unsafe direction for both. A
patch written to make the integration safer would have made it live for the first time, on
the deploy that was removing it.

**No fixture cleanup, because chat had no fixtures.** `grep -ric chat fixtures/*.json`
returns 0 for all sixteen files, and there are no Custom Fields or Property Setters on any
Chat doctype. The roles and Notification Types below came from patches, never from
`fixtures/`, so the usual "remove the record from the JSON, then write a delete patch"
two-step does not apply — only the second half does.

**No `Logs To Clear` special-casing beyond a plain delete.** Frappe self-heals these:
`_supports_log_clearing()` wraps `get_controller` in try/except and
`remove_unsupported_doctypes()` drops the stale rows on the next save or daily run. It also
`msgprint`s from a background job while doing it, so they are removed here instead.

-------------------------------------------------------------------------------------
Mechanics
-------------------------------------------------------------------------------------

`post_model_sync`. Every step is independently guarded and the patch **cannot raise**: a
raising patch aborts `bench migrate`, which on this repo is the deploy, and failing to tidy a
dead table is not worth a failed deploy. Safe to run twice — the second run finds nothing.

Row counts are printed before anything is dropped, so the migrate log records what was
destroyed rather than asserting it was empty. Chat was never enabled on production, so the
expected output is a row of zeros; a non-zero count is worth reading before the next deploy
overwrites the log.
"""

import frappe

#: Every DocType the `Chat` module owned. Twenty-two have tables; `Chat Settings` is a
#: Single and has none. Listed literally rather than derived from the filesystem, because
#: the filesystem no longer has them — that is the point of the patch.
CHAT_DOCTYPES = (
	"Chat Allowed User",
	"Chat Attachment",
	"Chat Audit Log",
	"Chat Context Chunk",
	"Chat Drift Report",
	"Chat Event Subscription",
	"Chat Export Request",
	"Chat Inbound Event",
	"Chat Mention",
	"Chat Message",
	"Chat Message Revision",
	"Chat Ops Alert",
	"Chat Provisioning Run",
	"Chat Push Subscription",
	"Chat Relay Job",
	"Chat Retrieval Audit",
	"Chat Retrieval Audit Room",
	"Chat Room",
	"Chat Room Digest",
	"Chat Room Member",
	"Chat Thread Digest",
	"Triton Invocation Log",
)

#: The Single, kept apart because it needs the `tabSingles` pass and has no table.
CHAT_SINGLE = "Chat Settings"

#: Seeded by `patches/seed_chat_roles.py`, never by `fixtures/role.json`.
CHAT_ROLES = ("Chat User", "Chat Auditor")

#: Seeded by `patches/chat_phase4_notifications.py`. `Notification Log.type` is a Link on
#: v16, which is why they had to exist at all.
CHAT_NOTIFICATION_TYPES = ("Chat Message", "Chat Mention")

MODULE = "Chat"

#: The identifier fields that NAME the live Google resources, printed before they are
#: deleted. This is not diagnostics — it is the only surviving record of what to tear down.
#:
#: `Chat Settings` was the single place the topic, the two subscription names and the JWT
#: audience URL were written down; the repo has no other copy, and the runbook that recorded
#: the original provisioning (`RUNBOOK_google_cloud_setup.md`) is not in this repository.
#: Delete these rows without reading them and the teardown checklist in
#: `docs/google-chat-teardown.md` has to be completed by hunting through the GCP console.
#:
#: Safe to print: `Chat Settings` carries ZERO Password fields, by design and by test — the
#: whole auth model was keyless, so none of these is a credential. They are identifiers, and
#: the deploy log is already a privileged artefact.
GOOGLE_IDENTIFIER_FIELDS = (
	"google_project_id",
	"gcp_project_number",
	"workspace_domain",
	"delegation_service_account",
	"vm_service_account_email",
	"chat_app_service_account",
	"interaction_endpoint_url",
	"pubsub_topic",
	"pubsub_events_subscription",
	"pubsub_interaction_subscription",
)

LOG_TITLE = "delete_chat_module"


def execute():
	_report_google_identifiers()
	_report_row_counts()
	_delete_attached_files()
	_delete_single_rows()
	_delete_doctypes()
	_drop_tables()
	_delete_roles()
	_delete_notification_types()
	_delete_logs_to_clear()
	_delete_module_def()


def _log(note):
	"""Never raises, and never silently swallows either -- including the traceback.

	``frappe.log_error(title=..., message=...)`` looks like it files the message alongside a
	stack trace. It does not: v16's implementation reads
	``traceback = message`` whenever a message is supplied and the title is single-line, and
	only falls back to ``frappe.get_traceback()`` when that is empty. So passing a human
	sentence *replaces* the exception with it, and an Error Log row from this patch would say
	"Could not drop table tabChat Message" with no exception type, no line and no stack.

	That is a bad trade in any guarded ``except``; in a once-only patch that cannot be re-run
	and has already committed the deletions around it, it is the difference between a
	diagnosable failure and a mystery. So the note and the real traceback are concatenated and
	filed together.
	"""
	try:
		frappe.log_error(
			title=LOG_TITLE,
			message="%s\n\n%s" % (note, frappe.get_traceback(with_context=True)),
		)
	except Exception:
		pass


def _report_google_identifiers():
	"""Print which Google resources this integration was wired to, before losing the record.

	Read straight out of `tabSingles` with raw SQL, which is both the only way that works and
	the only way that is safe here. `Chat Settings` is a Single, its DocType is about to be
	deleted, and every `frappe.db` read helper defaults to `order_by="creation"` — a column
	`tabSingles` does not have — so an ORM read of it raises `OperationalError (1054)` on
	every site, every time.

	Prints nothing rather than a row of blanks when the Single was never materialised, which
	is the expected case here: chat was never enabled on production, so most of these were
	never filled in. An absent line means "there was nothing to write down", not "the read
	failed" — the failure path logs instead.
	"""
	try:
		rows = frappe.db.sql(
			"select field, value from tabSingles where doctype = %s and field in %s",
			(CHAT_SINGLE, tuple(GOOGLE_IDENTIFIER_FIELDS)),
		)
		found = [(f, v) for f, v in (rows or []) if (v or "").strip()]
		if not found:
			print(
				"%s: no Google identifiers were ever set -- nothing to tear down at Google "
				"beyond the Chat app registration itself." % LOG_TITLE
			)
			return
		print(
			"%s: Google resources this integration named, recorded here because deleting "
			"these rows destroys the only copy (see docs/google-chat-teardown.md):" % LOG_TITLE
		)
		for field, value in sorted(found):
			print("    %s = %s" % (field, value))
	except Exception:
		_log(
			"Could not read the Google identifier rows before deleting them. The teardown "
			"checklist in docs/google-chat-teardown.md must be completed from the GCP "
			"console instead."
		)


def _report_row_counts():
	"""Print what is about to be destroyed, before destroying it.

	The deploy log is the only place this is ever recorded: once the tables are dropped the
	question "was there anything in them" has no answer. Reads each table directly rather
	than through `frappe.db.count`, which would need a DocType that may already be gone.
	"""
	try:
		counts = []
		for doctype in CHAT_DOCTYPES:
			try:
				if not frappe.db.table_exists(doctype):
					continue
				n = frappe.db.sql("select count(*) from `tab{0}`".format(doctype))[0][0]
			except Exception:
				n = "?"
			if n:
				counts.append("%s=%s" % (doctype, n))
		print(
			"%s: rows about to be deleted -- %s"
			% (LOG_TITLE, ", ".join(counts) if counts else "none, every table is empty")
		)
	except Exception:
		_log("Could not report row counts; continuing with the teardown.")


def _delete_attached_files():
	"""Delete the `File` documents attached to a chat record, and their bytes.

	`File` is not a chat doctype and no step below touches it, so a chat attachment or a
	governance export bundle would survive this patch as a private `File` row pointing at a
	`Chat Attachment` that no longer exists -- plus the actual bytes still on disk under
	`private/files/`. The row is invisible (its `attached_to_doctype` names a deleted
	DocType), unreachable through any list view, and permanent.

	Deleted through `frappe.delete_doc` rather than raw SQL precisely BECAUSE the bytes
	matter: `File.on_trash` is what unlinks the file from the filesystem, and a raw
	`delete from tabFile` would orphan every one of them.

	Expected to find nothing on this site -- chat was never enabled, so nothing was ever
	uploaded -- which is why it prints only when it actually deletes something.
	"""
	try:
		names = frappe.db.sql_list(
			"select name from `tabFile` where attached_to_doctype in %(doctypes)s",
			{"doctypes": tuple(CHAT_DOCTYPES)},
		)
	except Exception:
		_log("Could not enumerate the File rows attached to chat records.")
		return
	if not names:
		return
	deleted = 0
	for name in names:
		try:
			frappe.delete_doc("File", name, force=True, ignore_missing=True, delete_permanently=True)
			deleted += 1
		except Exception:
			_log("Could not delete File %s (attached to a chat record)." % name)
	print("%s: deleted %d attached File(s) and their bytes" % (LOG_TITLE, deleted))


def _delete_single_rows():
	"""Drop the `tabSingles` rows for `Chat Settings`.

	Raw SQL on purpose, and the only correct spelling — see the module docstring's point 3.
	`tabSingles` is not a doctype table and every ORM read of it raises.
	"""
	try:
		frappe.db.sql("delete from tabSingles where doctype = %s", (CHAT_SINGLE,))
		frappe.clear_document_cache(CHAT_SINGLE, CHAT_SINGLE)
	except Exception:
		_log("Could not delete the tabSingles rows for %s." % CHAT_SINGLE)


def _delete_doctypes():
	"""Remove the DocType rows, their DocFields and their DocPerms.

	`force=True` because a Chat Room with messages linked to it would otherwise refuse, and
	the whole module is going. `ignore_missing=True` because `remove_orphan_doctypes` may
	have taken one first on a re-run, and because this patch must be safe twice.
	"""
	for doctype in CHAT_DOCTYPES + (CHAT_SINGLE,):
		try:
			if not frappe.db.exists("DocType", doctype):
				continue
			frappe.delete_doc("DocType", doctype, force=True, ignore_missing=True)
		except Exception:
			_log("Could not delete DocType %s; the orphan sweep will retry it." % doctype)


def _drop_tables():
	"""The step nothing in the framework does.

	`DROP TABLE IF EXISTS` rather than a guarded `table_exists` + `DROP`, so a concurrent
	drop cannot turn this into an error. Backticked because every one of these names
	contains spaces.
	"""
	for doctype in CHAT_DOCTYPES:
		try:
			frappe.db.sql_ddl("drop table if exists `tab{0}`".format(doctype))
		except Exception:
			_log("Could not drop table tab%s." % doctype)


def _delete_roles():
	"""`Chat User` and `Chat Auditor`, and **their `Has Role` rows first**.

	Deleted rather than disabled: a disabled role still appears in every role picker, and
	"why can I assign a role for a feature that does not exist" is a question somebody asks a
	year later.

	THE CHILD ROWS ARE THE WHOLE POINT OF THIS FUNCTION, and the intuition is wrong.
	Frappe does NOT remove `Has Role` rows when the Role is deleted. Verified against
	`origin/version-16`: `Role` declares no `on_trash`, and the only
	`frappe.db.delete("Has Role", {"role": ...})` in the framework lives in
	`Role.remove_roles()`, reachable solely from `validate()` when `disabled` is ticked.
	`delete_from_table` clears child rows whose `parenttype` is the deleted doctype, and a
	`Has Role` row's parenttype is `User` or `Role Profile` -- never `Role`.

	`force=True` is what makes that silent: without it `check_if_doc_is_linked` would raise
	`LinkExistsError` and this function's `except` would log a visible failure. With it, the
	orphan rows just stay.

	And an orphan `Has Role` row is not cosmetic. `Document._save` runs `_validate_links()`
	BEFORE `run_before_save_methods()`, and it walks every child row -- so the row throws
	`LinkValidationError: Could not find Role: Chat User` before `User.validate()` gets a
	chance to rebuild roles from the profile. That user then cannot be saved from the Desk at
	all, and neither can anything that saves them: the enable/disable toggle, a Role Profile
	change, HR onboarding, `training.assignment.on_user_roles_changed`. It surfaces days
	later as "I can't save this user", naming a feature nobody remembers.

	Measured on production 2026-09-13, before this shipped: four such rows existed, two for
	each role, all with parenttype `User`. This is not hypothetical.
	"""
	for role in CHAT_ROLES:
		try:
			# First, and unconditionally -- the rows can outlive the Role by a failed delete.
			frappe.db.delete("Has Role", {"role": role})
			if not frappe.db.exists("Role", role):
				continue
			frappe.delete_doc("Role", role, force=True, ignore_missing=True)
		except Exception:
			_log("Could not delete Role %s or its Has Role rows." % role)


def _delete_notification_types():
	"""The two `Notification Type` records the bell rows pointed at.

	Left behind they are harmless but misleading — a notification type nothing can ever
	emit, offered in the Notification Settings UI as something a user can toggle.
	"""
	for name in CHAT_NOTIFICATION_TYPES:
		try:
			# `Notification Log.type` is a Link to this doctype on v16 -- which is the whole
			# reason these records had to exist. Deleting the target without the rows leaves
			# log entries whose Link is dangling, and the same `_validate_links` problem as
			# the Role case above applies to anything that ever re-saves one. Three such rows
			# existed on production when this was written.
			frappe.db.delete("Notification Log", {"type": name})
			if not frappe.db.exists("Notification Type", name):
				continue
			frappe.delete_doc("Notification Type", name, force=True, ignore_missing=True)
		except Exception:
			_log("Could not delete Notification Type %s or its Notification Log rows." % name)


def _delete_logs_to_clear():
	"""`Chat Relay Job` and `Chat Inbound Event` rows on `Log Settings`.

	A child table, so this is a direct delete on the child rather than a save of the parent:
	loading `Log Settings` to edit its table would re-run its own validation against rows for
	doctypes that no longer exist.
	"""
	try:
		frappe.db.sql(
			"delete from `tabLogs To Clear` where ref_doctype in %(doctypes)s",
			{"doctypes": tuple(CHAT_DOCTYPES)},
		)
		frappe.clear_document_cache("Log Settings", "Log Settings")
	except Exception:
		_log("Could not delete the Logs To Clear rows for the chat log tables.")


def _delete_module_def():
	"""The `Module Def` row.

	Last, because a Module Def cannot go while a DocType still claims it. `modules.txt` no
	longer lists `Chat`, so nothing recreates this — `add_module_defs` runs on install-app
	and reads that file, and on migrate a Module Def only ever appears as a side effect of a
	DocType import.
	"""
	try:
		if not frappe.db.exists("Module Def", MODULE):
			return
		frappe.delete_doc("Module Def", MODULE, force=True, ignore_missing=True)
		print("%s: removed Module Def %s" % (LOG_TITLE, MODULE))
	except Exception:
		_log("Could not delete Module Def %s." % MODULE)
