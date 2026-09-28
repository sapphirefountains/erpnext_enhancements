# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The ``/itinerary`` offline marker: whose saved itinerary a phone may show with no signal.

``/itinerary`` keeps each person's itinerary and files on their phone (``itinerary.js`` in
IndexedDB, ``www/itinerary-sw.js`` in Cache Storage; ``www/README.md``, "Offline: the itinerary on
the phone"). Offline the page cannot ask the server who is signed in. The cookie it had to go on,
frappe's ``user_id``, is a *session* cookie: a home-screen app that is started again has dropped
it whether or not anybody signed out, so "no cookie" had to count as "still signed in", and after
a sign-out the next person to open the app with no signal could see the saved itinerary.

So the page has a cookie of its own, ``ee_itinerary_key``, that outlives the app and that a
sign-out removes:

* **Set** by ``www/itinerary.py`` every time ``/itinerary`` is drawn for a signed-in person
  (:func:`set_marker`), for 30 days from that visit. The same value goes into the page's boot as
  ``offline_key``, and ``itinerary.js`` stores it with every answer it saves and names the
  worker's files cache after it.
* **Removed** by the ``on_logout`` hook (:func:`forget_on_logout`) on every sign-out, and by the
  ``on_login`` hook (:func:`forget_on_login`) when anybody else signs in on that browser. The
  second is for a session that simply ran out: expiry runs no logout, so without it the next
  person to sign in on the phone would inherit the last one's marker.
* **Read** by ``itinerary.js``. Offline, a saved copy is shown only while the marker is present
  and is the key it was saved with. Missing or different, the page says "Sign in to see your
  itinerary." and deletes everything saved on the phone.

What it is: ``HMAC-SHA256(site encryption key, context + user)``, 64 hex characters. The same for
one person on one site every time, so a phone keeps its copies across visits and deploys. It is
nobody's email, and it cannot be turned back into one without the site's key.

What it is not: a credential, or a lock. The server never reads it to decide anything: every
answer still comes from the session, and a stolen marker opens nothing. And anyone who can open
the browser's developer tools on the phone can read IndexedDB whatever the cookie says. It is the
rule the page follows, so the next person to pick up a shared phone is not shown the last
person's trip.

What removes it, and what does not (frappe v16, ``frappe/auth.py``):

* ``LoginManager.logout`` runs ``on_logout`` for every sign-out through frappe:
  ``/api/method/logout`` (which the desk's menu and the website's ``/logout`` page both call),
  ``web_logout`` and ``/api/v2/method/logout``. The cookie goes with that response, to the browser
  that signed out.
* A session that **expires** runs no hook at all (``Session.resume`` finds no row and carries on
  as Guest), so the marker stays. That is deliberate: a phone whose session ran out is still the
  person's phone, and their itinerary is still wanted at the gate. The next sign-in on it, as
  anybody else, removes it (``on_login``).
* Sessions deleted from elsewhere (a password change's "log out of all sessions", *Logout All
  Sessions*, a user disabled or deleted, ``deny_multiple_sessions``) end without a request from
  the phone, and a cookie can only be removed in a response to the browser that holds it. The
  phone keeps its marker until someone signs in on it. A user disabled or deleted from the desk
  runs ``on_logout`` inside the *administrator's* request, so it is the administrator's own marker
  that goes: their saved copy on that browser does not open offline until they next open
  ``/itinerary`` with a signal.
* Signing out with no signal, or clearing cookies by hand, are the browser's business.

``Path=/`` rather than ``/itinerary``, for two reasons: frappe v16's ``CookieManager`` has no path
argument (``set_cookie`` and ``delete_cookie`` both write at werkzeug's default, ``/``), so a
cookie set at ``/itinerary`` could never be deleted through it; and the requests that have to see
it are ``/api/method/logout`` and the login, which a ``/itinerary`` path would not reach. Not
HttpOnly: the page must read it with no server to ask. ``SameSite=Lax``, and ``Secure`` whenever
the site is served over https.

Both hooks run on every sign-in and sign-out of every user, so neither may raise: frappe calls
them inside ``LoginManager``, where an exception would fail the login or the logout itself.
"""

import hashlib
import hmac

import frappe

#: The cookie's name. itinerary.js reads it by this name.
COOKIE = "ee_itinerary_key"

#: How long a phone keeps it after its last visit to /itinerary with a signal (seconds).
MAX_AGE = 30 * 24 * 60 * 60

# Keeps this HMAC apart from anything else the site's encryption key is used for.
_CONTEXT = b"erpnext_enhancements:itinerary-offline-marker:v1\x00"


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
	"""Mark this browser as ``user``'s for ``/itinerary`` offline, and return the key for the boot.

	Called by ``www/itinerary.py`` for a signed-in person only. ``""`` (and no cookie) for Guest,
	outside a request, or when the key cannot be made: the page then saves nothing and shows
	nothing saved, and works online exactly as before. Never raises.
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
	sign-in sends no extra cookie. frappe runs ``on_login`` for a real sign-in, ``login_as`` and
	impersonation, and for the Guest session a sign-out starts (``login_as_guest``); Guest has no
	key, so that one removes it too. A key that cannot be made is nobody's: the marker goes.
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
