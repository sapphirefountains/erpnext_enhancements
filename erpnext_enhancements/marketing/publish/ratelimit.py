# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Publishing rate limits: pure decisions, mirrored in Redis Lua (TASK-2026-01482).

**The bucket is an optimisation; backoff is the correctness mechanism.** A limit here only
keeps the sweep from asking for something the network would refuse. What makes a refusal
safe is elsewhere and stays: the transport's retry rules (reads retry on 408/429/5xx, writes
never on their own), and the outbox's classification (a 429 retries later; any other 4xx is
a fault to read, not to retry). Never delete a retry path because a limit exists.

The shape follows the retired chat module's limiter, which this doc now replaces. Each rule is
a **pure, total function** over plain values, and that function is the tested specification.
Where the rule must hold across workers, a **Lua script** printed next to it does the same
thing atomically in Redis. ``tests/test_marketing_ratelimit.py`` runs every script against its
function (fakeredis + lupa in CI) over the same cases, so the two cannot drift.

The limits, as of 2026-09-22 (the task text's numbers were already stale; see the approvals
packet):

* **Instagram:** a rolling 24-hour count of API posts per account. Meta's docs say 100 in one
  place and 50 in another, so the limiter uses the **live** figure when the Meta publisher has
  read it (``GET /{ig-user-id}/content_publishing_limit``, TASK-2026-01483, via
  ``set_instagram_limit``) and the most conservative figure ever cited, 25, until then.
* **YouTube:** ``videos.insert`` has its own bucket of 100 calls a day; thumbnails and playlist
  adds cost about 50 units each from the 10,000-unit general budget. Both reset at midnight
  **Pacific**, whatever the site's time zone.
* **Meta:** ``X-App-Usage`` and ``X-Business-Use-Case-Usage`` are authoritative. Near the limit,
  or when Meta names a time to regain access, the connection pauses.
* **Any network:** a 429's ``Retry-After`` pauses the connection that long.

Facebook Page and LinkedIn posting have no local bucket: their limits are generous for a
company this size, and a 429 pauses them anyway.
"""

import datetime
import json
from email.utils import parsedate_to_datetime

from erpnext_enhancements.marketing.publish import constants as P

DAY = 86400

# ---------------------------------------------------------------- rolling window


def window_decision(stamps, now, limit, window):
	"""``(admit, remaining, retry_after)`` for one more event at ``now``. Pure and total.

	``stamps`` are the times (epoch seconds) of events already admitted. An event older than
	``now - window`` has left the window. ``remaining`` counts what is left *after* this one.
	A limit lowered below what the window already holds (Meta can do that) waits until enough
	events leave; a limit of 0 or less never admits.
	"""
	live = sorted(t for t in stamps if t > now - window)
	if limit > 0 and len(live) < limit:
		return True, limit - len(live) - 1, 0
	if limit <= 0 or not live:
		return False, 0, window
	# The (count - limit)th oldest must leave before there is room for one more.
	return False, 0, max(live[len(live) - limit] + window - now, 0)


#: KEYS[1] a sorted set of admitted events scored by time. ARGV: now, window, limit, member.
#: ZREMRANGEBYSCORE removes scores <= now - window, matching ``t > now - window`` above.
#: retry_after is returned as a string: Redis truncates a Lua number to an integer.
WINDOW_LUA = """
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now - window)
local count = redis.call('ZCARD', KEYS[1])
if limit > 0 and count < limit then
  redis.call('ZADD', KEYS[1], now, ARGV[4])
  redis.call('PEXPIRE', KEYS[1], math.ceil(window * 1000))
  return {1, limit - count - 1, '0'}
end
if limit <= 0 or count == 0 then
  return {0, 0, tostring(window)}
