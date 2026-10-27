# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Time Kiosk offline marker: whose kept kiosk a phone may show, and replay, with no signal.

The kiosk's root-scope worker (``www/kiosk-sw.js``) keeps the ``/kiosk`` page and the kiosk's GET
answers so the app works in a dead zone, and it queues location points until they upload. Phones
are shared. Offline, nothing can ask the server who is signed in, and frappe's own ``user_id``
cookie cannot say either: it is a *session* cookie, which a home-screen app started again has lost
whether or not anybody signed out (the itinerary found this first; see
``travel_management/itinerary_offline.py``, the pattern this module copies).

So the kiosk has a cookie of its own, ``ee_kiosk_key``:

* **Set** by ``www/kiosk.py`` every time ``/kiosk`` is drawn for a signed-in person
  (:func:`set_marker`), for 30 days from that visit. The same value goes into the boot as
  ``offline_key``.
* **Removed** by the ``on_logout`` hook (:func:`forget_on_logout`) on every sign-out through
  frappe, and by the ``on_login`` hook (:func:`forget_on_login`) whenever anybody else signs in on
  that browser. ``on_login`` runs for every way in: the password form (``POST /api/method/login``,
  or ``POST /`` with ``cmd=login``), and the ones that arrive as a GET, ``login_via_google`` and the
  other OAuth callbacks and ``login_via_key`` (``LoginManager.login_as`` -> ``post_login``). That is
  what makes a session that simply ran out safe: expiry runs no hook, so without it the next person
  to sign in, by any route, would inherit the last one's marker.
* **Read** by ``public/js/kiosk/app.js``. A page whose boot key is not the cookie's (a kept page
  drawn for somebody who has since signed out, or for whom somebody else has signed in) refuses to
  draw, says "Signed out", and has the worker delete everything it keeps. Every kiosk call names
  the key in ``X-Kiosk-Key``, and the worker keeps and replays answers under it.
* **Echoed** by the ``after_request`` hook (:func:`stamp_response`): every
  ``api.time_kiosk`` response carries ``X-Kiosk-Key`` for the session that actually answered, and
  the worker keeps an answer only when that matches the key the page claimed. A stale window still
  claiming Pat while the phone's session is Sam's gets Sam's answer, and nothing is kept.
* **Checked** by ``api.time_kiosk.log_geolocation_batch`` (:func:`belongs_to_session`): a batch of
  queued points tagged with somebody else's key is refused, so one person's unsent positions can
  never be filed as another's ``Time Kiosk Log`` rows.

What it is: ``HMAC-SHA256(site encryption key, context + user)``, 64 hex characters. The same for
one person on one site every time, so a phone keeps its kept answers across visits; nobody's email,
and not reversible without the site's key. Its context differs from the itinerary's, so the two
markers for one person are different values.

What it is not: a credential or a lock. The server decides nothing from it except to refuse (a
batch for somebody else) or to label (the echo); every answer still comes from the session, and a
stolen marker opens nothing. Any script on the site could send any ``X-Kiosk-Key``, and anyone with
the phone's developer tools can read Cache Storage whatever the cookie says. The real control is
deleting: a sign-out, or a sign-in as anybody else, removes the cookie, and the page and worker then
delete what was kept.

What removes it, and what does not (frappe v16, ``frappe/auth.py``), exactly as for the itinerary:

