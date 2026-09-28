"""Frappe web-page controller for the traveler itinerary at ``/itinerary``.

Mobile-friendly, chrome-free page where a traveler sees their day-by-day trip
itinerary (flights with PNRs, hotel confirmations, agenda stops with POI
locations and maps). Follows the Time Kiosk shell pattern (``www/kiosk.py``).
Live data comes from ``erpnext_enhancements.api.travel`` (session-trust
security model: the employee is derived server-side, trips are scoped by the
Travel Trip permission hooks).

Offline (since the Plan a Trip program's PR 4): the page is a home-screen app
(``itinerary-manifest.json``) with its own service worker, ``/itinerary-sw.js``,
registered by ``itinerary.js`` with scope ``/itinerary`` (never the site root,
which belongs to the kiosk's worker). The worker keeps this page, its stylesheet and
script, and the person's pictures and PDFs; ``itinerary.js`` keeps every answer in
IndexedDB, and shows the saved copy when the server cannot be reached at all. The rendered
page (boot included) may therefore be served from the phone later, to whoever holds it.

One part of that runs here: the **offline marker**. Every render for a signed-in person
sets the ``ee_itinerary_key`` cookie (30 days, ``Path=/``, readable by the page) and puts
the same value in the boot as ``offline_key``
(``travel_management.itinerary_offline.set_marker``); every sign-out deletes it (the
``on_logout`` hook), and so does a sign-in as anybody else (``on_login``). ``itinerary.js``
saves only under that key, and offline shows a saved copy only while the cookie still holds
it; missing or different, it says "Sign in to see your itinerary." and deletes everything
saved. It also still checks the boot's ``user`` against the session's ``user_id`` cookie.
The marker is not a credential: the server never reads it to answer anything. The worker
and the manifest have no controller, and must not have one:
``scripts/check_www_controllers.py`` fails on a ``.py`` with no page template beside it.

Addresses: ``/itinerary?trip=<name>&as=<employee|crew>&view=docs&file=<document>``, and
``/itinerary?view=trips`` for the list of every trip (v1.556.0).
``trip`` is the trip on screen; ``as`` is whose view of it (one person's bookings and
their own confirmation numbers, or ``crew`` for the whole crew), and without it the
page shows the viewer's own view on a trip they travel on, else the whole crew.
``view=docs`` is the Documents screen (every file that person can see: the whole
trip's, then each booking's), and ``file`` a Trip Document open in the picture viewer
over whichever screen it was opened from. All four are read by ``itinerary.js``, not
here, and none is trusted: the page sends ``trip`` and ``as`` to
``get_trip_itinerary``, which gives the full answer to anyone with read permission on the
trip (crew, the trip's owner and travel coordinators), a *limited* one to any other staff
member (the Employee role: no confirmation numbers, files or sheet links, since v1.556.0)
and refuses everyone else, and it refuses a person who is not on the crew, and it opens
only a ``file`` that answer lists for that person. So a link naming a trip outside
the boot list, a person, a screen, the list of all trips (``get_all_trips``, staff only) or a file needs
nothing from this controller
beyond keeping the query string through the login redirect.

The same answer carries the trip's ``contacts`` (911, the office travel desk, who booked
the trip, the trip lead's work mobile, the job site's contact, and the person shown's
hotels with urgent care nearby, from ``api.travel._trip_contacts``) and the trip sheet's
addresses (``my_sheet_url`` for the person shown, ``sheet_url`` for the whole trip), which
``itinerary.js`` draws as the Contacts card and "Print / save as PDF". Neither is in the
address or the boot, so this controller has no part in them either.

Cache busting: raw ``/assets`` URLs are served 1-year-immutable, so
``itinerary.html`` appends ``?v={{ deploy_version }}`` to every mutable asset
URL (same rationale and token as the kiosk — see
:func:`erpnext_enhancements.www.kiosk.get_deploy_version`).
"""

from urllib.parse import quote

import frappe

from erpnext_enhancements.api.travel import get_itinerary_bootstrap
from erpnext_enhancements.travel_management.itinerary_offline import set_marker
from erpnext_enhancements.www.kiosk import get_deploy_version

# Always render fresh per-user; never cache the authenticated shell on the server. (The copy
# kept on the phone is the service worker's, one person's, and checked on use: see above.)
# It is also what lets the marker cookie out: frappe flushes cookies only onto a response
# whose Cache-Control is not public (app.process_response).
no_cache = 1

ROUTE = "/itinerary"


def get_context(context):
	"""Route: ``/itinerary`` (rendered by ``itinerary.html``).

	Guests are redirected to ``/login?redirect-to=/itinerary``, with the query
	string kept (``?trip=``, ``&as=``, ``&view=`` and ``&file=``: the trip, person,
	screen and picture the page was showing — see itinerary.js). For an authenticated
	user this exposes ``boot_json`` (employee +
	their active trips, including ones they own but are not on, + CSRF token, + the
	offline marker's ``offline_key``, injected as ``window.ITIN_BOOT``), ``csrf_token``
	(``window.ITIN_CSRF``) and ``deploy_version`` (asset cache-bust token), and sets the
	marker cookie itself (``set_marker``, above). A guest gets neither.
	"""
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = login_redirect(
			frappe.request.full_path if getattr(frappe, "request", None) else ROUTE
		)
		raise frappe.Redirect

	boot = get_itinerary_bootstrap()
	# The offline marker: this browser is this person's for the copy kept on the phone. ""
	# (and no cookie) when it cannot be made, and then the page keeps nothing offline.
	boot["offline_key"] = set_marker(frappe.session.user)

	context.no_cache = 1
	context.boot_json = script_json(boot)
	context.csrf_token = boot.get("csrf_token") or ""
	context.deploy_version = get_deploy_version()
	return context


def script_json(value):
	"""``frappe.as_json`` made safe to print inside an inline ``<script>``.

	``itinerary.html`` prints the boot with ``| safe``, and ``as_json`` leaves ``<`` alone, so
	a trip purpose containing ``</script>`` would end the block early. The boot now carries
	every trip the person owns as well as the ones they are on, so it is escaped here: ``<``,
	``>`` and ``&`` become ``\\u`` escapes, which read back as the same characters. Outside a
	string JSON has none of the three, so nothing else changes.
	"""
	return frappe.as_json(value).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def login_redirect(full_path):
	"""``/login?redirect-to=<path>``, encoded so ``?trip=`` comes back after login.

	Encoded whole (everything but ``/``), as ``stock_scan_rules.login_redirect`` does and
	for its reason: the login page splits its own query on ``&``, so a raw ``?`` or ``&``
	in the target would be cut. A bare visit reads exactly as it always has.
	"""
	path = (full_path or ROUTE).rstrip("?")
	if not path.startswith(ROUTE):
		path = ROUTE
	return "/login?redirect-to=" + quote(path, safe="/")
