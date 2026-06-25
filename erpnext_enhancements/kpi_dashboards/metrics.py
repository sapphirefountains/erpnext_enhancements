"""Pure KPI math — no frappe import, so it runs in the bench-free CI suite.

These helpers turn a raw metric value plus its target into the presentation
fields a snapshot value carries: the Good/Watch/Bad status, the period-over-period
trend, a human display string, and a source-staleness check. Keeping them pure
(deterministic, side-effect-free, ``now`` injectable) makes the grading logic
unit-testable without a database — see ``tests/test_kpi_metrics.py``.
"""

from datetime import datetime

# A value within this fraction of its target counts as "Watch" rather than "Bad".
WATCH_BAND = 0.10

HIGHER = "Higher is better"
LOWER = "Lower is better"


def compute_status(value, target, direction=HIGHER, watch_band=WATCH_BAND):
	"""Grade ``value`` against ``target`` -> "Good" | "Watch" | "Bad" | "".

	Returns "" (no badge) when there is no usable target. ``direction`` decides
	whether being above or below target is good.
	"""
	if target is None:
		return ""
	try:
		value = float(value)
		target = float(target)
	except (TypeError, ValueError):
		return ""

	if target == 0:
		# A zero target only makes sense for "lower is better" (e.g. 0 failed
		# syncs): exactly zero is Good, anything above it is Bad.
		if direction == LOWER:
			return "Good" if value <= 0 else "Bad"
		return ""

	ratio = value / target
	if direction == LOWER:
		if ratio <= 1:
			return "Good"
		if ratio <= 1 + watch_band:
			return "Watch"
		return "Bad"
	# higher is better
	if ratio >= 1:
		return "Good"
	if ratio >= 1 - watch_band:
		return "Watch"
	return "Bad"


def compute_trend_pct(value, prior):
	"""Percent change of ``value`` vs the prior snapshot's value (None if N/A)."""
	if prior is None:
		return None
	try:
		value = float(value)
		prior = float(prior)
	except (TypeError, ValueError):
		return None
	if prior == 0:
		return None
	return (value - prior) / abs(prior) * 100.0


def fmt_value(value, unit=""):
	"""Human display string for a value given its unit."""
	try:
		v = float(value)
	except (TypeError, ValueError):
		return str(value) if value is not None else ""
	unit = (unit or "").strip()
	if unit == "USD":
		return f"${v:,.0f}"
	if unit == "%":
		return f"{v:,.1f}%"
	if unit == "days":
		return f"{v:,.1f} d"
	if unit in ("count", ""):
		if v == int(v):
			return f"{int(v):,}"
		return f"{v:,.1f}"
	return f"{v:,.2f} {unit}"


def _parse_dt(value):
	"""Coerce a frappe datetime/date or its string form to a naive datetime."""
	if isinstance(value, datetime):
		return value.replace(tzinfo=None)
	if hasattr(value, "year") and not isinstance(value, datetime):
		# a date — midnight
		return datetime(value.year, value.month, value.day)
	if isinstance(value, str):
		for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
			try:
				return datetime.strptime(value.strip()[:26], fmt)
			except ValueError:
				continue
	return None


def is_source_stale(last_sync, max_age_hours=6, now=None):
	"""True when an upstream source last synced more than ``max_age_hours`` ago
	(or never). ``now`` is injectable for deterministic tests."""
	if not last_sync:
		return True
	dt = _parse_dt(last_sync)
	if dt is None:
		return True
	now = now or datetime.now()
	age_hours = (now - dt).total_seconds() / 3600.0
	return age_hours > max_age_hours
