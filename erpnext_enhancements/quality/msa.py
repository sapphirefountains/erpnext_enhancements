# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Master subcontractor agreements: expiry, rates, and which orders belong to a job.

WI-075 sub-phase L. `Project Contract.validate_msa_gate` already refuses a Statement of Work
without a **Signed** MSA for that supplier — and that is the whole of the check. It asks whether
the agreement was ever signed; it never asks whether it is **still in force**. An MSA signed in
2019, with superseded rates and a lapsed certificate of insurance, gates a Statement of Work
issued today exactly as well as one signed last week.

Unknown is a third answer, and both easy alternatives are wrong
----------------------------------------------------------------

None of the sixteen live contracts records an expiry date. So:

* **Treating "no expiry recorded" as expired** blocks every Statement of Work the company can
  currently issue, on the day this deploys, for a data gap rather than a real lapse.
* **Treating it as valid** is the vacuous pass this whole programme exists to stop — the check
  reports clean forever, on every agreement, and nobody goes looking.

So :func:`expiry_state` has a third answer, :data:`STATE_UNKNOWN`, which is reported and never
blocks. The gap becomes visible instead of becoming either a false alarm or a false all-clear.

The rate the contract actually promised
----------------------------------------

`Project Contract`'s four flat rate fields on a Statement of Work are a **snapshot**, frozen on
the day it was issued, and they stay that way: a signed agreement prints its own
`agreement_html`, and a rate that re-read live from the MSA would silently change a document
somebody has already signed. :func:`rate_drift` compares the snapshot against what the MSA says
today and reports the difference for a person to decide about. It never rewrites the snapshot.

Two purchase-order projects, and either one alone loses money
--------------------------------------------------------------

`Purchase Order.project` and `Purchase Order Item.project` disagree on real data. Measured on
production: **40 of 148 live pending lines carry no row project, and 32 of those sit under a
header that names the job.** On PRJ-00566 a row-only match returns 37 rows where the union
returns 63. A rollup written either way under-reports, and it under-reports *silently* — the
query runs, the number looks plausible, and the missing orders are simply absent.
:func:`order_project` is that union as one rule, so the two halves of the app cannot drift.

