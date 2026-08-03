"""Move the Drive service-account key out of cleartext (v1.211.0).

``Project Folder Google Drive Settings.service_account_json`` was a ``Code``
field, which means the private key was stored verbatim in ``tabSingles.value``
and rendered back onto the form. Anyone who reached that page as Administrator
could read a full-Drive-scope credential straight off the screen — no database
access, no server script, no decryption. After the 2026-08-02 intrusion, in which
someone logged in as Administrator ten times, that stopped being hypothetical.

Changing the fieldtype alone does **not** move the value: a `bench migrate` would
leave the cleartext sitting in ``tabSingles`` and the app would then read an
asterisk placeholder, so Drive and Calendar would break *and* the secret would
still be exposed. This patch is what actually relocates it.

Runs ``post_model_sync``, i.e. after the DocType JSON has been applied, so
``set_password`` writes to ``__Auth`` under the new fieldtype.

Idempotent: if the stored value is already the placeholder (all asterisks) or the
field is empty, it does nothing.

**Rotate the key anyway.** Encrypting it now does not un-expose whatever was
readable before, and this patch cannot know who read it.
"""

import frappe

DOCTYPE = "Project Folder Google Drive Settings"
FIELD = "service_account_json"


def execute():
	if not frappe.db.exists("DocType", DOCTYPE):
		return

	rows = frappe.db.sql(
		"""select value from `tabSingles` where doctype = %s and field = %s""",
		(DOCTYPE, FIELD),
	)
	raw = (rows[0][0] if rows and rows[0][0] else "").strip()
	if not raw:
		return

	# Already migrated: Frappe leaves an asterisk placeholder of equal length in
	# tabSingles once the real value lives in __Auth.
	if set(raw) <= {"*"}:
		return

	try:
		doc = frappe.get_single(DOCTYPE)
		# Assigning and saving routes the value through save_passwords(), which
		# writes __Auth and replaces the column with the placeholder.
		doc.set(FIELD, raw)
		doc.save(ignore_permissions=True)
		frappe.db.commit()
	except Exception:
		# Never abort a migrate over this. The old value stays where it was, which
		# is no worse than before the patch ran, and the error is on record.
		frappe.log_error(
			f"Could not encrypt {DOCTYPE}.{FIELD}\n{frappe.get_traceback()}",
			"Drive key encryption",
		)
		return

	# Belt and braces: confirm the column no longer holds the key itself. If the
	# save did not do what we expect, overwrite the column directly rather than
	# leaving cleartext behind.
	rows = frappe.db.sql(
		"""select value from `tabSingles` where doctype = %s and field = %s""",
		(DOCTYPE, FIELD),
	)
	current = (rows[0][0] if rows and rows[0][0] else "").strip()
	if current and set(current) > {"*"}:
		frappe.db.sql(
			"""update `tabSingles` set value = %s where doctype = %s and field = %s""",
			("*" * len(current), DOCTYPE, FIELD),
		)
		frappe.db.commit()
