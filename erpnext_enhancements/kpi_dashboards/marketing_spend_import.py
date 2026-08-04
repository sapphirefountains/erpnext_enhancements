# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""WP-4: get historical marketing spend into ERPNext, and keep channel names sane.

`Marketing Spend` has existed for a while and holds **zero rows**. Until it holds
something, there is no budget baseline, and "is this channel worth it" has no
denominator — which is the whole reason WP-4 exists.

## Why a bespoke importer rather than frappe's Data Import

Two reasons, both about the doctype's own naming rule.

`Marketing Spend` is named `format:SPEND-{month}-{channel}`, so a month/channel
pair is unique **by construction**. Re-importing a corrected spreadsheet — which
is what actually happens, because the first export from any ad platform is always
slightly wrong — collides on every row frappe's Data Import tries to insert. It
errors out rather than updating, and you are left hand-deleting rows. So the
import here is an **upsert**: an existing month/channel is updated in place.

Second, ad platforms and accounting exports disagree about what a channel is
called. "Google Ads", "google ads", "Google AdWords" and "AdWords" are one line
item to a marketer and four to a `GROUP BY`. Since `channel` is a free-text Data
field, nothing stops that fragmenting the rollup the moment two people type. The
importer canonicalises against `CHANNEL_ALIASES` and **reports every mapping it
made**, so a wrong guess is visible rather than silent.

## What it does not do

It does not invent months, split an annual figure across twelve rows, or allocate
a channel across value streams. Spend allocation is a judgement about intent;
`value_stream` is filled in by a person where they genuinely know it, and the
dashboard reports the rest as unallocated rather than dividing it arbitrarily.
"""

import csv
import io

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate

#: Canonical channel name -> the spellings seen in the wild. Lower-cased on both
#: sides at match time. Deliberately conservative: an alias here is a claim that
#: two strings are THE SAME LINE ITEM, and a wrong claim silently merges two
#: budgets. When unsure, leave it out and let the name pass through unchanged.
CHANNEL_ALIASES = {
	"Google Ads": ("google ads", "googleads", "google adwords", "adwords", "google ppc", "google paid search"),
	"Meta Ads": ("meta ads", "facebook ads", "facebook", "instagram ads", "meta", "fb ads"),
	"LinkedIn Ads": ("linkedin ads", "linkedin", "li ads"),
	"Trade Show": ("trade show", "tradeshow", "trade shows", "exhibition", "expo", "booth"),
	"Print": ("print", "print advertising", "magazine", "brochures", "direct mail"),
	"Sponsorship": ("sponsorship", "sponsorships", "sponsor"),
	"Website": ("website", "web", "site", "seo", "web development"),
	"Email": ("email", "email marketing", "newsletter", "mailchimp", "zoho campaigns"),
	"Signage": ("signage", "vehicle wrap", "vehicle wraps", "truck wrap"),
	"Photography": ("photography", "photo", "video", "videography", "content"),
	"Agency Retainer": ("agency", "agency retainer", "retainer", "marketing agency"),
}

#: Column headings accepted for each field, lower-cased and stripped. Ad-platform
#: exports never agree on these either.
COLUMN_ALIASES = {
	"month": ("month", "period", "date", "month starting", "spend month"),
	"channel": ("channel", "source", "campaign type", "medium", "line item", "description"),
	"amount": ("amount", "spend", "cost", "total", "amount spent", "usd", "$"),
	"value_stream": ("value stream", "value_stream", "stream", "service line"),
	"notes": ("notes", "note", "comment", "comments", "detail"),
}

#: Rows above this are refused outright. A file this size is a mistake — a
#: mis-picked export, or a per-click report rather than a per-month summary.
MAX_ROWS = 2000

#: Roles allowed to import. This writes the budget baseline every downstream
#: spend figure is divided by.
IMPORTER_ROLES = {"System Manager", "Sales Manager", "Accounts Manager"}


def canonical_channel(raw):
	"""Map a channel spelling onto its canonical name.

	Returns the input stripped-but-unchanged when nothing matches — an unknown
	channel is a new channel, not an error. Fragmentation is worth flagging, not
	worth blocking an import over.
	"""
	value = (raw or "").strip()
	if not value:
		return ""
	needle = value.lower()
	for canonical, aliases in CHANNEL_ALIASES.items():
		if needle == canonical.lower() or needle in aliases:
			return canonical
	return value


def _normalise_month(raw):
	"""Coerce anything month-shaped to the first of that month.

	Spend is monthly. Storing 2026-03-17 because that is when the invoice landed
	makes every ``GROUP BY month`` produce one bucket per invoice date.
	"""
	if not raw:
		return None
	value = str(raw).strip()
	# "2026-03" and "03/2026" are both common in platform exports and neither
	# parses as a date on its own.
	if len(value) == 7 and value[4] in "-/":
		value = value.replace("/", "-") + "-01"
	elif len(value) == 7 and value[2] in "-/":
		month, year = value.replace("/", "-").split("-")
		value = f"{year}-{month}-01"
	try:
		return getdate(value).replace(day=1)
	except Exception:
		return None


def _normalise_amount(raw):
	"""Strip currency formatting. '$1,234.56' and '(500)' both appear in exports.

	Returns None for anything unreadable — and deliberately does **not** use
	``frappe.utils.flt``, which coerces junk to 0.0 rather than raising. A cell
	reading "n/a" or "TBC" would otherwise import as $0 of spend: a silent zero in
	the denominator of every cost-per-lead figure, indistinguishable from a month
	where nothing was actually spent. A refused row is visible; a zero is not.
	"""
	if raw in (None, ""):
		return None
	value = str(raw).strip().replace(",", "").replace("$", "").replace("£", "")
	negative = value.startswith("(") and value.endswith(")")
	if negative:
		value = value[1:-1].strip()
	if not value:
		return None
	try:
		amount = float(value)
	except (TypeError, ValueError):
		return None
	return -amount if negative else amount


def _map_columns(headings):
	"""Map a CSV's headings onto our fields. Unrecognised columns are ignored."""
	mapping = {}
	for index, heading in enumerate(headings):
		needle = (heading or "").strip().lower()
		for field, aliases in COLUMN_ALIASES.items():
			if needle in aliases and field not in mapping:
				mapping[field] = index
				break
	return mapping


