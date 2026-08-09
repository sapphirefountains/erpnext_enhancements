# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Chat deep links — one builder, three consumers, no divergent copies.

A deep link into a chat message is built in three unrelated places across the phases:

* the SPA router (Phase 3), which parses the same shape it emits;
* the notification deep-link builder (Phase 4) — ``Notification Log.link``, the Web Push
  payload, and the truncation suffix an oversized relayed message carries into Google
  Chat (ADR §G.10, §H.2.3, §H.5.1);
* the citation resolver for ``chat_message`` sources (Phase 5).

Three implementations of one URL shape is how a notification lands on an error page while
the SPA's own links work fine. Writing it once in Phase 1 is cheap; reconciling three
copies in Phase 5 is not.

**The route shape is** ``/chat/room/<room>?message=<msg>`` (ADR §F.17, §H.2.3, §H.5.1).
Frappe v16 resolves website routes through werkzeug's ``Rule`` verbatim, so the full
converter set including ``<path:name>`` is available and one rule —
``{"from_route": "/chat/<path:chat_path>", "to_route": "chat"}`` — makes a hard refresh at
that URL work (register B1, CLOSED). Phase 3 owns that rule and the ``website_404`` cache
trap that comes with shipping it late; Phase 1 only agrees on the shape.

**Purity.** :func:`build_chat_route` and :func:`encode_room_segment` touch no Frappe
runtime at all, so the interesting half of this module is exercisable in the bench-free
CI tier. Only :func:`build_message_deep_link` needs a site, and it imports Frappe inside
the function body for that reason — the same idiom, and the same justification, as
``kpi_dashboards/permissions.py``.

Indentation is tabs, per ``CLAUDE.md`` and the Frappe convention this package follows.
"""

from __future__ import annotations

from urllib.parse import quote, urlencode

#: The SPA's room route, minus the origin. Phase 3's ``website_route_rules`` entry must
#: keep matching this; it is a prefix of it (``/chat/<path:chat_path>``).
CHAT_ROUTE_PREFIX = "/chat/room"


def encode_room_segment(room: str) -> str:
	"""Percent-encode a room name for use as a **path segment**.

	``safe=""`` — no character survives unencoded, ``/`` emphatically included. Room names
	are ``hash`` autonames (32 hex characters, ADR §F.4), so in practice this encodes
	nothing at all. It is here for the case that is not practice: a room name that ever
	arrives from anywhere other than the autonamer, at which point an unencoded ``/``
	or ``?`` silently retargets the link.
	"""
	room = (room or "").strip()
	if not room:
		raise ValueError("build a chat deep link with a room name, not an empty string")
	return quote(room, safe="")


def build_chat_route(room: str, message: str | None = None, thread: str | None = None) -> str:
	"""The origin-relative deep link: ``/chat/room/<room>?message=<msg>&thread=<thread>``.

	Pure — no Frappe, no site, no I/O. This is the half that can be unit-tested in CI.

	Args:
		room: the ``Chat Room`` name. Required; an empty room has no route.
		message: a ``Chat Message`` name to scroll to and highlight. Optional — a room
			link without one is a perfectly good link.
		thread: a ``thread_root`` message name, to open the thread panel.

	Returns:
		A path with a query string, every component percent-encoded.

	Parameter order is fixed at ``message`` then ``thread``, and empty values are dropped
	rather than emitted as ``?message=``. Both matter more than they look: these URLs are
	compared for equality by Phase 4's notification dedupe and stored in
	``Notification Log.link``, so two calls describing the same target must produce the
	same bytes.
	"""
	path = f"{CHAT_ROUTE_PREFIX}/{encode_room_segment(room)}"

	params: list[tuple[str, str]] = []
	if (message or "").strip():
		params.append(("message", message.strip()))
	if (thread or "").strip():
		params.append(("thread", thread.strip()))
	if not params:
		return path

	# quote_via=quote rather than the default quote_plus: this is a URL, not a form post,
	# and a "+" in a name must survive as "+" rather than becoming a space on the way back.
	return f"{path}?{urlencode(params, quote_via=quote)}"


def build_message_deep_link(
	room: str, message: str | None = None, thread: str | None = None
) -> str:
	"""The absolute deep link, origin included. The function every consumer calls.

	The origin comes from ``frappe.utils.get_url()`` — Frappe's own site-URL helper —
	rather than from a ``Chat Settings`` field. A settings field is a second copy of a
	fact the framework already knows, and the copy is wrong on exactly the day somebody
	changes the site's host.

	``VERIFY:`` (unexecuted — this environment has no bench) that on the production host,
	behind the Google Cloud load balancer, ``get_url()`` returns the external HTTPS origin
	and not an internal address. Run on the bench::

	    bench --site <site> execute frappe.utils.get_url

	The classic failure this guards against is ``http://localhost`` links baked into
	production push notifications, where nobody notices until a phone taps one. If it does
	return an internal address, the fix is ``host_name`` in ``site_config.json``, not a new
	setting here.
	"""
	# Imported inside the function, not at module scope, so that build_chat_route stays
	# importable — and therefore testable — without a Frappe runtime.
	from frappe.utils import get_url

	# Passing a path *with* a query string through get_url is existing practice in this
	# repo (``status_alerts.py:225``, ``process_steps.py:898``), so this is not a new
	# assumption about how the helper joins.
	return get_url(build_chat_route(room, message=message, thread=thread))
