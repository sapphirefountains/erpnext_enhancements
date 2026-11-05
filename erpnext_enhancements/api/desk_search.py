"""The Desk search box (Ctrl+K): records first, grouped, with a preview (v1.795.0).

Nik, 2026-11-04: "Searching in ERPNext sucks". Typing a customer's first name listed sixteen screen
names (Drive Link Candidate, Calendar Cutover Item, Report Cheques and Deposits Incorrectly
cleared...) above her Contact. Measured on prod (frappe 16.53.0) that afternoon:

- frappe's box scores every DocType name with a fuzzy subsequence match (search_utils.get_doctypes,
  fuzzy_match.js): "Drive Link Candidate" scored 200 for that name, and "Report Cheques and
  Deposits Incorrectly cleared" 195 because its letters appear in that order.
- This app's old live search (api/search.py, ``search_global_docs``) gave every record a flat
  index of 150, so her records sat at rows 17 to 36, under the fold. It ran ``LIKE '%<name>%'``
  over ``__global_search`` (773k rows, ~94% File rows), took ~0.46 s, and returned rows in table
  order: a longer name sharing her first letters before her, emails and Files mixed in, her Account
  at row 31.

This endpoint replaces that call. It reads the name, phone and email columns of the record types
people look for, ranks the matches, and answers them grouped:

  People (Contact, Lead) · Accounts (Customer) · Projects · Opportunities · Quotes, orders and
  invoices · Fountains and maintenance (Serial No, Sapphire Maintenance Contract and Record) ·
  Suppliers · Items

Nik picked the types and features on 2026-11-04: records first, grouped with headings, a preview
line (phone and email for people), related records under a person or account, search by phone,
email or document number, and phones too. Employees, the "/" key and type filters were offered and
left out. ERPNext's own Maintenance Visit and Schedule are left out because prod has none (the
Sapphire Maintenance doctypes replaced them).

Ranking (``score_row``), highest first: a document number ("707", "PRJ-707", "opp 58") > the title
is exactly the text > starts with it > a word starts with it > every word starts a word > contains
it > a phone match > a match on another field (account, email). A whole-word title match gets +8,
so "Dana Whitfield" beats "Danae Corp" for "Dana" (made-up names), and open work gets +5 (an Open project, an
unpaid invoice, an enabled account). Ties go to the most recently modified record. Groups are
ordered by their best row, ties in the order above.

Every text match is a bound LIKE; every identifier comes from SPECS intersected with the table's
real columns (``frappe.db.get_table_columns``), so a custom field missing on some site drops out
instead of raising. Two-letter text matches at the start of a word only, so "Al" finds Alpine and
Al Smith, not every name containing "al". Results are cut to what the person may read through
``frappe.get_list`` (role permissions, user permissions and permission-query hooks), the pattern
api/search.py already uses. It writes nothing and runs as the person typing.

The tables are small (2,864 Contacts, 1,714 Accounts, 665 Projects, 868 Opportunities on prod), so
a few LIKE scans per keystroke cost far less than the old full-index scan.
"""

import re

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import cstr, flt, fmt_money, formatdate

MIN_CHARS = 2
MAX_CHARS = 140
# Candidate rows read per doctype before ranking; a group that hit it says "25+".
CANDIDATES = 25
# Pages of CANDIDATES read for one doctype while the person's permissions keep fewer than
# CANDIDATES of them: the cap is applied before the permission filter, so someone restricted by
# User Permissions or a permission-query hook would otherwise see nothing whenever the 25 best
# matches belong to others (review of PR #1425).
CANDIDATE_PAGES = 4
# Requests per IP per minute. Keyed by IP because frappe's limiter keys by IP or by a form field,
# not by user; the office shares one address, so this is generous for people and still a ceiling
# for a script.
RATE_LIMIT = 600
# Rows shown: the first group gets one more than the rest.
FIRST_GROUP_ROWS = 4
GROUP_ROWS = 3
# People and Accounts rows (from the top of their group) that get related-record links.
RELATED_ROWS = 2
RELATED_CAP = 500

