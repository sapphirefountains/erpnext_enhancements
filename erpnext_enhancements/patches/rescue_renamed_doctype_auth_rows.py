# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Re-point the `__Auth` rows that `rename_poseidon_settings_doctype` left behind.

--------------------------------------------------------------------------------------
`frappe.rename_doc` on a DocType does NOT move its stored passwords
--------------------------------------------------------------------------------------

`patches/rename_poseidon_settings_doctype.py` renamed the Single **Poseidon Settings ->
Triton Settings**, and its docstring says the rename carries "the configured Gateway URL,
**secrets**, prompts and Twilio credentials" across. The first and third are true. **The
secrets are not.**

A Password field stores a *masked placeholder* in `tabSingles` and the real, encrypted value
in the separate `__Auth` table, keyed by `(doctype, name, fieldname)`. `rename_doc` rewrites
`tabSingles`; it does not rewrite `__Auth`. So after the rename:

* `tabSingles` has a `maps_api_key` row for **Triton Settings** — the placeholder came across,
* `__Auth` still files the real value under **Poseidon Settings**,
* and `Triton Settings.get_password("maps_api_key")` returns `None`.

**The failure is invisible from the Desk**, which is why it survived so long: a Password field
renders blank whether or not a value is stored, so the form looks exactly the same as a
correctly-configured one. The only way to see it is to ask the server to decrypt.

--------------------------------------------------------------------------------------
What was actually broken
--------------------------------------------------------------------------------------

Four secrets were stranded. Two — `admin_webhook_secret` and `twilio_api_secret` — were
later re-entered by hand under the new name, which is presumably how somebody noticed
something was wrong. **Two were not**, and both fail silently or invisibly:

* **`maps_api_key`** — read by `api/gemini.generate_content_with_vertex_ai`, which
  `frappe.throw`s "Vertex AI API Key (maps_api_key) is missing in Triton Settings". Every
  Vertex feature in the app has therefore never worked on this site: the morning briefing's
  Gemini narrative, `api/communication`'s email/SMS drafts, `api/training_ai`'s quiz and
  checkpoint drafting, and `assistant_tools/draft_course_spec`. Seven Error Log rows
  ("Enhancement Request description draft failed") carry that exact message.
* **`twilio_auth_token`** — read by `api/telephony.validate_twilio_request`, which builds a
  `RequestValidator("")` and therefore rejects **every** inbound Twilio webhook with
  PermissionError (`receive_mms` is guest-facing and guarded by it); and by
  `api/call_recording_export`, which sends HTTP basic auth with an empty password and gets a
  401. Note the direction: this one fails **closed**. Nothing was let through.

--------------------------------------------------------------------------------------
How this moves them
--------------------------------------------------------------------------------------

By rewriting the `doctype` and `name` columns of the row — **never by decrypting it.** Frappe
encrypts these with the site's own `encryption_key`; the ciphertext does not depend on the
doctype name, so re-filing the row is sufficient and the plaintext is never materialised, not
in memory, not in a variable, not in a log. This patch never reads the `password` column.

**Only fieldnames that have no row under the new name are moved.** `admin_webhook_secret` and
`twilio_api_secret` exist under both, and the hand-entered ones are newer and are what the
site has actually been running on — overwriting them with a pre-rename value would be an
unannounced credential rollback. They are left exactly where they are, and the orphans are
deleted rather than moved so the table does not keep a second copy of a live secret.

Written against `__Auth` directly because there is no supported API for "re-file an existing
encrypted value under a different owner": `set_encrypted_password` takes a plaintext, and
getting one would mean decrypting first. A three-column UPDATE is both safer and smaller than
a decrypt/re-encrypt round trip.

Idempotent: the second run finds no rows under the old name. Guarded and wrapped, because a
patch that raises aborts `bench migrate`, which on this repo is the deploy.
"""

import frappe

OLD = "Poseidon Settings"
NEW = "Triton Settings"


def execute():
	try:
		_rescue()
	except Exception:
		# Never abort the deploy over this. The secrets stay where they are and the
		# next migrate tries again.
		frappe.log_error(
			title="rescue_renamed_doctype_auth_rows",
			message="Could not re-file __Auth rows from %s to %s" % (OLD, NEW),
		)


def _rescue():
	if not frappe.db.exists("DocType", NEW):
		return

	# Fieldnames only. The `password` column is never selected, here or anywhere below.
	stranded = {
		row.fieldname
		for row in frappe.db.sql(
			"select fieldname from `__Auth` where doctype = %s and name = %s",
			(OLD, OLD),
			as_dict=True,
		)
	}
	if not stranded:
		return

	already = {
		row.fieldname
		for row in frappe.db.sql(
			"select fieldname from `__Auth` where doctype = %s and name = %s",
			(NEW, NEW),
			as_dict=True,
		)
	}

	moved = sorted(stranded - already)
	superseded = sorted(stranded & already)

	for fieldname in moved:
		frappe.db.sql(
			"""
			update `__Auth`
			set doctype = %s, name = %s
			where doctype = %s and name = %s and fieldname = %s
			""",
			(NEW, NEW, OLD, OLD, fieldname),
		)

	for fieldname in superseded:
		# A newer value was entered by hand under the new name after the rename. That is
		# the one the site runs on; the pre-rename copy is stale and is removed rather
		# than left lying in the table.
		frappe.db.sql(
			"delete from `__Auth` where doctype = %s and name = %s and fieldname = %s",
			(OLD, OLD, fieldname),
		)

	frappe.clear_document_cache(NEW, NEW)
	print(
		"rescue_renamed_doctype_auth_rows: moved %s; dropped stale %s"
		% (", ".join(moved) or "nothing", ", ".join(superseded) or "nothing")
	)
