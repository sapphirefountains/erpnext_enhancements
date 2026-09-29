# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The knowledge base mirror's account may call its snapshot and nothing else (WI-080 PR 8 review).

The private mirror signs in with an API key, as a **Website User** holding only KB Mirror. A role with no
DocPerm keeps it out of ``/api/resource``, lists and reports, but not out of whitelisted methods: in
frappe v16 the whitelist refuses only a Guest, or a function that is not whitelisted
(``is_whitelisted``, ``frappe/__init__.py:479-487``, v16.35.0), and a key's user is signed in. So every
login-only endpoint whose own body checks nothing would answer to that key. There were such endpoints in
this app when this guard was written: ``sync_contact.get_contacts_for_context`` returned any party's
contacts with their phone numbers and email addresses, ``get_addresses_for_context`` their addresses, and
``link_existing_record`` and ``unlink_record`` re-linked or unlinked any Contact or Address with
``ignore_permissions``. v1.561.1 gave each of them a permission check of its own. The guard stays: a
leaked mirror key would reach whatever such endpoint is added next.

:func:`confine_mirror_account` closes that in one place rather than endpoint by endpoint. It is an
``auth_hooks`` entry, and it has to be:

* **Not ``before_request``.** v16's ``application`` runs ``init_request`` first, and ``init_request``
  ends by running every ``before_request`` hook (``app.py:139``, ``:244-245``); the API key is read only
  afterwards, in ``validate_auth`` (``app.py:141``; ``auth.py:629-652``, ``:695-747``). At
  ``before_request`` a keyed request is still Guest, so a guard there would confine nothing.
* **``auth_hooks`` run inside ``validate_auth``, after the API key and OAuth checks have set the user**
  (``auth.py:640``, ``:750-752``), and before Frappe dispatches anything: ``cmd``, ``/api``, a private
  file, a web page (``app.py:143-179``). They run on every request, whichever way its user signed in.

**Who is confined:** a user holding KB Mirror who is not a System User. v16 gives every System User, and
nobody else, the automatic role "Desk User" (``permissions.py:35``, ``get_roles`` ``:560-562``), so the
check is two membership tests on the user's roles, which Frappe caches per user. The mirror's account stays
confined even if a website role (Customer, say) is later added to it. A staff login given KB Mirror by
mistake is not confined, so it is never locked out of the Desk; it gains only the snapshot, and it
already reads the published knowledge base there. Administrator, Guest and a request with no user are
never confined, and a guest's request costs no lookup at all.

**What it may ask for:** exactly ``GET`` of :data:`SNAPSHOT_PATH`, the address the private repo's script
calls, with no ``cmd`` anywhere in the request. Frappe dispatches ``cmd`` before it looks at the path
(``app.py:146-155``), so a request that carries one is refused whatever its path, and so is every other
path: another method, the same method under ``/api/v1`` or ``/api/v2`` or with a trailing slash,
``/api/resource``, ``/api/v2/document``, ``/private/files``, a web page, and the realtime server's
sign-in (``frappe.realtime.get_user_info``). The refusal is ``PermissionError``, a 403 that Frappe does
not log (``handle_exception`` logs only a status of 500 or more, outside developer mode).

**How it fails:** the roles lookup is frappe's own, and if it raises the request fails as it would at
its first permission check. For a confined account, a request whose method, path or form cannot be read
is refused. It reads the request's method and path and whether its form has a ``cmd``, never a header
or a value, and it writes and logs nothing.

``tests/test_knowledge_base_actions.py`` (``MirrorConfinementTest``) runs it over the in-memory site,
and ``tests/test_hooks_integrity.py`` pins it to ``auth_hooks`` and out of ``before_request``.

Indentation is tabs, the ``.editorconfig`` default for a new file.
"""

import frappe
from frappe import _

from erpnext_enhancements.knowledge_base import constants

#: The snapshot's dotted path. ``tests/test_knowledge_base_actions.py`` asserts it is the endpoint's own.
SNAPSHOT_METHOD = "erpnext_enhancements.api.knowledge_base_mirror.snapshot"
#: The one address the account may ask for, exactly as the private repo's script calls it.
SNAPSHOT_PATH = "/api/method/" + SNAPSHOT_METHOD
#: v16's automatic role of every System User, and of nobody else (``frappe/permissions.py:35``).
SYSTEM_USER_ROLE = "Desk User"
#: Never confined: Frappe's own two accounts, and a request no one is signed in to.
NEVER_CONFINED = frozenset({"", "Guest", "Administrator"})


def confine_mirror_account():
	"""``auth_hooks``: refuse every request by the mirror's account except its snapshot, before Frappe
	dispatches it. Every other user's request passes untouched."""
	if not is_mirror_account(frappe.session.user):
		return
	if asks_for_snapshot():
		return
	frappe.throw(_("The knowledge base mirror's account may only read its snapshot."), frappe.PermissionError)


def is_mirror_account(user):
	"""Whether ``user`` is confined: holds :data:`constants.MIRROR_ROLE` and is not a System User."""
	if not user or user in NEVER_CONFINED:
		return False
	roles = frappe.get_roles(user)
	return constants.MIRROR_ROLE in roles and SYSTEM_USER_ROLE not in roles


def asks_for_snapshot():
	"""Whether this request can dispatch the snapshot and nothing else. Any doubt is a no."""
	try:
		form = frappe.local.form_dict
		if not isinstance(form, dict) or "cmd" in form:
			return False
		request = frappe.local.request
		return request.method == "GET" and request.path == SNAPSHOT_PATH
	except Exception:
		return False