GROUPS = (
	("people", "People"),
	("accounts", "Accounts"),
	("projects", "Projects"),
	("opportunities", "Opportunities"),
	("sales", "Quotes, orders and invoices"),
	("maintenance", "Fountains and maintenance"),
	("suppliers", "Suppliers"),
	("items", "Items"),
)
GROUP_ORDER = {key: i for i, (key, _label) in enumerate(GROUPS)}

# A letter prefix typed before a number names the series to look in; anything else is used as is.
NUMBER_PREFIXES = {
	"P": "PRJ",
	"PRJ": "PRJ",
	"PROJ": "PRJ",
	"PROJECT": "PRJ",
	"O": "OPP",
	"OPP": "OPP",
	"OPPORTUNITY": "OPP",
	"Q": "QTN",
	"QTN": "QTN",
	"QUOTE": "QTN",
	"QUOTATION": "QTN",
	"SO": "ORD",
	"ORD": "ORD",
	"ORDER": "ORD",
	"INV": "SINV",
	"SINV": "SINV",
	"INVOICE": "SINV",
	"MNT": "MNT-CON",
	"CON": "MNT-CON",
	"CONTRACT": "MNT-CON",
}

# One entry per searched doctype. title: first non-empty column is the row's title. match: text
# columns every typed word is looked for in. email/phone: matched too, phone by digits only.
# child_*: (child doctype, column) rows of the record. extra: read for the preview line. open /
# closed: (column, values) that make a row "open" for ranking. numbered: the name ends in a
# number (PRJ-00707), so a typed number can find it.
SPECS = (
	{
		"doctype": "Contact",
		"group": "people",
		"type": "Contact",
		"title": ("full_name",),
		"match": ("full_name", "first_name", "last_name", "company_name", "custom_account"),
		"email": ("custom_email", "email_id", "custom_secondary_email_address"),
		"phone": (
			"custom_phone_number",
			"custom_mobile_number",
			"mobile_no",
			"phone",
			"custom_home_phone",
			"custom_other_phone",
		),
		"child_email": ("Contact Email", "email_id"),
		"child_phone": ("Contact Phone", "phone"),
		"extra": ("custom_account", "company_name"),
		"related": "contact",
	},
	{
		"doctype": "Lead",
		"group": "people",
		"type": "Lead",
		"title": ("lead_name", "company_name", "title"),
		"match": ("lead_name", "company_name", "title"),
		"email": ("email_id",),
		"phone": ("custom_primary_phone", "mobile_no", "phone", "whatsapp_no"),
		"extra": ("status", "company_name"),
		# "Opportunity" means it became one, which is now the live record
		"closed": ("status", ("Converted", "Do Not Contact", "Lost Quotation", "Opportunity")),
		"related": "lead",
	},
	{
		"doctype": "Customer",
		"group": "accounts",
		"type": "Account",
		"title": ("customer_name",),
		"match": ("customer_name", "name"),
		"email": ("custom_primary_email_address", "custom_accounts_email_address", "email_id"),
		"phone": ("custom_primary_phone", "custom_accounts_phone_number", "mobile_no"),
		"extra": ("disabled", "custom_primary_first_name", "customer_group"),
		"disabled": "disabled",
		"related": "customer",
	},
	{
		"doctype": "Project",
		"group": "projects",
		"type": "Project",
		"title": ("project_name",),
		"match": ("project_name", "name", "customer", "custom_customer_name"),
		"extra": ("status", "customer", "percent_complete"),
		# prod uses Active; stock ERPNext says Open
		"open": ("status", ("Open", "Active")),
		"numbered": True,
	},
	{
		"doctype": "Opportunity",
		"group": "opportunities",
		"type": "Opportunity",
		# custom_opportunity_name is the title people see, not the name (CRM-OPP-...) or `title`
		"title": ("custom_opportunity_name", "title"),
		"match": (
			"custom_opportunity_name",
			"title",
			"name",
			"party_name",
			"customer_name",
			"contact_display",
		),
		"extra": ("status", "party_name", "customer_name"),
		# prod's pipeline lives in status (Qualification ... Closed Won, Lost); a won deal's live record is its project
		"closed": ("status", ("Lost", "Closed", "Converted", "Closed Won", "Closed Lost")),
		"numbered": True,
	},
	{
		"doctype": "Quotation",
		"group": "sales",
		"type": "Quotation",
		"title": ("customer_name", "party_name", "title"),
		"match": ("name", "customer_name", "party_name", "title"),
		"extra": ("status", "transaction_date", "grand_total", "currency"),
		"open": ("status", ("Draft", "Open", "Partially Ordered")),
		"numbered": True,
		"submittable": True,
	},
	{
		"doctype": "Sales Order",
		"group": "sales",
		"type": "Sales Order",
		"title": ("customer_name", "customer", "title"),
		"match": ("name", "customer_name", "customer", "title", "project"),
		"extra": ("status", "transaction_date", "grand_total", "currency"),
		"closed": ("status", ("Completed", "Closed", "Cancelled")),
		"numbered": True,
		"submittable": True,
	},
	{
		"doctype": "Sales Invoice",
		"group": "sales",
		"type": "Sales Invoice",
		"title": ("customer_name", "customer", "title"),
		"match": ("name", "customer_name", "customer", "title", "project"),
		"extra": ("status", "posting_date", "grand_total", "outstanding_amount", "currency"),
		"open": (
			"status",
			(
				"Draft",
				"Unpaid",
				"Overdue",
				"Partly Paid",
				"Unpaid and Discounted",
				"Overdue and Discounted",
				"Partly Paid and Discounted",
			),
		),
		"numbered": True,
		"submittable": True,
	},
	{
		"doctype": "Serial No",
		"group": "maintenance",
		"type": "Fountain",
		"title": ("name",),
		"match": ("name", "serial_no", "item_name", "customer", "custom_project", "location"),
		"extra": ("item_name", "customer", "status", "custom_project"),
		# blank on every fountain on prod, so only an explicit end counts as closed
		"closed": ("status", ("Inactive", "Expired")),
	},
	{
		"doctype": "Sapphire Maintenance Contract",
		"group": "maintenance",
		"type": "Maintenance contract",
		"title": ("customer",),
		"match": ("name", "customer", "project"),
		"extra": ("status", "customer", "project"),
		"open": ("status", ("Active",)),
		"numbered": True,
	},
	{
		"doctype": "Sapphire Maintenance Record",
		"group": "maintenance",
		"type": "Maintenance visit",
		"title": ("visit_label", "customer"),
		"match": ("name", "customer", "legacy_name", "visit_label", "serial_no", "project"),
		"extra": ("customer", "visit_date", "scheduled_visit_date", "workflow_state"),
		"submittable": True,  # is_submittable: a canceled visit is docstatus 2
	},
	{
		"doctype": "Supplier",
		"group": "suppliers",
		"type": "Supplier",
		"title": ("supplier_name",),
		"match": ("supplier_name", "name"),
		"email": ("custom_email", "email_id"),
		"phone": ("custom_phone_number", "mobile_no"),
		"extra": ("supplier_group", "disabled"),
		"disabled": "disabled",
	},
	{
		"doctype": "Item",
		"group": "items",
		"type": "Item",
		"title": ("item_name",),
		"match": ("item_name", "item_code", "name"),
		"extra": ("item_group", "disabled", "item_code"),
		"disabled": "disabled",
	},
)