end
local entry = redis.call('ZRANGE', KEYS[1], count - limit, count - limit, 'WITHSCORES')
local wait = tonumber(entry[2]) + window - now
if wait < 0 then wait = 0 end
return {0, 0, tostring(wait)}
"""

# ---------------------------------------------------------------- daily budgets


def budgets_decision(used, costs, budgets):
	"""``(admit, remaining)`` for spending ``costs[i]`` from each budget at once. Pure and total.

	All or nothing: if any budget cannot take its cost, nothing is spent. ``remaining`` is,
	per budget, what is left after this spend (admitted) or now (refused). A negative cost is
	treated as 0.
	"""
	costs = [max(c, 0) for c in costs]
	fits = all(u + c <= b for u, c, b in zip(used, costs, budgets, strict=True))
	if fits:
		return True, [b - u - c for u, c, b in zip(used, costs, budgets, strict=True)]
	return False, [max(b - u, 0) for u, b in zip(used, budgets, strict=True)]


#: KEYS: one counter per budget. ARGV: ttl, then (cost, budget) per key. All or nothing.
BUDGETS_LUA = """
local n = #KEYS
local used = {}
for i = 1, n do
  used[i] = tonumber(redis.call('GET', KEYS[i]) or '0')
end
local fits = true
for i = 1, n do
  local cost = math.max(tonumber(ARGV[2 * i]), 0)
  if used[i] + cost > tonumber(ARGV[2 * i + 1]) then fits = false end
end
local out = {}
for i = 1, n do
  local cost = math.max(tonumber(ARGV[2 * i]), 0)
  local budget = tonumber(ARGV[2 * i + 1])
  if fits then
    if cost > 0 then redis.call('INCRBY', KEYS[i], cost) end
    redis.call('EXPIRE', KEYS[i], tonumber(ARGV[1]))
    out[i] = budget - used[i] - cost
  else
    out[i] = math.max(budget - used[i], 0)
  end
