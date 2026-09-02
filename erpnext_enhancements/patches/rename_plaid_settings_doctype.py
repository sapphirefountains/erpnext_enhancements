"""Un-collide our "Plaid Settings" from ERPNext's, and give ERPNext its own back (PRE_model_sync).

ERPNext v16 ships a Single DocType named **"Plaid Settings"** (module ERPNext
Integrations: ``enabled``, ``automatic_sync``, ``plaid_client_id``, ``plaid_secret``,
``plaid_env``, ``enable_european_access``) behind its native Plaid Link + transactions
sync. This app's ``plaid_banking`` module (v1.148.0) shipped a *second* DocType with
the same name. Two DocTypes with one name can never coexist: ``sync_all`` imports a
DocType JSON whenever its ``migration_hash`` differs from the stored one (the
``modified`` timestamp is only consulted for non-DocType records --
frappe/modules/import_file.py, version-16), so on every migrate erpnext's JSON was
imported and then ours, in app-install order, and the later-installed app -- this one --
won. On prod "Plaid Settings" therefore read ``module = Plaid Banking`` with OUR field
list, while the rows in ``tabSingles`` were still the native ones (``enabled = 0``,
``plaid_env = sandbox`` ...). Two consequences: erpnext's code reads
``settings.plaid_env`` / ``enable_european_access`` off a schema that no longer declares
them, so switching the native ``enabled`` on would break Link and the hourly sync; and
our code read ``plaid_enabled`` off a row that never had it, so the widget reported
"disabled" whatever anyone did. **Bumping either JSON's ``modified`` changes nothing**
(the hash decides, and app order breaks the tie). The fix is two different names, which
this patch makes true on an existing site (a fresh install never collides: the new JSON
under ``plaid_banking_settings/`` has its own name from the start).

What runs, in order, and why each step:

1. ``frappe.rename_doc("DocType", "Plaid Settings", "Plaid Banking Settings",
   force=True)``. ``DocType.after_rename`` (frappe/core/doctype/doctype/doctype.py,
   version-16) moves **every** ``tabSingles`` row under the old name to the new one,
   ours and native alike, and rewrites the ``name`` row's value; it does not rename
   files while ``frappe.flags.in_patch`` is set, and it does not touch ``__Auth``
   (``rename_password`` there only renames rows whose ``doctype`` is "DocType", i.e.
   passwords *on* the DocType record -- none). The native Password row therefore stays
   keyed ``('Plaid Settings', 'Plaid Settings', 'plaid_secret')``, which is where it
   must be.
2. Put erpnext's own navigation back. ``rename_doc`` also runs ``rename_dynamic_links``
   (frappe/model/rename_doc.py), which rewrites every Dynamic Link row pointing at a
   DocType named "Plaid Settings" -- and erpnext ships two: the "Plaid Settings" card
   on the Invoicing workspace (``tabWorkspace Link``) and the Banking sidebar item
   (``tabWorkspace Sidebar Item``). Left alone they would open OUR widget switch under
   the native label, and ``sync_all`` never repairs them: a child-row UPDATE does not
   bump the parent's ``modified``, and a non-DocType record is skipped whenever the DB
   stamp is not older than the JSON's (prod's Invoicing equals the file, Banking is
   newer). This app ships no workspace, sidebar item, shortcut or desktop icon that
   references the old name (verified on prod: the only two rows are erpnext's), so
   every row now pointing at the new name is native and goes back.
3. ``UPDATE tabSingles SET doctype = 'Plaid Settings' WHERE doctype = 'Plaid Banking
   Settings' AND field NOT IN (<our fields>)`` -- everything that is not one of OUR
   fields returns to the native name: the six native values AND the Single's meta rows
   (``name``, ``modified``, ``owner``, ``creation`` ...), which are the native Single's
   save history and belong with it. Then the ``name`` row's value is reset to the native
   name (``after_rename`` rewrote it to the new one). On prod that leaves "Plaid Banking
   Settings" with **no** rows, so ``Document.load_from_db`` takes its ``new_doc()`` branch
   and the declared defaults apply on first load; the post_model_sync backfill stays as
   the belt-and-braces for a site where some of our rows did exist.
4. Belt-and-braces for ``__Auth``: move any native ``plaid_secret`` row that somehow
   sits under the new name back, and DELETE any row for our retired
   ``plaid_access_token`` under either name. Prod has no ``__Auth`` rows for either
   doctype today, but a bearer token that outlives its field must not linger.
5. ``frappe.reload_doc("erpnext_integrations", "doctype", "plaid_settings", force=True)``
   so the native DocType record exists again *immediately*, from erpnext's JSON
   (``get_module_path`` resolves the module through ``frappe.local.module_app``, which
   maps ``erpnext_integrations`` -> ``erpnext``). The subsequent ``sync_all`` then finds
   the hash it just wrote and skips, and syncs our ``plaid_banking_settings.json`` onto
   the renamed record, dropping the retired credential/token fields.

``pre_model_sync`` is the right and only place: the rename must precede model sync
(otherwise sync creates a *third* "Plaid Banking Settings" from our JSON and leaves
the collision in place), and a ``before_migrate`` hook would run it on every migrate
against a name that only exists once. Idempotent by inspection of the DB state: no
"Plaid Settings" -> nothing to do; "Plaid Settings" already owned by another module
-> already native, nothing to do; "Plaid Banking Settings" already present -> the
rename happened, nothing to do.
"""