def parse_rows(content):
	"""Parse CSV text into normalised dicts plus a list of problems.

	Pure — no database access — so the whole normalisation path is testable
	without a bench, which is where the fiddly bits (month coercion, currency
	stripping, alias mapping) actually live.
	"""
	problems = []
	try:
		reader = csv.reader(io.StringIO(content))
		rows = list(reader)
	except Exception as exc:
		return [], [_("Could not read the file: {0}").format(exc)]

	if not rows:
		return [], [_("The file is empty.")]
	if len(rows) - 1 > MAX_ROWS:
		return [], [_("{0} rows — this looks like a per-click export rather than a monthly summary.").format(len(rows) - 1)]

	mapping = _map_columns(rows[0])
	for required in ("month", "channel", "amount"):
		if required not in mapping:
			problems.append(
				_("No column recognised as '{0}'. Accepted headings: {1}").format(
					required, ", ".join(COLUMN_ALIASES[required])
				)
			)
	if problems:
		return [], problems

	parsed = []
	for line_number, row in enumerate(rows[1:], start=2):
		if not any((cell or "").strip() for cell in row):
			continue

		def cell(field):
			index = mapping.get(field)
			return row[index] if index is not None and index < len(row) else ""

		month = _normalise_month(cell("month"))
		raw_channel = (cell("channel") or "").strip()
		channel = canonical_channel(raw_channel)
		amount = _normalise_amount(cell("amount"))

		if not month:
			problems.append(_("Line {0}: could not read a month from {1!r}").format(line_number, cell("month")))
			continue
		if not channel:
			problems.append(_("Line {0}: no channel").format(line_number))
			continue
		if amount is None:
			problems.append(_("Line {0}: could not read an amount from {1!r}").format(line_number, cell("amount")))
			continue

		parsed.append({
			"line": line_number,
			"month": str(month),
			"channel": channel,
			"raw_channel": raw_channel,
			"renamed": channel != raw_channel,
			"amount": amount,
			"value_stream": (cell("value_stream") or "").strip(),
			"notes": (cell("notes") or "").strip(),
		})

	return parsed, problems