Imports only ``datetime``.
"""

from datetime import date, datetime, timedelta

#: In force, and not near enough to expiry to mention.
STATE_OK = "ok"
#: Inside the warning window.
STATE_WARN = "warn"
#: Inside the escalation window — close enough that somebody senior should hear about it.
STATE_ESCALATE = "escalate"
#: Past its expiry date.
STATE_EXPIRED = "expired"
#: No expiry recorded. **Not** valid and **not** expired; see the module docstring.
STATE_UNKNOWN = "unknown"

#: Default windows. The build spec asks for 60 days' notice with escalation at 14.
WARN_DAYS = 60
ESCALATE_DAYS = 14

#: Default term when an MSA records none. One year is the common commercial term and is only
#: ever used to *derive* an expiry for display — it is never written onto the contract, because
#: inventing a term and storing it would turn a guess into a fact somebody later quotes.
DEFAULT_TERM_MONTHS = 12

#: How a Statement of Work's four frozen rate fields map onto the MSA's rate schedule.
#: (snapshot fieldname, classification, rate_type, label)
SNAPSHOT_RATES = (
	("rate_journeyman", "Journeyman", "Hourly", "Journeyman hourly rate"),
	("rate_apprentice", "Apprentice", "Hourly", "Apprentice hourly rate"),
	("rate_equipment", "Equipment", "Equipment", "Equipment rate"),
	("materials_markup_percent", "Materials", "Markup %", "Materials markup"),
)

RATE_TYPES = ("Hourly", "Daily", "Unit", "Equipment", "Markup %")

#: Below this, two rates are the same number typed differently. Above it, somebody changed the
#: deal. A cent of float noise is not a rate change.
RATE_TOLERANCE = 0.005


def expires_on(signed_on, term_months=None, override=None):
	"""The date an MSA lapses, or ``None`` when it cannot be worked out.

	An explicit ``override`` always wins — that is somebody stating the fact. Otherwise the term
	is counted from the signing date. With neither a signing date nor an override there is no
	answer, and ``None`` is that answer rather than a guess.
	"""
	explicit = _as_date(override)
	if explicit:
		return explicit
	signed = _as_date(signed_on)
	if not signed:
		return None
	months = _int(term_months) or DEFAULT_TERM_MONTHS
	if months < 1:
		return None
	return _add_months(signed, months)


def expiry_state(expiry, today, warn_days=WARN_DAYS, escalate_days=ESCALATE_DAYS):
	"""``(state, days_remaining)``.

	``days_remaining`` is negative once expired and ``None`` when unknown, so a caller cannot
	accidentally render "expires in 0 days" for an agreement nobody has dated.
	"""
	expiry = _as_date(expiry)
	today = _as_date(today)
	if today is None:
		return STATE_UNKNOWN, None
	if expiry is None:
		return STATE_UNKNOWN, None

	remaining = (expiry - today).days
	if remaining < 0:
		return STATE_EXPIRED, remaining

	escalate = max(_int(escalate_days), 0)
	warn = max(_int(warn_days), 0)
	if remaining <= escalate:
		return STATE_ESCALATE, remaining
	if remaining <= warn:
		return STATE_WARN, remaining
	return STATE_OK, remaining


def gate_message(state, days, msa_name, expiry=None):
	"""What to tell somebody issuing a Statement of Work. ``None`` when there is nothing to say.

	Worded so the unknown case reads as a gap in the record rather than as an accusation: the
	sixteen live contracts have no expiry because the field did not exist, not because anybody
	neglected it.
	"""
	if state == STATE_OK:
		return None
	if state == STATE_UNKNOWN:
		return (
			f"MSA {msa_name} has no expiry date recorded, so whether it is still in force cannot "
			"be checked. Set its term or expiry date on the agreement."
		)
	if state == STATE_EXPIRED:
		return f"MSA {msa_name} expired on {expiry} — {abs(days)} days ago."
	if state == STATE_ESCALATE:
		return f"MSA {msa_name} expires on {expiry}, in {days} days. Renew it now."
	return f"MSA {msa_name} expires on {expiry}, in {days} days."


def blocks_issue(state, mode):
	"""Whether this expiry state should refuse a Statement of Work.

	``mode`` is Off / Warn / Block, and even on **Block** an *unknown* expiry never refuses. A
	missing field is a gap in the record, and blocking every agreement the company currently has
	on the day this deploys would be the change stopping the work rather than the risk.
	"""
	if mode != "Block":
		return False
	return state == STATE_EXPIRED


def effective_rate(lines, classification, rate_type, on_date):
	"""The rate in force on ``on_date``, or ``None``.

	A line with no dates is in force always. Where several lines match, the one that *started*
	most recently wins — a newer schedule supersedes an older one. Ties are resolved by input
	order rather than arbitrarily, so the same inputs always give the same answer.
	"""
	on_date = _as_date(on_date)
	best = None
	best_start = None
	for line in lines or []:
		if (_get(line, "classification") or "").strip() != classification:
			continue
		if (_get(line, "rate_type") or "").strip() != rate_type:
			continue
		start = _as_date(_get(line, "effective_from"))
		end = _as_date(_get(line, "effective_to"))
		if on_date is not None:
			if start and start > on_date:
				continue
			if end and end < on_date:
				continue
		key = start or date.min
		if best is None or key > best_start:
			best, best_start = line, key
	return None if best is None else _float(_get(best, "rate"))


def rate_drift(snapshot, lines, on_date):
	"""Where a Statement of Work's frozen rates differ from what the MSA says today.

	Returns ``[{field, label, snapshot, current}, ...]``. **Reported, never applied.** A signed
	agreement prints its own snapshot, and quietly rewriting these would change a document
	somebody has already signed.

	A rate the MSA does not publish is not drift — plenty of Statements of Work carry a
	negotiated figure the schedule never listed, and calling that a discrepancy would make the
	check noise on the first day.
	"""
	out = []
	for field, classification, rate_type, label in SNAPSHOT_RATES:
		frozen = _get(snapshot, field)
		if frozen in (None, ""):
			continue
		current = effective_rate(lines, classification, rate_type, on_date)
		if current is None:
			continue
		if abs(_float(frozen) - current) > RATE_TOLERANCE:
			out.append(
				{
					"field": field,
					"label": label,
					"snapshot": _float(frozen),
					"current": current,
				}
			)
	return out


def overlapping_rate_lines(lines):
	"""Rate lines that cover the same classification and type at the same time.

	Two lines in force at once means the rate depends on which row a query read first, and a
	subcontractor invoice checked against it could be right or wrong depending on nothing. Named
	so the MSA can say so on save rather than leaving it to be discovered in an argument.
	"""
	buckets = {}
	for index, line in enumerate(lines or []):
		key = (
			(_get(line, "classification") or "").strip(),
			(_get(line, "rate_type") or "").strip(),
		)
		buckets.setdefault(key, []).append((index, line))

	clashes = []
	for key, rows in buckets.items():
		for i in range(len(rows)):
			for j in range(i + 1, len(rows)):
				if _overlaps(rows[i][1], rows[j][1]):
					clashes.append({"classification": key[0], "rate_type": key[1],
									"rows": (rows[i][0] + 1, rows[j][0] + 1)})
	return clashes


def order_project(row_project, header_project):
	"""Which project a purchase-order line belongs to.

	``ifnull(nullif(poi.project, ''), po.project)`` as one rule, in Python, because both halves
    of this app need the same answer and a second copy in SQL is a second thing to get wrong.

	Measured on production: 40 of 148 live pending lines carry no row project, and 32 of those
	sit under a header that names the job. A rollup keyed on the row alone loses them — on
	PRJ-00566 it returns 37 rows where the union returns 63 — and it loses them *silently*.
	"""
	row = (row_project or "").strip()
	if row:
		return row
	return (header_project or "").strip() or None


def _overlaps(a, b):
	a_start, a_end = _as_date(_get(a, "effective_from")), _as_date(_get(a, "effective_to"))
	b_start, b_end = _as_date(_get(b, "effective_from")), _as_date(_get(b, "effective_to"))
	a_start = a_start or date.min
	b_start = b_start or date.min
	a_end = a_end or date.max
	b_end = b_end or date.max
	return a_start <= b_end and b_start <= a_end


def _days_in_month(year, month):
	if month == 12:
		return 31
	return (date(year, month + 1, 1) - timedelta(days=1)).day


def _add_months(value, months):
	total = value.month - 1 + months
	year = value.year + total // 12
	month = total % 12 + 1
	return date(year, month, min(value.day, _days_in_month(year, month)))


def _as_date(value):
	if not value:
		return None
	if isinstance(value, datetime):
		return value.date()
	if isinstance(value, date):
		return value
	text = str(value).strip()
	if not text:
		return None
	for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
		try:
			return datetime.strptime(text, fmt).date()
		except ValueError:
			continue
	return None


def _float(value):
	try:
		return float(value or 0)
	except (TypeError, ValueError):
		return 0.0


def _int(value):
	try:
		return int(value)
	except (TypeError, ValueError):
		return 0


def _get(obj, field, default=None):
	if obj is None:
		return default
	if isinstance(obj, dict):
		return obj.get(field, default)
	return getattr(obj, field, default)
