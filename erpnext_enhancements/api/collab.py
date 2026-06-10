"""Live collaborative form sync relay.

Clients on a collab-enabled form (see ``public/js/collab/live_form_sync.js``)
POST debounced field changes (and field-focus presence events) here; after a
write-permission check each event is re-published to the document's realtime
room (``doc:{doctype}/{docname}``), whose membership is itself
permission-checked by Frappe's socket.io ``can_subscribe_doc``. Clients never
emit realtime events to each other directly — this endpoint is the security
authority for every broadcast.

Endpoints:
	- ``broadcast_field_update`` — a debounced field value change
	  (``collab_field_update`` event).
	- ``broadcast_focus`` — "user X is editing field Y" presence
	  (``collab_focus`` event), powering the per-field highlight UI.

The relay never writes to the database: broadcast values are ephemeral and
persistence only happens through normal document saves, so a forged or
oversized value can at worst annoy room members who already have read access
to the document.

Security model:
	- ``doctype`` must be in the settings-driven allowlist
	  (:func:`get_collab_doctypes` — master switch + child table on ERPNext
	  Enhancements Settings; the bootinfo copy only gates client attachment).
	- Caller must hold *write* permission on the specific document.
	- ``fieldname`` must exist on the target meta and hold a value (display
	  fieldtypes such as Section Break / HTML / Table do not). Field updates
	  reject invalid fields with a throw; focus events drop them *silently* —
	  presence is best-effort, and a throw surfaces as an error modal on the
	  sender (focus can land inside non-value wrappers, e.g. the Comments App
	  button in an HTML field, where a modal would break the click it rode in
	  on).
	- ``value`` is capped at ``MAX_VALUE_LENGTH`` characters.

No server-side throttle in v1: the client debounces 300ms per field (~3
requests/sec/field while typing), focus events fire only on focus moves plus
a 30s heartbeat, and each call is one permission check plus one Redis
publish. v2 hardening, should abuse appear: a ``frappe.cache()`` token bucket
keyed by ``(user, docname)``.
"""

import frappe
from frappe import _
from frappe.model import no_value_fields
from frappe.utils import cint, cstr, get_fullname

# The launch allowlist — top 10 by edit volume + multi-editor activity
# (tabVersion, 180 days, 2026-06). The *live* allowlist is settings-driven
# (see get_collab_doctypes); this tuple only seeds it via the
# seed_collab_doctypes patch on existing sites.
DEFAULT_COLLAB_DOCTYPES = (
	"Task",
	"Project",
	"Opportunity",
	"Customer",
	"Contact",
	"Address",
	"Item",
	"Supplier",
	"Purchase Order",
	"ToDo",
)

# Generous cap for Text Editor HTML; anything larger is rejected outright.
MAX_VALUE_LENGTH = 140_000


def get_collab_doctypes():
	"""The live collab allowlist, from ERPNext Enhancements Settings.

	Returns an empty set when the master switch (``collab_enabled``) is off.
	``frappe.get_cached_doc`` keeps this one cheap lookup per request and is
	invalidated automatically when the Single is saved — so toggling doctypes
	needs no deploy; collaborators pick the change up on their next page load
	(the client list ships in bootinfo via ``boot.boot_session``).
	"""
	try:
		settings = frappe.get_cached_doc("ERPNext Enhancements Settings")
	except Exception:
		# fresh DB mid-migrate: the Single/table may not exist yet
		return set()
	if not cint(settings.get("collab_enabled")):
		return set()
	return {row.document_type for row in settings.get("collab_doctypes") or [] if row.document_type}


def _check_access(doctype, docname):
	"""Allowlist + write-permission guard shared by both endpoints (throws)."""
	if doctype not in get_collab_doctypes():
		frappe.throw(_("Live sync is not enabled for {0}").format(doctype))

	if not frappe.has_permission(doctype, "write", doc=docname):
		frappe.throw(_("Not permitted"), frappe.PermissionError)