@frappe.whitelist()
def preview_import(content):
	"""Parse and report, writing nothing.

	Deliberately a separate call from ``run_import``. Spend is the denominator of
	every marketing figure in the business; nobody should discover that a column
	was misread by looking at the dashboard a week later.
	"""
	_check_permission()
	parsed, problems = parse_rows(content or "")

	renamed = [
		{"line": row["line"], "from": row["raw_channel"], "to": row["channel"]}
		for row in parsed if row["renamed"]
	]
	existing = []
	for row in parsed:
		name = _spend_name(row["month"], row["channel"])
		if frappe.db.exists("Marketing Spend", name):
			existing.append({"line": row["line"], "name": name})

	return {
		"rows": parsed,
		"problems": problems,
		"renamed": renamed,
		"will_update": existing,
		"will_create": len(parsed) - len(existing),
		"total": sum(flt(r["amount"]) for r in parsed),
	}


@frappe.whitelist()
def run_import(content, batch_label=None):
	"""Upsert the parsed rows. Returns what it did, per row.

	Upsert rather than insert: a month/channel pair is unique by the doctype's
	naming rule, and re-importing a corrected export is the normal case, not the
	exception.
	"""
	_check_permission()
	parsed, problems = parse_rows(content or "")
	if problems and not parsed:
		return {"created": 0, "updated": 0, "problems": problems}

	batch = (batch_label or "").strip()[:140] or frappe.utils.now()

	created = updated = 0
	for row in parsed:
		name = _spend_name(row["month"], row["channel"])
		value_stream = row["value_stream"]
		if value_stream and not frappe.db.exists("Value Streams", value_stream):
			problems.append(
				_("Line {0}: value stream {1!r} does not exist — imported without one.").format(
					row["line"], value_stream
				)
			)
			value_stream = ""

		if frappe.db.exists("Marketing Spend", name):
			doc = frappe.get_doc("Marketing Spend", name)
			updated += 1
		else:
			doc = frappe.new_doc("Marketing Spend")
			created += 1

		doc.month = row["month"]
		doc.channel = row["channel"]
		doc.amount = row["amount"]
		if value_stream:
			doc.value_stream = value_stream
		if row["notes"]:
			doc.notes = row["notes"]
		doc.source_note = batch

		doc.flags.ignore_permissions = True
		doc.save() if not doc.is_new() else doc.insert(ignore_permissions=True)

	return {"created": created, "updated": updated, "problems": problems, "batch": batch}


def _spend_name(month, channel):
	"""Reproduce the doctype's own ``format:SPEND-{month}-{channel}`` naming."""
	return f"SPEND-{month}-{channel}"


def _check_permission():
	if not IMPORTER_ROLES.intersection(frappe.get_roles()):
		frappe.throw(_("You are not permitted to import marketing spend."), frappe.PermissionError)


def validate_spend(doc, method=None):
	"""``Marketing Spend.validate``: canonicalise and snap to the first of the month.

	Hand-entered rows go through the same normalisation as imported ones, or the
	rollup fragments the first time somebody types "google ads" into the form.
	"""
	if getattr(doc, "channel", None):
		doc.channel = canonical_channel(doc.channel)
	month = _normalise_month(getattr(doc, "month", None))
	if month:
		doc.month = month
	if flt(getattr(doc, "amount", 0)) < 0 and not cint(getattr(doc.flags, "allow_negative_spend", 0)):
		# A credit or refund is real, but it is nearly always a mis-parsed
		# parenthesised figure. Warn rather than block.
		frappe.msgprint(
			_("Negative spend recorded for {0}. If this is a refund or credit, that is fine — "
			  "otherwise check the source figure.").format(doc.channel or _("this row")),
			indicator="orange",
			alert=True,
		)