import frappe

OLD = "Plaid Settings"
NEW = "Plaid Banking Settings"
OUR_MODULE = "Plaid Banking"
NATIVE_MODULE_SCRUBBED = "erpnext_integrations"

# Every field OUR old "Plaid Settings" JSON declared (git HEAD~ plaid_settings.json), kept
# and retired alike, EXCEPT ``plaid_client_id`` / ``plaid_secret``: both schemas declared
# those two names and the rows on disk are the native operator's keys, so they go back
# with the native set. Everything not listed here returns to the native name -- the six
# native values and the Single's meta rows (name, modified, owner, creation ...).
OUR_FIELDS = (
	"plaid_enabled",
	"plaid_environment",
	"company",
	"plaid_status",
	"plaid_status_message",
	"plaid_last_sync",
	"refresh_poll_minutes",
	"plaid_access_token",
	"plaid_item_id",
	"plaid_institution_name",
	"plaid_auth_blocked",
)
# The native Single's declared fields (erpnext/erpnext_integrations/doctype/plaid_settings/
# plaid_settings.json, version-16), for the record and for the test that pins the split.
NATIVE_FIELDS = (
	"enabled",
	"automatic_sync",
	"plaid_client_id",
	"plaid_secret",
	"plaid_env",
	"enable_european_access",
)
RETIRED_SECRET_FIELDS = ("plaid_access_token",)

# Desk navigation child tables whose ``link_to`` is a Dynamic Link on a DocType name,
# with the column that carries the link type (frappe/desk/doctype/*, version-16).
# ``rename_dynamic_links`` rewrites the first three; ``Desktop Icon.link_type`` has no
# "DocType" option in v16, so that row is listed for completeness and matches nothing.
# erpnext owns every row that matched. Each is guarded on table AND column existence.
NAVIGATION_TABLES = (
	("Workspace Link", "link_type"),
	("Workspace Sidebar Item", "link_type"),
	("Workspace Shortcut", "type"),
	("Desktop Icon", "link_type"),
)


def execute():
	if not frappe.db.exists("DocType", OLD):
		return
	if frappe.db.get_value("DocType", OLD, "module") != OUR_MODULE:
		return  # already the native one; nothing collided (or this already ran)
	if frappe.db.exists("DocType", NEW):
		frappe.logger().info(f"{NEW} already exists alongside {OLD}; leaving both for model sync")
		return

	frappe.rename_doc("DocType", OLD, NEW, force=True)

	restore_native_navigation()

	frappe.db.sql(
		"""update `tabSingles` set doctype = %s where doctype = %s and field not in %s""",
		(OLD, NEW, OUR_FIELDS),
	)
	frappe.db.sql(
		"""update `tabSingles` set value = %s where doctype = %s and field = 'name'""",
		(OLD, OLD),
	)
	frappe.db.sql(
		"""update `__Auth` set doctype = %s, name = %s
		where doctype = %s and name = %s and fieldname = 'plaid_secret'""",
		(OLD, OLD, NEW, NEW),
	)
	frappe.db.sql(
		"""delete from `__Auth` where doctype in %s and fieldname in %s""",
		((OLD, NEW), RETIRED_SECRET_FIELDS),
	)

	if "erpnext" in frappe.get_installed_apps():
		frappe.reload_doc(NATIVE_MODULE_SCRUBBED, "doctype", "plaid_settings", force=True)

	frappe.clear_cache()


def restore_native_navigation():
	"""Point erpnext's Workspace / sidebar / shortcut / icon rows back at the native name.

	``rename_dynamic_links`` just moved every ``link_to = 'Plaid Settings'`` row (where the
	type column says DocType) to the new name. All of them are erpnext's -- this app ships
	none -- so the inverse UPDATE is exact. Guarded per table because the set of desk
	navigation doctypes differs between framework versions.
	"""
	for doctype, type_column in NAVIGATION_TABLES:
		if not frappe.db.table_exists(doctype) or not frappe.db.has_column(doctype, type_column):
			continue
		frappe.db.sql(
			f"""update `tab{doctype}` set link_to = %s where `{type_column}` = 'DocType' and link_to = %s""",
			(OLD, NEW),
		)