end
return {fits and 1 or 0, out}
"""

# ---------------------------------------------------------------- YouTube's quota day


def _nth_sunday(year, month, n):
	first = datetime.date(year, month, 1)
	return first + datetime.timedelta(days=(6 - first.weekday()) % 7, weeks=n - 1)


def pacific_offset_hours(now_utc):
	"""UTC offset of US Pacific time at ``now_utc`` (naive UTC). Pure; US rules since 2007.

	Computed rather than read from ``zoneinfo`` because Windows Pythons often ship without the
	tz database; the tests check it against ``zoneinfo`` wherever that is available.
	"""
	year = now_utc.year
	starts = datetime.datetime.combine(_nth_sunday(year, 3, 2), datetime.time(10))  # 02:00 PST
	ends = datetime.datetime.combine(_nth_sunday(year, 11, 1), datetime.time(9))  # 02:00 PDT
	return -7 if starts <= now_utc < ends else -8


def quota_day(now_utc):
	"""The YouTube quota day (it resets at midnight Pacific) for ``now_utc`` (naive UTC). Pure."""
	return (now_utc + datetime.timedelta(hours=pacific_offset_hours(now_utc))).date()


def seconds_to_quota_reset(now_utc):
	"""Seconds until the next YouTube quota reset. Pure."""
	local = now_utc + datetime.timedelta(hours=pacific_offset_hours(now_utc))
	midnight = datetime.datetime.combine(local.date() + datetime.timedelta(days=1), datetime.time())
	# Convert the local midnight back with the offset in force then (a DST change can fall in between).
	offset_then = pacific_offset_hours(midnight - datetime.timedelta(hours=pacific_offset_hours(now_utc)))
	return max(int((midnight - datetime.timedelta(hours=offset_then) - now_utc).total_seconds()), 1)


# ---------------------------------------------------------------- Meta's usage headers and Retry-After


def _header(headers, name):
	for key, value in (headers or {}).items():
		if str(key).lower() == name:
			return value
	return None


def _json(value):
	if isinstance(value, dict | list):
		return value
	try:
		return json.loads(value) if value else None
	except (TypeError, ValueError):
		return None


def meta_pause_seconds(headers, threshold=None, default_pause=None):
	"""How long to stop calling Meta, from its usage headers; 0 to carry on. Pure and total.

	``X-App-Usage`` is ``{"call_count": %, "total_cputime": %, "total_time": %}``;
	``X-Business-Use-Case-Usage`` maps a business ID to a list of the same per use case, plus
	``estimated_time_to_regain_access`` in minutes. A named time wins; otherwise any figure at
	or over the threshold pauses for the default. A header that does not parse pauses nothing:
	backoff still protects, and a guess would only stall posts.
	"""
	threshold = P.META_USAGE_PAUSE_PERCENT if threshold is None else threshold
	default_pause = P.META_DEFAULT_PAUSE_SECONDS if default_pause is None else default_pause
	percents = []
	regain_minutes = 0
	app = _json(_header(headers, "x-app-usage"))
	if isinstance(app, dict):
		percents.extend(v for v in app.values() if isinstance(v, int | float))
	business = _json(_header(headers, "x-business-use-case-usage"))
	if isinstance(business, dict):
		for entries in business.values():
			for entry in entries if isinstance(entries, list) else []:
				if not isinstance(entry, dict):
					continue
				percents.extend(
					v
					for k, v in entry.items()
					if k != "estimated_time_to_regain_access" and isinstance(v, int | float)
				)
				regain = entry.get("estimated_time_to_regain_access")
				if isinstance(regain, int | float) and regain > regain_minutes:
					regain_minutes = regain
	if regain_minutes > 0:
		return int(regain_minutes * 60)
	if percents and max(percents) >= threshold:
		return int(default_pause)
	return 0


def retry_after_seconds(value, now_utc=None):
	"""A ``Retry-After`` value (seconds, or an HTTP date) in seconds; None if absent or garbage. Pure."""
	if value is None or str(value).strip() == "":
		return None
	text = str(value).strip()
	try:
		return max(int(float(text)), 0)
	except ValueError:
		pass
	try:
		when = parsedate_to_datetime(text)
	except (TypeError, ValueError, IndexError):
		return None
	if when is None:
		return None
	now_utc = now_utc or datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
	if when.tzinfo is not None:
		when = when.astimezone(datetime.UTC).replace(tzinfo=None)
	return max(int((when - now_utc).total_seconds()), 0)


def pause_after_response(connection, status, headers, now_utc=None):
	"""Seconds to pause ``connection`` after a response, 0 for none. Pure.

	A 429 pauses for its ``Retry-After`` (a minute if none is given). Meta's usage headers can
	ask for longer on any response, a success included: that is when they are worth reading.
	"""
	pause = 0
	if status == 429:
		after = retry_after_seconds(_header(headers, "retry-after"), now_utc)
		pause = after if after is not None else P.DEFAULT_429_PAUSE_SECONDS
	if connection == P.CONNECTION_META:
		pause = max(pause, meta_pause_seconds(headers))
	return min(pause, P.MAX_PAUSE_SECONDS)


def parse_instagram_limit(body):
	"""``(used, total)`` from ``content_publishing_limit``, or None if it does not parse. Pure."""
	rows = (body or {}).get("data") if isinstance(body, dict) else None
	if not rows or not isinstance(rows[0], dict):
		return None
	row = rows[0]
	total = (row.get("config") or {}).get("quota_total")
	used = row.get("quota_usage")
	if not isinstance(total, int) or not isinstance(used, int):
		return None
	return used, total


# ---------------------------------------------------------------- the Redis-backed limiter


def _utcnow():
	# Deliberately UTC, not frappe's now_datetime(), which is site-local: YouTube's quota day
	# is Pacific and the windows are epoch seconds, and mixing the two broke Turnstile once.
	return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def _epoch(now_utc):
	return (now_utc - datetime.datetime(1970, 1, 1)).total_seconds()


class RedisLimiter:
	"""The scripts above, run against ``frappe.cache()`` (a ``redis.Redis``) or a test double.

	Raw Redis calls do not get Frappe's site prefix on their own, so every key goes through
	``make_key`` when the cache has it.
	"""

	def __init__(self, cache=None):
		if cache is None:
			import frappe

			cache = frappe.cache()
		self.cache = cache

	def key(self, name):
		make_key = getattr(self.cache, "make_key", None)
		return make_key(name) if make_key else name

	def window(self, name, now, window, limit, member):
		admit, remaining, wait = self.cache.eval(WINDOW_LUA, 1, self.key(name), now, window, limit, member)
		return bool(int(admit)), int(remaining), float(wait)

	def budgets(self, names, costs, budgets, ttl):
		args = [ttl]
		for cost, budget in zip(costs, budgets, strict=True):
			args += [cost, budget]
		admit, remaining = self.cache.eval(BUDGETS_LUA, len(names), *[self.key(n) for n in names], *args)
		return bool(int(admit)), [int(r) for r in remaining]

	def pause(self, name, seconds):
		if seconds > 0:
			self.cache.set(self.key(name), "1", px=int(seconds * 1000))

	def paused_for(self, name):
		ms = self.cache.pttl(self.key(name))
		return max(ms, 0) / 1000 if ms and ms > 0 else 0

	def set_value(self, name, value, seconds):
		self.cache.set(self.key(name), str(value), ex=int(seconds))

	def get_int(self, name):
		value = self.cache.get(self.key(name))
		try:
			return int(value) if value is not None else None
		except (TypeError, ValueError):
			return None


# ---------------------------------------------------------------- what the sweep and transport call


def pause_key(connection):
	return f"marketing:publish:pause:{connection}"


def instagram_limit_key(account):
	return f"marketing:publish:ig_limit:{account}"


def set_instagram_limit(account, total, limiter=None):
	"""Record Instagram's live posting limit for an account (the Meta publisher reads it)."""
	(limiter or RedisLimiter()).set_value(instagram_limit_key(account), int(total), DAY)