* ``LoginManager.logout`` runs ``on_logout`` for ``/api/method/logout`` (the desk's menu and the
  website's ``/logout`` page), ``web_logout`` and ``/api/v2/method/logout``.
* An **expired** session runs no hook, so the marker stays, and offline the phone still opens that
  person's kept kiosk until somebody signs in on it (``on_login``) or ``/kiosk`` loads with a signal
  and is sent to the login page (the worker forgets everything then).
* Sessions deleted from elsewhere (a password change, *Logout All Sessions*, a disabled user) send
  the phone nothing; it keeps its marker until someone signs in on it.

``Path=/`` (frappe v16's ``CookieManager`` has no path argument, and the logout and login requests
that must see it are not under ``/kiosk``), not HttpOnly (the page reads it with no server to
ask), ``SameSite=Lax`` (so it rides the top-level GET back from Google's sign-in), ``Secure`` on
https.

Every hook here runs on every sign-in, sign-out or kiosk request, so none may raise.
"""

import hashlib
import hmac

import frappe

#: The cookie's name. app.js reads it by this name.
COOKIE = "ee_kiosk_key"

#: The header the page claims with and the server echoes in. kiosk-sw.js reads it by this name.
HEADER = "X-Kiosk-Key"

#: How long a phone keeps it after its last visit to /kiosk with a signal (seconds).
MAX_AGE = 30 * 24 * 60 * 60

#: The kiosk's API, whose answers the worker keeps: only these responses are stamped.
API_PREFIX = "/api/method/erpnext_enhancements.api.time_kiosk."

# Keeps this HMAC apart from anything else the site's encryption key is used for, the itinerary's
# marker included.
_CONTEXT = b"erpnext_enhancements:kiosk-offline-marker:v1\x00"


def _secret():
	"""The site's encryption key (``site_config.json``); frappe creates it on first use."""
	from frappe.utils.password import get_encryption_key

	return get_encryption_key()


def key_for(user):
	"""The marker for ``user``: 64 hex characters, or ``""`` for Guest and nobody."""
	if not user or user == "Guest":
		return ""
	return hmac.new(_secret().encode(), _CONTEXT + str(user).encode(), hashlib.sha256).hexdigest()


def _secure():
	"""True when the site is served over https (the request says so, or its own address does)."""
	request = getattr(frappe.local, "request", None)
	if getattr(request, "scheme", None) == "https":
		return True
	try:
		return str(frappe.utils.get_url() or "").startswith("https://")
	except Exception:
		return False


def set_marker(user):
	"""Mark this browser as ``user``'s for the kiosk offline, and return the key for the boot.

	Called by ``www/kiosk.py`` for a signed-in person only. ``""`` (and no cookie) for Guest,
	outside a request, or when the key cannot be made: the page then keeps and replays nothing,
	and works online exactly as before. Never raises.
	"""
	try:
		key = key_for(user)
		cookies = getattr(frappe.local, "cookie_manager", None)
		if not key or cookies is None:
			return ""
		cookies.set_cookie(COOKIE, key, max_age=MAX_AGE, secure=_secure(), httponly=False, samesite="Lax")
		return key
	except Exception:
		return ""


def _held():
	"""The marker this request came with, or ``""``."""
	request = getattr(frappe.local, "request", None)
	cookies = getattr(request, "cookies", None)
	try:
		return (cookies.get(COOKIE) if cookies is not None else "") or ""
	except Exception:
		return ""


def _forget():
	cookies = getattr(frappe.local, "cookie_manager", None)
	if cookies is not None:
		cookies.delete_cookie(COOKIE)


def forget_on_logout(login_manager=None):
	"""``on_logout`` hook: the marker goes with the session it was set in. Never raises."""
	try:
		_forget()
	except Exception:
		pass


def forget_on_login(login_manager=None):
	"""``on_login`` hook: a marker left by anybody else goes when someone signs in. Never raises.

	Only when the request carries one that is not the signing-in person's own, so an ordinary
	sign-in sends no extra cookie. Guest (``login_as_guest``, at the end of a sign-out) has no key,
	so that removes it too, and so does a key that cannot be made.
	"""
	try:
		held = _held()
		if not held:
			return
		try:
			own = key_for(getattr(login_manager, "user", None))
		except Exception:
			own = ""
		if held != own:
			_forget()
	except Exception:
		pass


def belongs_to_session(offline_key):
	"""True unless ``offline_key`` names somebody other than the session's user.

	For ``log_geolocation_batch``: a queued batch tagged with another person's key is refused. An
	empty key (a point queued before v1.753.2, or a page whose marker could not be made) is not
	refused, as before. A key the server cannot check is refused: nobody's points are filed under
	a session that cannot be shown to be theirs.
	"""
	if not offline_key:
		return True
	try:
		return hmac.compare_digest(str(offline_key), key_for(frappe.session.user))
	except Exception:
		return False


def stamp_response(response=None, request=None):
	"""``after_request`` hook: label each kiosk API GET with the key of the session that answered.

	``X-Kiosk-Key`` is the marker of ``frappe.session.user`` (empty for Guest). kiosk-sw.js keeps
	an answer only when it matches the key the page claimed, so the cache key is who the server
	answered for, not merely who the page said it was. Every other request leaves on the first
	check. Frappe logs and swallows an exception from an ``after_request`` hook; this one never
	raises anyway.
	"""
	try:
		if request is None or response is None or request.method != "GET":
			return
		if not str(request.path or "").startswith(API_PREFIX):
			return
		session = getattr(frappe.local, "session", None)
		response.headers[HEADER] = key_for(getattr(session, "user", None))
	except Exception:
		pass