# ------------------------------------------------------------------ the typed text


def parse_query(txt):
	"""What the text is: words, a phone number, a document number, or all three."""
	text = " ".join(cstr(txt).split())[:MAX_CHARS]
	words = text.split(" ") if text else []
	digits = re.sub(r"\D", "", text)
	# +1 801 555 0142 and 801-555-0142 are the same number; stored numbers vary.
	if len(digits) == 11 and digits.startswith("1"):
		digits = digits[1:]
	phone = len(digits) >= 4 and re.fullmatch(r"[\d\s().+\-]+", text) is not None
	number = prefix = None
	m = re.fullmatch(r"([A-Za-z]{1,11})?[\s\-#]*?(\d{1,6})", text)
	if m and int(m.group(2)) > 0:
		number = str(int(m.group(2)))
		raw = (m.group(1) or "").upper()
		prefix = NUMBER_PREFIXES.get(raw, raw) or None
	return {
		"text": text,
		"words": words,
		"digits": digits,
		"phone": phone,
		"number": number,
		"prefix": prefix,
	}


def like_escape(value):
	return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def at_word_start(haystack, needle):
	return bool(needle) and re.search(r"(?:^|[^0-9a-z])" + re.escape(needle), haystack) is not None


def whole_word(haystack, needle):
	return (
		bool(needle)
		and re.search(r"(?:^|[^0-9a-z])" + re.escape(needle) + r"(?:$|[^0-9a-z])", haystack) is not None
	)


