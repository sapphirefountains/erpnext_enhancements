"""Is ``frappe.local.request_ip`` a real client, or the Google load balancer?

The chain in front of this site is **GCLB -> nginx -> bench**. frappe sets
``request_ip`` from the first ``X-Forwarded-For`` entry it receives
(``auth.py:set_request_ip``), and bench's nginx template writes that header as
``$remote_addr``. So the address frappe records is whatever nginx believes the
peer is -- and without the realip module, nginx believes it is a Google Front End.

That was the state from 2026-07-18 to 2026-08-03. Every login was recorded from a
rotating ``35.191.x`` address, every IP-keyed rate limit collapsed into one bucket
shared by all callers, and the 2026-08-02 intrusion's logins carried no client
address at all. ``/etc/nginx/conf.d/00-realip.conf`` fixed it on 2026-08-03 UTC (all
62 logins in proxy ranges before, 0 of 143 after), and a forged
``X-Forwarded-For`` sent on 2026-09-22 never reached the rate limiter's key.

**The fix lives on the VM, which is why this module exists.** It is a hand-placed
nginx file: a rebuilt VM, a tidy-up of ``conf.d``, or a new load-balancer address
undoes it silently, and nothing in the app would notice -- a rate limit keyed on
``35.191.x`` still counts, it just counts everybody. The file's source of truth is
``infra/configs/nginx-realip.conf`` (installed on every boot by
``infra/configs/startup_script.sh``); this module holds the same address list,
``tests/test_client_ip.py`` fails the build if the two disagree, and
:func:`check_client_ip_derivation` runs daily and writes an Error Log row the day
logins start arriving from the load balancer again.

The classifier is standard library only, so it is testable with no bench and no
stub. The two functions that read the database import frappe lazily.
"""

import ipaddress

#: Google Front End source ranges. The load balancer's proxies connect to nginx
#: from these, and so do its health checks. Google-wide and documented; not ours.
GCLB_PROXY_NETWORKS = ("35.191.0.0/16", "130.211.0.0/22")

#: The load balancers' own forwarding-rule addresses. GCLB appends its own address
#: after the client's, so an untrusted one here stops nginx's right-to-left walk on
#: the load balancer and records IT as every visitor. prod first, then beta.
#: Must match ``set_real_ip_from`` in infra/configs/nginx-realip.conf exactly.
LB_FRONTEND_ADDRESSES = ("136.68.113.208", "34.149.67.36")

#: Every category :func:`classify` can return, in report order.
CATEGORIES = ("public", "private", "loopback", "gclb_proxy", "load_balancer", "missing", "invalid")

#: Categories that mean "this is not a client -- the derivation is broken".
PROXY_CATEGORIES = ("gclb_proxy", "load_balancer")

#: How far back the daily check looks. Long enough to span a weekend with no
#: logins, short enough that a regression is reported within a working day.
LOOKBACK_DAYS = 3

ALERT_TITLE = "Client IP derivation regressed"

_PROXY_NETWORKS = tuple(ipaddress.ip_network(n) for n in GCLB_PROXY_NETWORKS)
_LB_ADDRESSES = frozenset(ipaddress.ip_address(a) for a in LB_FRONTEND_ADDRESSES)


def classify(value):
	"""One of :data:`CATEGORIES` for a single recorded address.

	The load-balancer check runs before the private/loopback checks on purpose: it
	is the specific failure this module exists to catch, and it must not be
	shadowed by a broader class.
	"""
	text = value.strip() if isinstance(value, str) else ""
	if not text:
		return "missing"
	try:
		addr = ipaddress.ip_address(text)
	except ValueError:
		# Includes "a, b" -- a whole header where one address belongs.
		return "invalid"
	if addr.version == 6 and addr.ipv4_mapped:
		addr = addr.ipv4_mapped
	if addr in _LB_ADDRESSES:
		return "load_balancer"
	if any(addr in network for network in _PROXY_NETWORKS):
		return "gclb_proxy"
	if addr.is_loopback:
		return "loopback"
	if addr.is_private or addr.is_link_local:
		return "private"
	return "public"