def observe(connection, status, headers, limiter=None, now_utc=None):
	"""Called by the publish transport on every response. Pauses the connection if it should."""
	seconds = pause_after_response(connection, status, headers, now_utc)
	if seconds:
		(limiter or RedisLimiter()).pause(pause_key(connection), seconds)
	return seconds


def admit(job, limiter=None, now_utc=None):
	"""May the sweep claim ``job`` now? ``(ok, retry_after_seconds, note, quota_remaining)``.

	Consumes quota when it says yes: the claim that follows is the send. A refused publish does
	not give it back -- conservative, and the counts reset on their own.
	"""
	limiter = limiter or RedisLimiter()
	now_utc = now_utc or _utcnow()
	network = job.get("network")
	connection = P.CONNECTION_FOR.get(network)
	if connection:
		paused = limiter.paused_for(pause_key(connection))
		if paused:
			return False, paused, f"{connection} is paused by the network's rate limit", None

	if network == P.NETWORK_INSTAGRAM:
		account = job.get("social_account")
		limit = limiter.get_int(instagram_limit_key(account)) or P.INSTAGRAM_POSTS_PER_DAY_FALLBACK
		now = _epoch(now_utc)
		# A member per admission, not per job: a retried job counts again, which is conservative
		# and is also exactly what the pure window_decision() it mirrors does.
		ok, remaining, wait = limiter.window(
			f"marketing:publish:ig_window:{account}", now, DAY, limit, f"{job.get('name')}:{now}"
		)
		if not ok:
			return False, wait, f"Instagram's {limit} posts per 24 hours for this account is used up", 0
		return True, 0, "", remaining

	if network == P.NETWORK_YOUTUBE:
		day = quota_day(now_utc).isoformat()
		ok, remaining = limiter.budgets(
			[f"marketing:publish:yt_uploads:{day}", f"marketing:publish:yt_units:{day}"],
			[1, P.YOUTUBE_UPLOAD_EXTRA_UNITS],
			[P.YOUTUBE_UPLOADS_PER_DAY, P.YOUTUBE_UNITS_PER_DAY],
			2 * DAY,
		)
		if not ok:
			return False, seconds_to_quota_reset(now_utc), "YouTube's daily upload quota is used up", 0
		return True, 0, "", remaining[0]

	return True, 0, "", None