def score_row(parsed, title, others=(), is_open=False, number_hit=False, phone_hit=False):
	"""How well one record matches. Pure; see the module docstring for the ladder."""
	phrase = parsed["text"].lower()
	words = [w.lower() for w in parsed["words"]]
	t = cstr(title).lower()
	if number_hit:
		score = 200
	elif t == phrase:
		score = 100
	elif t.startswith(phrase):
		score = 90
	elif at_word_start(t, phrase):
		score = 80
	elif len(words) > 1 and all(at_word_start(t, w) for w in words):
		score = 75
	elif phrase in t:
		score = 60
	elif len(words) > 1 and all(w in t for w in words):
		score = 55
	elif phone_hit:
		score = 50
	else:
		blob = " ".join(cstr(o).lower() for o in others if o)
		if at_word_start(blob, phrase) or (words and all(at_word_start(blob, w) for w in words)):
			score = 45
		elif words and all(w in blob for w in words):
			score = 40
		else:
			score = 30  # matched in a column the preview does not show (an email, a child row)
	if 60 <= score < 200 and whole_word(t, phrase):
		score += 8
	if is_open:
		score += 5
	return score


# ------------------------------------------------------------------ the query


def _available(spec, columns):
	"""The spec with every column the table does not have taken out (a site without a custom field)."""
	have = set(columns) | {"name"}
	out = dict(spec)
	for key in ("title", "match", "email", "phone", "extra"):
		out[key] = tuple(c for c in spec.get(key, ()) if c in have)
	for key in ("open", "closed", "disabled"):
		value = spec.get(key)
		column = value[0] if isinstance(value, tuple) else value
		if value and column not in have:
			out.pop(key)
	if not out["match"]:
		out["match"] = ("name",)
	return out


def _q(column):
	return "`" + column + "`"