def summarize(addresses):
	"""Counts per category for an iterable of recorded addresses."""
	counts = dict.fromkeys(CATEGORIES, 0)
	for address in addresses:
		counts[classify(address)] += 1
	return {
		"total": sum(counts.values()),
		"proxy": sum(counts[c] for c in PROXY_CATEGORIES),
		"counts": counts,
	}


def regression_message(summary, days=LOOKBACK_DAYS):
	"""The Error Log body when ``summary`` shows the derivation broken, else None.

	No logins is not evidence either way -- a quiet weekend must not read as a
	pass or as a failure -- so it returns None. One proxy address is enough: a
	working chain produces none at all (0 of 143 from 2026-08-03 to 2026-09-22).
	"""
	if not summary.get("total") or not summary.get("proxy"):
		return None
	counts = summary["counts"]
	return (
		f"{summary['proxy']} of {summary['total']} logins in the last {days} days recorded a Google "
		f"load-balancer address instead of the visitor's ({counts['gclb_proxy']} front-end, "
		f"{counts['load_balancer']} forwarding-rule).\n\n"
		"frappe.local.request_ip is no longer the client. Every IP-keyed rate limit -- including the "
		"web-lead ingress -- is now one bucket shared by every caller, and login forensics record "
		"no client.\n\n"
		"On the VM: confirm /etc/nginx/conf.d/00-realip.conf exists and matches "
		"apps/erpnext_enhancements/infra/configs/nginx-realip.conf, then `sudo nginx -t && sudo "
		"systemctl reload nginx`. If the load balancer's forwarding-rule address changed, add it to "
		"that file AND to LB_FRONTEND_ADDRESSES in erpnext_enhancements/utils/client_ip.py.\n\n"
		"Audit: bench --site <site> execute erpnext_enhancements.utils.client_ip.audit_login_ips"
	)


# ------------------------------------------------------------------ frappe side


def _login_rows(since, fields):
	import frappe

	return frappe.get_all(
		"Activity Log",
		filters={"operation": "Login", "status": "Success", "creation": [">=", since]},
		fields=fields,
		order_by="creation asc",
		limit_page_length=0,
	)


def audit_login_ips(since="2026-07-01"):
	"""Before/after evidence: successful logins per month, classified.

	``bench --site <site> execute erpnext_enhancements.utils.client_ip.audit_login_ips``
	(optionally ``--kwargs "{'since': '2026-08-01'}"``). Read-only.

	Activity Log keeps ~90 days, so anything older than that has been pruned and
	cannot be re-measured -- which is why the 2026-08-13 counts for May and June
	no longer reproduce.
	"""
	rows = _login_rows(since, ["creation", "ip_address"])

	months = {}
	last_proxy = None
	for row in rows:
		created = str(row.get("creation") or "")
		month = created[:7]
		months.setdefault(month, []).append(row.get("ip_address"))
		if classify(row.get("ip_address")) in PROXY_CATEGORIES:
			last_proxy = created

	after = [r.get("ip_address") for r in rows if last_proxy and str(r.get("creation") or "") > last_proxy]
	return {
		"since": since,
		"by_month": {month: summarize(addresses) for month, addresses in sorted(months.items())},
		"last_proxy_login": last_proxy,
		"since_last_proxy_login": summarize(after)
		if last_proxy
		else summarize(r.get("ip_address") for r in rows),
	}


def check_client_ip_derivation():
	"""Daily: write an Error Log row if recent logins came from the load balancer.

	Read-only apart from that row, and ungated -- it is one indexed query a day,
	and the failure it watches for is one nobody would otherwise see. Returns the
	summary so ``bench execute`` shows what it judged.

	Deliberately not throttled beyond its own cadence: one row a day for as long
	as the chain is broken is the right volume for a fault that silently disarms
	every IP-keyed control in the app.
	"""
	import frappe
	from frappe.utils import add_days, now_datetime

	rows = _login_rows(add_days(now_datetime(), -LOOKBACK_DAYS), ["ip_address"])
	summary = summarize(r.get("ip_address") for r in rows)
	message = regression_message(summary)
	if message:
		frappe.log_error(message, ALERT_TITLE)
	return summary