def _is_broadcastable_field(doctype, fieldname, child_doctype):
	"""Whether ``fieldname`` is a valid sync target.

	True when it exists on the parent meta — or on ``child_doctype``, which
	must be one of the parent's table options — and holds a value (display
	fieldtypes such as Section Break / HTML / Table do not).
	"""
	meta = frappe.get_meta(doctype)
	if child_doctype:
		if child_doctype not in {df.options for df in meta.get_table_fields()}:
			return False
		target_meta = frappe.get_meta(child_doctype)
	else:
		target_meta = meta

	df = target_meta.get_field(fieldname)
	return bool(df) and df.fieldtype not in no_value_fields


def _check_target(doctype, docname, fieldname, child_doctype):
	"""Strict guard for field updates: access checks plus field validity.

	Focus events use :func:`_check_access` + :func:`_is_broadcastable_field`
	directly so an invalid field can be dropped without a throw.
	"""
	_check_access(doctype, docname)
	if fieldname and not _is_broadcastable_field(doctype, fieldname, child_doctype):
		frappe.throw(_("Invalid field"))


@frappe.whitelist()
def broadcast_field_update(
	doctype,
	docname,
	fieldname,
	value=None,
	origin=None,
	child_doctype=None,
	child_name=None,
):
	"""Validate a field change and re-publish it to the document's realtime room.

	Args:
		doctype, docname: the parent document being collaboratively edited.
		fieldname: changed field on the parent (or on the child row).
		value: new value (scalar; ephemeral — never persisted here).
		origin: opaque client id so the sender can ignore its own echo.
		child_doctype, child_name: set when the change targets a child table
			row; ``child_doctype`` must be one of the parent's table options.
	"""
	if not fieldname:
		frappe.throw(_("Invalid field"))

	_check_target(doctype, docname, fieldname, child_doctype)

	if value is not None and len(cstr(value)) > MAX_VALUE_LENGTH:
		frappe.throw(_("Value too large for live sync"))

	frappe.publish_realtime(
		"collab_field_update",
		{
			"doctype": doctype,
			"docname": docname,
			"fieldname": fieldname,
			"value": value,
			"origin": origin,
			"child_doctype": child_doctype,
			"child_name": child_name,
			"user": frappe.session.user,
		},
		doctype=doctype,
		docname=docname,
	)


@frappe.whitelist()
def broadcast_focus(
	doctype,
	docname,
	fieldname=None,
	origin=None,
	child_doctype=None,
	child_name=None,
	focused=1,
):
	"""Re-publish a "user is editing this field" presence event to the doc room.

	Backs the per-field highlight UI: receivers outline the field and show the
	sender's name. Presence is ephemeral and best-effort — receivers expire a
	highlight on their own TTL if no heartbeat re-asserts it, so a missed blur
	(crashed tab, dropped connection) self-heals.

	Args:
		doctype, docname: the parent document being collaboratively edited.
		fieldname: focused field; may be omitted when ``focused`` is falsy
			(a plain "stopped editing" event).
		origin: opaque client id so the sender can ignore its own echo.
		child_doctype, child_name: set when the focus is a child table cell.
		focused: 1 on focus/heartbeat, 0 on blur.
	"""
	focused = cint(focused)
	_check_access(doctype, docname)

	if focused and not (fieldname and _is_broadcastable_field(doctype, fieldname, child_doctype)):
		# Drop quietly, never throw: focus can land inside non-value wrappers
		# (HTML fields hosting custom widgets — e.g. the Comments App's "New
		# Note" button — Buttons, grid Table wrappers), and a throw here pops
		# an "Invalid field" error modal on the sender that breaks whatever
		# the click was doing. Nothing is published, so nothing is at risk;
		# receivers TTL out any highlight this event would have refreshed.
		return

	frappe.publish_realtime(
		"collab_focus",
		{
			"doctype": doctype,
			"docname": docname,
			"fieldname": fieldname,
			"origin": origin,
			"child_doctype": child_doctype,
			"child_name": child_name,
			"focused": focused,
			"user": frappe.session.user,
			"user_fullname": get_fullname(frappe.session.user),
		},
		doctype=doctype,
		docname=docname,
	)