def build_query(spec, parsed, child_tables=()):
	"""(sql, values) for one doctype's candidates. ``spec`` is already cut to real columns
	(``_available``); ``child_tables`` names the child doctypes that exist."""
	values = {"limit": CANDIDATES, "offset": 0}
	table = "`tab" + spec["doctype"] + "`"
	text_cols = spec["match"] + spec["email"]
	child_email = spec.get("child_email") if spec.get("child_email", (None,))[0] in child_tables else None
	child_phone = spec.get("child_phone") if spec.get("child_phone", (None,))[0] in child_tables else None
	if child_email or child_phone:
		values["ptype"] = spec["doctype"]

	word_blocks = []
	for i, word in enumerate(parsed["words"]):
		w = like_escape(word)
		if len(parsed["text"]) <= 2:
			values[f"w{i}a"], values[f"w{i}b"] = w + "%", "% " + w + "%"
			tests = [f"{_q(c)} like %(w{i}a)s or {_q(c)} like %(w{i}b)s" for c in text_cols]
		else:
			values[f"w{i}"] = "%" + w + "%"
			tests = [f"{_q(c)} like %(w{i})s" for c in text_cols]
			if child_email:
				tests.append(
					f"name in (select parent from `tab{child_email[0]}` where parenttype = %(ptype)s"
					f" and {_q(child_email[1])} like %(w{i})s)"
				)
		word_blocks.append("(" + " or ".join(tests) + ")")
	ors = ["(" + " and ".join(word_blocks) + ")"] if word_blocks else []

	if parsed["phone"] and (spec["phone"] or child_phone):
		values["digits"] = "%" + parsed["digits"] + "%"
		tests = [f"regexp_replace(ifnull({_q(c)}, ''), '[^0-9]', '') like %(digits)s" for c in spec["phone"]]
		if child_phone:
			tests.append(
				f"name in (select parent from `tab{child_phone[0]}` where parenttype = %(ptype)s"
				f" and regexp_replace(ifnull({_q(child_phone[1])}, ''), '[^0-9]', '') like %(digits)s)"
			)
		ors.append("(" + " or ".join(tests) + ")")

	number_hit = "0"
	if parsed["number"] and spec.get("numbered"):
		# The number is digits only (parse_query), so the pattern is safe to bind as is.
		values["num_re"] = "-0*" + parsed["number"] + "$"
		cond = "name regexp %(num_re)s"
		if parsed["prefix"]:
			values["num_prefix"] = "%" + like_escape(parsed["prefix"]) + "-%"
			cond = f"({cond} and name like %(num_prefix)s)"
		ors.append(cond)
		number_hit = cond

	if not ors:
		return None, None

	title = spec["title"][0] if spec["title"] else "name"
	values["phrase_start"] = like_escape(parsed["text"]) + "%"
	values["phrase_word"] = "% " + like_escape(parsed["text"]) + "%"
	select = ["name", "modified"]
	for key in ("title", "extra", "phone", "email"):
		select.extend(c for c in spec[key] if c not in select)
	where = "(" + " or ".join(ors) + ")"
	if spec.get("submittable"):
		where += " and docstatus < 2"
	sql = (
		"select " + ", ".join(_q(c) for c in select) + f", ({number_hit}) as _number_hit"
		f" from {table} where {where}"
		f" order by _number_hit desc, case when {_q(title)} like %(phrase_start)s then 0"
		f" when {_q(title)} like %(phrase_word)s then 1 else 2 end, modified desc"
		" limit %(limit)s offset %(offset)s"
	)
	return sql, values


# ------------------------------------------------------------------ rows


def _first(row, columns):
	for c in columns:
		value = cstr(row.get(c)).strip()
		if value:
			return value
	return ""


def _digits(value):
	d = re.sub(r"\D", "", cstr(value))
	return d[1:] if len(d) == 11 and d.startswith("1") else d


def is_open(spec, row):
	if spec.get("disabled") and row.get(spec["disabled"]):
		return False
	if spec.get("open"):
		column, allowed = spec["open"]
		return cstr(row.get(column)) in allowed
	if spec.get("closed"):
		column, shut = spec["closed"]
		return cstr(row.get(column)) not in shut
	return True


def _money(value, currency):
	try:
		return fmt_money(flt(value), currency=currency or None)
	except Exception:
		return cstr(value)


def _date(value):
	try:
		return formatdate(value) if value else ""
	except Exception:
		return cstr(value)


