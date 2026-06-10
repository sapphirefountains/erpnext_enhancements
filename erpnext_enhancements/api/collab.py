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
	- ``doctype`` must be in ``COLLAB_DOCTYPES`` (server-side allowlist; the
	  JS constant of the same name only gates client attachment).
	- Caller must hold *write* permission on the specific document.
	- ``fieldname`` must exist on the target meta and hold a value (display
	  fieldtypes such as Section Break / HTML / Table are rejected).
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

# v2: move this list to a child table on ERPNext Enhancements Settings (the
# Single already follows a child-table-per-feature pattern), read it here, and
# ship it to the client via extend_bootinfo. Keep in sync with COLLAB_DOCTYPES
# in public/js/collab/live_form_sync.js.
# Top 10 by edit volume + multi-editor activity (tabVersion, 180 days, 2026-06).
COLLAB_DOCTYPES = {
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
}

# Generous cap for Text Editor HTML; anything larger is rejected outright.
MAX_VALUE_LENGTH = 140_000


def _check_target(doctype, docname, fieldname, child_doctype):
	"""Shared guard for both broadcast endpoints.

	Enforces the doctype allowlist and write permission, and (when a
	``fieldname`` is given) validates that it exists on the parent meta — or on
	``child_doctype``, which must be one of the parent's table options — and
	holds a value (display fieldtypes are rejected).
	"""
	if doctype not in COLLAB_DOCTYPES:
		frappe.throw(_("Live sync is not enabled for {0}").format(doctype))

	if not frappe.has_permission(doctype, "write", doc=docname):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	if not fieldname:
		return

	meta = frappe.get_meta(doctype)
	if child_doctype:
		if child_doctype not in {df.options for df in meta.get_table_fields()}:
			frappe.throw(_("Invalid child table"))
		target_meta = frappe.get_meta(child_doctype)
	else:
		target_meta = meta

	df = target_meta.get_field(fieldname)
	if not df or df.fieldtype in no_value_fields:
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
	if focused and not fieldname:
		frappe.throw(_("Invalid field"))

	_check_target(doctype, docname, fieldname, child_doctype)

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
