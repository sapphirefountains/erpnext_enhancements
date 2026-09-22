"""Search Console property forms and rolling sums. Standard library only.

TASK-2026-01474. Search Console answered HTTP 403 on every nightly pull from 2026-06-26, and
the organic figures on the Marketing workspace have read zero for the whole history of the
dataset. Part of that is a Google-side grant (the service account must be a user on the
property). Part of it is this app's own request: ``GA4 Settings.gsc_property_url`` on prod
is the bare string ``sapphirefountains.com``, and the Search Console API names a property
only as ``sc-domain:example.com`` (a Domain property) or a full URL prefix with its trailing
slash (``https://www.example.com/``). Google reads a bare string as the ``http://`` URL
prefix -- every nightly 403 names ``'http://sapphirefountains.com'`` -- which is one
narrow property among five, and a property that does not exist or is not shared draws the
same 403 as a missing grant, so the two causes are indistinguishable from the outside.

Nobody knows which form the property was registered as (decided 2026-09-22: support both),
so :func:`site_candidates` turns the stored value into the forms to try, in order, and
``api/analytics.py`` uses the first one Google accepts.
"""

import datetime

#: The nightly snapshot's window: ``today - 30`` to ``today`` inclusive, as
#: ``api/analytics.get_gsc_data`` has always queried it. Kept identical so a backfilled
#: night means the same thing as a live one.
WINDOW_DAYS = 30


def site_candidates(value):
	"""The Search Console ``siteUrl`` strings to try, in order, for a stored value.

	* ``sc-domain:...`` or an ``http(s)://`` prefix is taken as meant (a prefix gets the
	  trailing slash the API requires).
	* A bare domain -- the prod value -- is tried as a Domain property first, then as the
	  ``https`` URL prefixes (``www`` first), then the ``http`` ones. The last is the form
	  Google reads a bare string as -- every 403 on prod names ``'http://sapphirefountains.com'``
	  -- so the request that has always been made is still in the list, just no longer the
	  only one.
	"""
	value = (value or "").strip()
	if not value:
		return []
	if value.startswith("sc-domain:"):
		return [value]
	if value.startswith(("http://", "https://")):
		return [value if value.endswith("/") else value + "/"]
	host = value.strip("/").lower()
	bare = host[4:] if host.startswith("www.") else host
	return list(
		dict.fromkeys(
			[
				f"sc-domain:{bare}",
				f"https://www.{bare}/",
				f"https://{bare}/",
				f"http://www.{bare}/",
				f"http://{bare}/",
			]
		)
	)


def window_for(day, days=WINDOW_DAYS):
	"""``(start, end)`` of the rolling window a snapshot taken on ``day`` covers."""
	return day - datetime.timedelta(days=days), day


def rolling_sums(daily, snapshot_days, days=WINDOW_DAYS):
	"""``{day: (clicks, impressions)}`` summed over each snapshot day's window.

	``daily`` maps ISO date strings (Search Console's ``keys[0]``) to ``(clicks,
	impressions)``. A day with no row counts as zero -- Search Console omits days with no
	data rather than returning zeros.
	"""
	out = {}
	for day in snapshot_days:
		start, end = window_for(day, days)
		clicks = impressions = 0
		cursor = start
		while cursor <= end:
			c, i = daily.get(cursor.isoformat(), (0, 0))
			clicks += c
			impressions += i
			cursor += datetime.timedelta(days=1)
		out[day] = (int(round(clicks)), int(round(impressions)))
	return out