def subtitle(spec, row, title):
	"""The preview line under the title, plain text."""
	dt, g = spec["doctype"], row.get
	parts = []
	numbered = spec.get("numbered") and row.get("name") != title
	if numbered:
		parts.append(row.get("name"))
	if dt == "Contact":
		parts.append(cstr(g("custom_account")) or cstr(g("company_name")))
	elif dt == "Lead":
		if cstr(g("company_name")) != title:
			parts.append(g("company_name"))
		parts.append(g("status"))
	elif dt == "Customer":
		parts.append(g("custom_primary_first_name"))
		if g("disabled"):
			parts.append(_("Disabled"))
	elif dt == "Project":
		parts.append(g("customer"))
		parts.append(g("status"))
		if flt(g("percent_complete")):
			parts.append(_("{0}% done").format(int(flt(g("percent_complete")))))
	elif dt == "Opportunity":
		# prod keeps the pipeline in status; sales_stage reads "Prospecting" on every deal
		parts.append(cstr(g("customer_name")) or cstr(g("party_name")))
		parts.append(g("status"))
	elif dt in ("Quotation", "Sales Order"):
		parts.append(g("status"))
		parts.append(_date(g("transaction_date")))
		parts.append(_money(g("grand_total"), g("currency")))
	elif dt == "Sales Invoice":
		parts.append(g("status"))
		parts.append(_date(g("posting_date")))
		parts.append(_money(g("grand_total"), g("currency")))
		if flt(g("outstanding_amount")) > 0 and flt(g("outstanding_amount")) != flt(g("grand_total")):
			parts.append(_("{0} due").format(_money(g("outstanding_amount"), g("currency"))))
	elif dt == "Serial No":
		parts.append(g("item_name"))
		parts.append(g("customer"))
		if cstr(g("status")) not in ("", "Active"):
			parts.append(g("status"))
	elif dt == "Sapphire Maintenance Contract":
		parts.append(g("status"))
		parts.append(g("project"))
	elif dt == "Sapphire Maintenance Record":
		if cstr(g("customer")) != title:
			parts.append(g("customer"))
		parts.append(_date(g("visit_date") or g("scheduled_visit_date")))
		parts.append(g("workflow_state"))
	elif dt == "Supplier":
		parts.append(g("supplier_group"))
		if g("disabled"):
			parts.append(_("Disabled"))
	elif dt == "Item":
		if cstr(g("item_code") or g("name")) != title:
			parts.append(g("item_code") or g("name"))
		parts.append(g("item_group"))
		if g("disabled"):
			parts.append(_("Disabled"))
	seen, out = set(), []
	for p in parts:
		p = cstr(p).strip()
		if p and p not in seen:
			seen.add(p)
			out.append(p)
	return " · ".join(out)


def make_row(spec, row, parsed, child=None):
	"""One answer row from one candidate. ``child`` holds a Contact's phone and email rows."""
	child = child or {}
	title = _first(row, spec["title"]) or cstr(row.get("name"))
	phones = [cstr(row.get(c)).strip() for c in spec["phone"] if cstr(row.get(c)).strip()]
	phones += child.get("phones", [])
	emails = [cstr(row.get(c)).strip() for c in spec["email"] if cstr(row.get(c)).strip()]
	emails += child.get("emails", [])
	phone_hit = bool(parsed["phone"]) and any(parsed["digits"] in _digits(p) for p in phones)
	others = [cstr(row.get(c)) for c in spec["match"] if c not in spec["title"][:1]]
	others += emails
	scored = dict(
		parsed=parsed,
		title=title,
		others=others,
		number_hit=bool(row.get("_number_hit")),
		phone_hit=phone_hit,
	)
	return {
		"doctype": spec["doctype"],
		"name": cstr(row.get("name")),
		"title": title,
		"type": _(spec["type"]),
		"subtitle": subtitle(spec, row, title),
		"phone": phones[0] if phones else "",
		"email": emails[0] if emails else "",
		"route": ["Form", spec["doctype"], cstr(row.get("name"))],
		"score": score_row(is_open=is_open(spec, row), **scored),
		"modified": cstr(row.get("modified")),
		# how well it matched, without the open-work bonus: what orders the groups
		"_tier": score_row(is_open=False, **scored),
		"_spec": spec,
		"_row": row,
	}


# ------------------------------------------------------------------ groups


def rank_rows(rows):
	"""Best first; ties to the most recently modified."""
	rows = sorted(rows, key=lambda r: r["modified"], reverse=True)
	return sorted(rows, key=lambda r: r["score"], reverse=True)


def _see_all(doctype, every, capped, text):
	"""The list a group's "See all" opens. Everything that matched, by name, when we read it all;
	when the read was capped, the list filtered by the text on the title column, since a list of
	the names we happened to read would stop at the cap and call itself "all"."""
	if not capped or not text:
		return {"route": ["List", doctype], "route_options": {"name": ["in", [r["name"] for r in every]]}}
	titles = every[0]["_spec"].get("title") or ("name",)
	return {"route": ["List", doctype], "route_options": {titles[0]: ["like", "%" + text + "%"]}}


def build_groups(rows, capped=(), text=""):
	"""Group, order and cut the rows. ``capped`` names doctypes with more matches than were read."""
	by_group = {}
	for row in rows:
		by_group.setdefault(row["_spec"]["group"], []).append(row)
	groups = []
	for key, label in GROUPS:
		members = rank_rows(by_group.get(key, []))
		if not members:
			continue
		doctypes = {r["doctype"] for r in members}
		groups.append(
			{
				"key": key,
				"label": _(label),
				"total": len(members),
				"more": bool(doctypes & set(capped)),
				"best": max(r["_tier"] for r in members),
				"rows": members,
				"doctypes": doctypes,
			}
		)
	# By how well the best row matched, ties in GROUPS order; the open-work bonus orders rows within a
	# group only, so a draft invoice titled with the account's name never lifts its group above the
	# account's closed opportunities.
	groups.sort(key=lambda g: (-g["best"], GROUP_ORDER[g["key"]]))
	for i, group in enumerate(groups):
		shown = FIRST_GROUP_ROWS if i == 0 else GROUP_ROWS
		every = group["rows"]
		group["rows"] = every[:shown]
		group["see_all"] = None
		if len(every) > shown and len(group["doctypes"]) == 1:
			(doctype,) = group["doctypes"]
			group["see_all"] = _see_all(doctype, every, doctype in set(capped), text)
		del group["doctypes"]
	return groups


# ------------------------------------------------------------------ related records


def _count(doctype, filters):
	if not frappe.has_permission(doctype, "read"):
		return 0
	return len(frappe.get_list(doctype, filters=filters, pluck="name", limit_page_length=RELATED_CAP))


def _plural(n, one, many):
	return _(one) if n == 1 else _(many).format(n)


def _account_of_contact(row):
	account = cstr(row.get("custom_account"))
	if account:
		return account
	links = frappe.get_all(
		"Dynamic Link",
		filters={"parenttype": "Contact", "parent": row.get("name"), "link_doctype": "Customer"},
		pluck="link_name",
		limit=1,
	)
	return links[0] if links else ""


def related_for(row):
	"""Links under a person or an account: their account, and how many projects and opportunities."""
	kind = row["_spec"].get("related")
	out = []
	account = ""
	if kind == "contact":
		account = _account_of_contact(row["_row"])
		if account and frappe.has_permission("Customer", "read"):
			out.append({"label": account, "kind": _("Account"), "route": ["Form", "Customer", account]})
	elif kind == "customer":
		account = row["name"]
	elif kind == "lead":
		n = _count("Opportunity", {"opportunity_from": "Lead", "party_name": row["name"]})
		if n:
			out.append(
				{
					"label": _plural(n, "1 opportunity", "{0} opportunities"),
					"route": ["List", "Opportunity"],
					"route_options": {"opportunity_from": "Lead", "party_name": row["name"]},
				}
			)
		return out
	if not account:
		return out
	n = _count("Project", {"customer": account})
	if n:
		out.append(
			{
				"label": _plural(n, "1 project", "{0} projects"),
				"route": ["List", "Project"],
				"route_options": {"customer": account},
			}
		)
	n = _count("Opportunity", {"party_name": account})
	if n:
		out.append(
			{
				"label": _plural(n, "1 opportunity", "{0} opportunities"),
				"route": ["List", "Opportunity"],
				"route_options": {"party_name": account},
			}
		)
	return out


# ------------------------------------------------------------------ the endpoint


def _report_once(doctype):
	"""One Error Log per doctype per hour: this runs on every keystroke."""
	key = "ee_desk_search_error:" + doctype
	try:
		if frappe.cache.get_value(key):
			return
		frappe.cache.set_value(key, 1, expires_in_sec=3600)
	except Exception:
		pass
	# defer_insert: the row is queued in Redis and written by the scheduler, so it survives a request
	# whose transaction rolls back (frappe 16.53's sync_database rolls back every GET, and this
	# endpoint was GET until the PR #1425 review moved it to POST).
	frappe.log_error(
		title="Desk search: " + doctype + " not searched", message=frappe.get_traceback(), defer_insert=True
	)


def _contact_children(names, child_tables):
	"""Each Contact's phone and email child rows, primary first."""
	out = {n: {"phones": [], "emails": []} for n in names}
	if not names:
		return out
	pairs = (
		("Contact Phone", "phone", "is_primary_phone", "phones"),
		("Contact Email", "email_id", "is_primary", "emails"),
	)
	for child, column, primary, key in pairs:
		if child not in child_tables:
			continue
		rows = frappe.db.sql(
			f"select parent, `{column}` as v from `tab{child}`"
			f" where parenttype = 'Contact' and parent in %(names)s order by `{primary}` desc, idx",
			{"names": tuple(names)},
			as_dict=True,
		)
		for r in rows:
			value = cstr(r.get("v")).strip()
			if value and value not in out[r["parent"]][key]:
				out[r["parent"]][key].append(value)
	return out


def search_doctype(spec, parsed, child_tables):
	"""(rows, capped) for one doctype, already cut to what the person may read."""
	if not frappe.has_permission(spec["doctype"], "read"):
		return [], False
	spec = _available(spec, frappe.db.get_table_columns(spec["doctype"]))
	sql, values = build_query(spec, parsed, child_tables)
	if not sql:
		return [], False
	# The cap applies before the permission filter, so read further pages while the person's
	# permissions leave fewer than CANDIDATES: for anyone unrestricted the first page is kept whole
	# and this is one query, as before.
	kept, capped = [], False
	for page in range(CANDIDATE_PAGES):
		values["offset"] = page * CANDIDATES
		candidates = frappe.db.sql(sql, values, as_dict=True)
		if not candidates:
			break
		allowed = set(
			frappe.get_list(
				spec["doctype"],
				filters={"name": ["in", [c["name"] for c in candidates]]},
				pluck="name",
				limit_page_length=0,
			)
		)
		kept.extend(c for c in candidates if c["name"] in allowed)
		capped = len(candidates) >= CANDIDATES  # a full page: there may be more beyond it
		if not capped or len(kept) >= CANDIDATES:
			break
	children = {}
	if spec["doctype"] == "Contact":
		children = _contact_children([c["name"] for c in kept], child_tables)
	rows = [make_row(spec, c, parsed, children.get(c["name"])) for c in kept]
	return rows, capped


def _child_tables():
	out = set()
	for child in ("Contact Phone", "Contact Email"):
		try:
			if frappe.db.table_exists(child):
				out.add(child)
		except Exception:
			pass
	return out


def _public(row):
	return {k: v for k, v in row.items() if not k.startswith("_") and k != "modified"}


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=RATE_LIMIT, seconds=60)
def search(txt=None):
	"""Grouped record matches for the Desk search box. Read-only; runs as the person typing.

	POST, though it writes nothing: the text is names, phone numbers and emails, and a GET would
	put it in the query string of every request line prod's web tier logs (review of PR #1425).
	"""
	if frappe.session.user == "Guest":
		raise frappe.PermissionError
	parsed = parse_query(txt)
	if len(parsed["text"]) < MIN_CHARS:
		return {"txt": parsed["text"], "groups": []}
	child_tables = _child_tables()
	rows, capped = [], []
	for spec in SPECS:
		try:
			found, hit_cap = search_doctype(spec, parsed, child_tables)
		except Exception:
			_report_once(spec["doctype"])
			continue
		rows.extend(found)
		if hit_cap:
			capped.append(spec["doctype"])
	groups = build_groups(rows, capped, parsed["text"])
	for group in groups:
		if group["key"] not in ("people", "accounts"):
			continue
		for row in group["rows"][:RELATED_ROWS]:
			try:
				row["related"] = related_for(row)
			except Exception:
				row["related"] = []
			# a person's preview line is their account, which the first link now shows
			if (
				row["related"]
				and row["related"][0].get("kind")
				and row["related"][0]["label"] == row["subtitle"]
			):
				row["subtitle"] = ""
	for group in groups:
		group["rows"] = [_public(r) for r in group["rows"]]
	return {"txt": parsed["text"], "groups": groups}
