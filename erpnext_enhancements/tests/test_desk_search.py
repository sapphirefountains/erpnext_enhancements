"""The Desk search box: records first, grouped, with a preview (v1.795.0; Nik, 2026-11-04).

Bench-free: runs api/desk_search.py against a fake frappe, and reads files for the wiring. The
browser side is executed, not grepped, in ``scripts/test_desk_search.js``; this suite holds the
endpoint (what the typed text means, the ranking ladder, the SQL it builds and binds, grouping,
permissions, related links, one failure never stops the rest) and the seams the harness cannot see
(the bundle imports the script, the old flat-150 patch is gone, the stylesheet has the classes the
script sets, CI runs both).

The names in the fixtures are made up.

Run: python -m unittest erpnext_enhancements.tests.test_desk_search
"""

import ast
import re
import shutil
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP = REPO_ROOT / "erpnext_enhancements"
API = APP / "api" / "desk_search.py"
API_README = APP / "api" / "README.md"
MODULE = APP / "public" / "js" / "global_enhancements" / "desk_search.js"
OLD = APP / "public" / "js" / "erpnext_enhancements.js"
BUNDLE = APP / "public" / "js" / "erpnext_enhancements.bundle.js"
CSS = APP / "public" / "css" / "desk_enhancements.bundle.css"
HARNESS = REPO_ROOT / "scripts" / "test_desk_search.js"
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def read(path):
	return path.read_text(encoding="utf-8")


def code(path):
	"""The script with comments stripped: an assertion must not be satisfied by its own prose."""
	src = re.sub(r"/\*[\s\S]*?\*/", "", read(path))
	return "\n".join(line for line in src.splitlines() if not line.strip().startswith("//"))


# ------------------------------------------------------------------ a fake frappe


class _PermissionError(Exception):
	pass


class _Cache:
	def __init__(self):
		self.values = {}

	def get_value(self, key):
		return self.values.get(key)

	def set_value(self, key, value, expires_in_sec=None):
		self.values[key] = value


class _Db:
	"""Answers each doctype's candidate query with its seeded rows, in seeded order."""

	def __init__(self, rows=None, columns=None, children=None, fail=()):
		self.rows = rows or {}
		self.columns = columns or {}
		self.children = children or {}
		self.fail = set(fail)
		self.queries = []

	def get_table_columns(self, doctype):
		if doctype in self.columns:
			return list(self.columns[doctype])
		cols = {"name", "modified"}
		for row in self.rows.get(doctype, []):
			cols.update(row)
		return sorted(cols)

	def table_exists(self, doctype):
		return doctype in ("Contact Phone", "Contact Email")

	def sql(self, query, values=None, as_dict=False):
		self.queries.append((query, dict(values or {})))  # a copy: the paging loop reuses the dict
		table = re.search(r"from `tab([^`]+)`", query).group(1)
		if table in self.fail:
			raise RuntimeError("boom")
		if table in ("Contact Phone", "Contact Email"):
			names = set(values["names"])
			return [r for r in self.children.get(table, []) if r["parent"] in names]
		rows = self.rows.get(table, [])
		offset = (values or {}).get("offset", 0)
		limit = (values or {}).get("limit", len(rows))
		return [dict(r) for r in rows[offset : offset + limit]]


class _Frappe:
	PermissionError = _PermissionError

	def __init__(self, db, user="pat@x", allowed=None, readable=None, links=None, counts=None):
		self.db = db
		self.session = type("S", (), {"user": user})()
		self.cache = _Cache()
		self.allowed = allowed  # doctypes with read; None = all
		self.readable = readable or {}  # doctype -> names get_list returns; missing = all
		self.links = links or {}
		self.counts = counts or {}
		self.errors = []
		self.deferred = []
		self.get_list_calls = []

	def has_permission(self, doctype, ptype="read"):
		return self.allowed is None or doctype in self.allowed

	def get_list(self, doctype, filters=None, pluck=None, limit_page_length=None):
		self.get_list_calls.append((doctype, filters, limit_page_length))
		if "name" in (filters or {}):
			names = filters["name"][1]
			keep = self.readable.get(doctype)
			return [n for n in names if keep is None or n in keep]
		key = (doctype, tuple(sorted((filters or {}).items())))
		return [f"{doctype}-{i}" for i in range(self.counts.get(key, 0))]

	def get_all(self, doctype, filters=None, pluck=None, limit=None):
		return list(self.links.get(filters.get("parent"), []))

	def log_error(self, title=None, message=None, defer_insert=False):
		self.errors.append(title)
		self.deferred.append(defer_insert)

	@staticmethod
	def get_traceback():
		return "Traceback"

	@staticmethod
	def _(s):
		return s

	@staticmethod
	def whitelist(methods=None):
		def deco(fn):
			fn.whitelisted, fn.methods = True, methods
			return fn

		return deco


def _module(fake=None):
	tree = ast.parse(read(API))
	body = [n for n in tree.body if not isinstance(n, (ast.Import, ast.ImportFrom))]
	fake = fake or _Frappe(_Db())
	ns = {
		"re": re,
		"frappe": fake,
		"_": fake._,
		"cstr": lambda v: "" if v is None else str(v),
		"flt": lambda v: float(v or 0),
		"fmt_money": lambda v, currency=None: f"${v:,.2f}",
		"formatdate": lambda d: str(d),
		"rate_limit": lambda **kw: (lambda fn: fn),
	}
	exec(compile(ast.Module(body=body, type_ignores=[]), str(API), "exec"), ns)
	return ns


NS = _module()

CONTACTS = [
	{
		"name": "Dana Whitfield",
		"modified": "2026-07-01",
		"full_name": "Dana Whitfield",
		"custom_account": "Dana Whitfield Residence",
		"custom_phone_number": "+18015550142",
		"custom_email": "dana@example.com",
	},
	{
		"name": "Danae Corp",
		"modified": "2026-10-01",
		"full_name": "Danae Corp",
		"custom_account": "Tuscan Gardens",
		"custom_phone_number": "",
		"custom_email": "",
	},
]
CUSTOMERS = [
	{
		"name": "Dana Whitfield Residence",
		"modified": "2026-06-01",
		"customer_name": "Dana Whitfield Residence",
		"disabled": 0,
	}
]
PROJECTS = [
	{
		"name": "PRJ-00433",
		"modified": "2026-01-01",
		"project_name": "Dana Whitfield - Residence",
		"status": "Completed",
		"customer": "Dana Whitfield Residence",
	},
	{
		"name": "PRJ-00744",
		"modified": "2026-09-01",
		"project_name": "Dana Whitfield - Maintenance Contract",
		"status": "Active",
		"customer": "Dana Whitfield Residence",
	},
	{
		"name": "PRJ-00707",
		"modified": "2026-10-09",
		"project_name": "Dana Whitfield- Fountain 4-Tier",
		"status": "Completed",
		"customer": "Dana Whitfield Residence",
	},
	{
		"name": "PRJ-00474",
		"modified": "2025-12-01",
		"project_name": "Dana Whitfield - new fountain",
		"status": "Completed",
		"customer": "Dana Whitfield Residence",
	},
	{
		"name": "PRJ-00440",
		"modified": "2025-11-01",
		"project_name": "Dana Whitfield - Residence",
		"status": "Canceled",
		"customer": "Dana Whitfield Residence",
	},
]


class TestParse(unittest.TestCase):
	def test_words(self):
		p = NS["parse_query"]("  Dana   Whit ")
		self.assertEqual(
			(p["text"], p["words"], p["phone"], p["number"]), ("Dana Whit", ["Dana", "Whit"], False, None)
		)

	def test_phone_numbers_are_digits_and_lose_the_country_code(self):
		for txt in ("801-555-0142", "+1 801 555 0142", "(801) 555-0142"):
			p = NS["parse_query"](txt)
			self.assertTrue(p["phone"], txt)
			self.assertEqual(p["digits"], "8015550142", txt)
		self.assertEqual(NS["parse_query"]("555-0142")["digits"], "5550142")
		self.assertFalse(NS["parse_query"]("707")["phone"], "three digits are a document number, not a phone")

	def test_document_numbers(self):
		cases = {
			"707": (None, "707"),
			"00707": (None, "707"),
			"PRJ-707": ("PRJ", "707"),
			"prj 707": ("PRJ", "707"),
			"p707": ("PRJ", "707"),
			"opp 58": ("OPP", "58"),
			"SO 12": ("ORD", "12"),
			"inv 149": ("SINV", "149"),
			"quote 17": ("QTN", "17"),
			"contract 3": ("MNT-CON", "3"),
			"TASK-12": ("TASK", "12"),
		}
		for txt, (prefix, number) in cases.items():
			p = NS["parse_query"](txt)
			self.assertEqual((p["prefix"], p["number"]), (prefix, number), txt)
		for txt in ("0", "555-0142", "Dana", "1234567"):
			self.assertIsNone(NS["parse_query"](txt)["number"], txt)

	def test_long_text_is_cut(self):
		self.assertEqual(len(NS["parse_query"]("x" * 500)["text"]), NS["MAX_CHARS"])


class TestScore(unittest.TestCase):
	def score(self, txt, title, **kw):
		return NS["score_row"](NS["parse_query"](txt), title, **kw)

	def test_the_first_name_case(self):
		"""A whole first name beats a longer name that merely starts with it."""
		self.assertGreater(self.score("Dana", "Dana Whitfield"), self.score("Dana", "Danae Corp"))

	def test_the_ladder(self):
		ladder = [
			self.score("707", "anything", number_hit=True),
			self.score("dana whitfield", "Dana Whitfield"),  # exact
			self.score("dana", "Danae Corp"),  # starts with
			self.score("whit", "Dana Whitfield"),  # a word starts with it
			self.score("itfi", "Dana Whitfield"),  # contains
			self.score("dana field", "Dana Whitfield"),  # every word in it, one mid-word
			self.score("5550142", "Pat", phone_hit=True),
			self.score("tuscan", "Danae Corp", others=["Tuscan Gardens"]),  # another field
			self.score("dana@", "Pat"),  # only somewhere unseen (an email)
		]
		self.assertEqual(ladder, sorted(ladder, reverse=True), ladder)
		self.assertEqual(len(set(ladder)), len(ladder), ladder)

	def test_open_work_breaks_ties(self):
		self.assertEqual(
			self.score("dana", "Dana Whitfield", is_open=True), self.score("dana", "Dana Whitfield") + 5
		)

	def test_the_whole_word_bonus_needs_a_title_match(self):
		self.assertEqual(self.score("tuscan", "Danae Corp", others=["Tuscan Gardens"]), 45)


class TestQuery(unittest.TestCase):
	def spec(self, doctype, columns=None):
		spec = next(s for s in NS["SPECS"] if s["doctype"] == doctype)
		every = [c for k in ("title", "match", "email", "phone", "extra") for c in spec.get(k, ())]
		return NS["_available"](spec, columns if columns is not None else [*every, "status", "disabled"])

	def build(self, doctype, txt, columns=None, children=("Contact Phone", "Contact Email")):
		return NS["build_query"](self.spec(doctype, columns), NS["parse_query"](txt), set(children))

	def test_the_text_is_bound_never_inlined(self):
		sql, values = self.build("Contact", "O'Brien 50%_x")
		self.assertNotIn("O'Brien", sql)
		self.assertNotIn("50", sql)
		self.assertEqual(values["w0"], "%O'Brien%")
		self.assertEqual(values["w1"], "%50\\%\\_x%", "LIKE wildcards in the text are escaped")
		self.assertIn("(`full_name` like %(w0)s or", sql)
		self.assertIn(") and (", sql, "every word must match")
		self.assertEqual(values["limit"], NS["CANDIDATES"])

	def test_identifiers_come_from_the_spec_and_the_table(self):
		sql, _values = self.build("Contact", "dana", columns=["full_name", "first_name"])
		self.assertIn("`full_name` like", sql)
		self.assertNotIn("custom_email", sql, "a column the table lacks drops out")
		self.assertIn("from `tabContact`", sql)

	def test_two_letters_match_the_start_of_a_word(self):
		sql, values = self.build("Customer", "da")
		self.assertEqual((values["w0a"], values["w0b"]), ("da%", "% da%"))
		self.assertNotIn("%da%", values.values())

	def test_child_rows(self):
		sql, values = self.build("Contact", "dana@example")
		self.assertIn("select parent from `tabContact Email` where parenttype = %(ptype)s", sql)
		self.assertEqual(values["ptype"], "Contact")
		sql, _values = self.build("Contact", "dana@example", children=())
		self.assertNotIn("tabContact Email", sql, "no child table, no subquery")

	def test_phone_by_digits(self):
		sql, values = self.build("Contact", "801-555-0142")
		self.assertEqual(values["digits"], "%8015550142%")
		self.assertIn("regexp_replace(ifnull(`custom_phone_number`, ''), '[^0-9]', '') like %(digits)s", sql)
		self.assertIn("from `tabContact Phone` where parenttype = %(ptype)s", sql)
		sql, values = self.build("Project", "801-555-0142")
		self.assertNotIn("digits", values, "projects are not searched by phone")
		sql, values = self.build("Contact", "dana")
		self.assertNotIn("digits", values)

	def test_numbers_only_in_numbered_series(self):
		sql, values = self.build("Project", "prj 707")
		self.assertEqual(values["num_re"], "-0*707$")
		self.assertEqual(values["num_prefix"], "%PRJ-%")
		self.assertIn("(name regexp %(num_re)s and name like %(num_prefix)s)", sql)
		self.assertIn("as _number_hit", sql)
		self.assertTrue(sql.split(" order by ")[1].startswith("_number_hit desc"))
		_sql, values = self.build("Contact", "707")
		self.assertNotIn("num_re", values)

	def test_canceled_documents_are_left_out(self):
		sql, _values = self.build("Sales Invoice", "dana")
		self.assertIn("and docstatus < 2", sql)
		sql, _values = self.build("Project", "dana")
		self.assertNotIn("docstatus", sql)
		sql, _values = self.build("Sapphire Maintenance Record", "dana")
		self.assertIn(
			"and docstatus < 2", sql, "a canceled maintenance visit is docstatus 2 (is_submittable)"
		)

	def test_best_title_first_in_the_candidates(self):
		sql, values = self.build("Project", "dana whit")
		self.assertIn("case when `project_name` like %(phrase_start)s then 0", sql)
		self.assertEqual((values["phrase_start"], values["phrase_word"]), ("dana whit%", "% dana whit%"))

	def test_a_missing_open_column_drops_the_rule(self):
		spec = self.spec("Project", columns=["project_name"])
		self.assertNotIn("open", spec)
		self.assertEqual(spec["match"], ("project_name", "name"), "name is on every table")
		spec = NS["_available"](NS["SPECS"][0], [])
		self.assertEqual(spec["match"], ("name",), "nothing left to match: the name")


class TestRowsAndGroups(unittest.TestCase):
	def rows(self, doctype, data, txt="Dana"):
		spec = NS["_available"](
			next(s for s in NS["SPECS"] if s["doctype"] == doctype), list({k for r in data for k in r})
		)
		parsed = NS["parse_query"](txt)
		return [NS["make_row"](spec, r, parsed) for r in data]

	def test_preview_lines(self):
		(row,) = self.rows("Project", PROJECTS[1:2])
		self.assertEqual(row["subtitle"], "PRJ-00744 · Dana Whitfield Residence · Active")
		self.assertEqual(row["route"], ["Form", "Project", "PRJ-00744"])
		contact = self.rows("Contact", CONTACTS[:1])[0]
		self.assertEqual(
			(contact["subtitle"], contact["phone"], contact["email"]),
			("Dana Whitfield Residence", "+18015550142", "dana@example.com"),
		)
		(inv,) = self.rows(
			"Sales Invoice",
			[
				{
					"name": "ACC-SINV-2026-00149",
					"customer_name": "Dana Whitfield Residence",
					"status": "Overdue",
					"posting_date": "2026-10-01",
					"grand_total": 1200,
					"outstanding_amount": 400,
					"currency": "USD",
				}
			],
		)
		self.assertEqual(
			inv["subtitle"], "ACC-SINV-2026-00149 · Overdue · 2026-10-01 · $1,200.00 · $400.00 due"
		)
		(opp,) = self.rows(
			"Opportunity",
			[
				{
					"name": "CRM-OPP-2026-00058",
					"custom_opportunity_name": "Dana Whitfield - Fountain",
					"title": "Dana Whitfield Residence",
					"customer_name": "Dana Whitfield Residence",
					"status": "Closed Won",
				}
			],
		)
		self.assertEqual(opp["title"], "Dana Whitfield - Fountain", "custom_opportunity_name, not title")
		self.assertEqual(opp["subtitle"], "CRM-OPP-2026-00058 · Dana Whitfield Residence · Closed Won")

	def test_open_rules_follow_prod_statuses(self):
		scores = {r["name"]: r["score"] for r in self.rows("Project", PROJECTS)}
		self.assertGreater(scores["PRJ-00744"], scores["PRJ-00707"], "Active is open on prod")

	def test_groups(self):
		rows = (
			self.rows("Contact", CONTACTS) + self.rows("Customer", CUSTOMERS) + self.rows("Project", PROJECTS)
		)
		groups = NS["build_groups"](rows)
		self.assertEqual([g["key"] for g in groups], ["people", "accounts", "projects"])
		people, accounts, projects = groups
		self.assertEqual([r["name"] for r in people["rows"]], ["Dana Whitfield", "Danae Corp"])
		self.assertEqual(len(projects["rows"]), NS["GROUP_ROWS"])
		self.assertEqual(projects["rows"][0]["name"], "PRJ-00744", "the open one first, then the newest")
		self.assertEqual(projects["total"], 5)
		self.assertFalse(projects["more"])
		self.assertEqual(projects["see_all"]["route"], ["List", "Project"])
		self.assertEqual(
			sorted(projects["see_all"]["route_options"]["name"][1]), sorted(p["name"] for p in PROJECTS)
		)
		self.assertIsNone(people["see_all"], "nothing hidden, no see-all")

	def test_a_capped_group_sees_all_by_the_text(self):
		"""Review of PR #1425: "See all 25+" opened a list of just the 25 names read."""
		rows = self.rows("Project", PROJECTS)
		(projects,) = NS["build_groups"](rows, capped=["Project"], text="Dana")
		self.assertTrue(projects["more"])
		self.assertEqual(
			projects["see_all"],
			{"route": ["List", "Project"], "route_options": {"project_name": ["like", "%Dana%"]}},
		)

	def test_the_first_group_gets_one_more_row(self):
		rows = self.rows("Project", PROJECTS)
		(group,) = NS["build_groups"](rows)
		self.assertEqual(len(group["rows"]), NS["FIRST_GROUP_ROWS"])

	def test_a_number_puts_its_group_first(self):
		rows = self.rows("Contact", CONTACTS, txt="707") + self.rows(
			"Project", [dict(PROJECTS[2], _number_hit=1)], txt="707"
		)
		groups = NS["build_groups"](rows)
		self.assertEqual(groups[0]["key"], "projects")

	def test_the_open_bonus_never_reorders_groups(self):
		"""Measured on prod for a customer's first name: draft invoices titled with the account name (open, 103) sat
		above her closed opportunities (98) while groups were ordered by score."""
		rows = self.rows(
			"Opportunity",
			[
				{
					"name": "CRM-OPP-2026-00138",
					"custom_opportunity_name": "Dana Whitfield - Maintenance",
					"status": "Closed Won",
				}
			],
		)
		rows += self.rows(
			"Sales Invoice",
			[{"name": "ACC-SINV-2026-01778", "customer_name": "Dana Whitfield Residence", "status": "Draft"}],
		)
		self.assertEqual([g["key"] for g in NS["build_groups"](rows)], ["opportunities", "sales"])
		self.assertGreater(
			rows[1]["score"], rows[0]["score"], "the invoice still outscores the deal as a row"
		)

	def test_a_mixed_group_has_no_list_route(self):
		rows = self.rows(
			"Quotation",
			[
				{
					"name": f"SAL-QTN-2026-0001{i}",
					"customer_name": "Dana Whitfield Residence",
					"status": "Draft",
				}
				for i in range(3)
			],
		)
		rows += self.rows(
			"Sales Invoice",
			[
				{
					"name": f"ACC-SINV-2026-0001{i}",
					"customer_name": "Dana Whitfield Residence",
					"status": "Paid",
				}
				for i in range(3)
			],
		)
		(group,) = NS["build_groups"](rows)
		self.assertEqual(group["total"], 6)
		self.assertIsNone(group["see_all"], "the client opens the full results for a mixed group")


class TestEndpoint(unittest.TestCase):
	def run_search(self, txt="Dana", **kw):
		db = kw.pop("db", None) or _Db(
			rows={"Contact": CONTACTS, "Customer": CUSTOMERS, "Project": PROJECTS},
			children={"Contact Phone": [{"parent": "Danae Corp", "v": "801-555-0199"}], "Contact Email": []},
		)
		fake = _Frappe(db, **kw)
		return _module(fake)["search"](txt), fake, db

	def test_it_is_a_read_only_post(self):
		"""POST so the names, phones and emails typed never reach a logged request line; it writes
		nothing all the same. Rate-limited per IP."""
		src = read(API)
		self.assertIn(
			'@frappe.whitelist(methods=["POST"])\n@rate_limit(limit=RATE_LIMIT, seconds=60)\ndef search(txt=None):',
			src,
		)
		self.assertIn("from frappe.rate_limiter import rate_limit", src)
		for forbidden in (
			"set_value(",
			".save(",
			".insert(",
			"db.delete",
			"delete_doc",
			"frappe.db.commit",
			"ignore_permissions",
		):
			self.assertNotIn(forbidden, src.replace("cache.set_value(", ""), forbidden)
		self.assertFalse([line for line in src.splitlines() if line.startswith("    ")], "tab-indented")
		self.assertIn("desk_search.py", read(API_README))

	def test_the_answer(self):
		answer, fake, _db = self.run_search()
		self.assertEqual(answer["txt"], "Dana")
		self.assertEqual([g["key"] for g in answer["groups"]], ["people", "accounts", "projects"])
		top = answer["groups"][0]["rows"][0]
		self.assertEqual((top["doctype"], top["name"], top["type"]), ("Contact", "Dana Whitfield", "Contact"))
		self.assertFalse(
			[k for k in top if k.startswith("_") or k == "modified"], "internal keys stay on the server"
		)
		second = answer["groups"][0]["rows"][1]
		self.assertEqual(second["phone"], "801-555-0199", "a Contact's phone child row is its preview phone")
		self.assertEqual(fake.errors, [])

	def test_short_text_asks_nothing(self):
		answer, _fake, db = self.run_search("d")
		self.assertEqual(answer, {"txt": "d", "groups": []})
		self.assertEqual(db.queries, [])

	def test_guest_is_refused(self):
		with self.assertRaises(_PermissionError):
			self.run_search(user="Guest")

	def test_no_read_permission_no_query(self):
		answer, _fake, db = self.run_search(allowed={"Project"})
		self.assertEqual([g["key"] for g in answer["groups"]], ["projects"])
		self.assertEqual({re.search(r"from `tab([^`]+)`", q).group(1) for q, _v in db.queries}, {"Project"})

	def test_rows_the_person_may_not_read_drop_out(self):
		answer, fake, _db = self.run_search(readable={"Project": {"PRJ-00744"}})
		projects = next(g for g in answer["groups"] if g["key"] == "projects")
		self.assertEqual([r["name"] for r in projects["rows"]], ["PRJ-00744"])
		self.assertEqual(projects["total"], 1)
		(call,) = [c for c in fake.get_list_calls if c[0] == "Project" and "name" in (c[1] or {})]
		self.assertEqual(call[2], 0, "the permission read is not paged")

	def test_a_restricted_reader_still_finds_their_rows_past_the_cap(self):
		"""Review of PR #1425: the cap applied before the permission filter, so someone restricted by
		User Permissions saw nothing when the 25 best matches were others'."""
		many = [
			{
				"name": f"PRJ-{i:05d}",
				"modified": f"2026-10-{1 + i % 28:02d}",
				"project_name": f"Dana job {i}",
				"status": "Active",
			}
			for i in range(60)
		]
		db = _Db(rows={"Project": many})
		fake = _Frappe(db, allowed={"Project"}, readable={"Project": {"PRJ-00050", "PRJ-00055"}})
		answer = _module(fake)["search"]("Dana")
		(projects,) = answer["groups"]
		self.assertEqual(sorted(r["name"] for r in projects["rows"]), ["PRJ-00050", "PRJ-00055"])
		self.assertEqual(
			[v["offset"] for _q, v in db.queries], [0, 25, 50], "three pages, the last one short"
		)
		self.assertFalse(projects["more"], "the last page was short: nothing more to see")

	def test_an_unrestricted_reader_reads_one_page(self):
		many = [
			{"name": f"PRJ-{i:05d}", "modified": "2026-10-01", "project_name": f"Dana job {i}"}
			for i in range(30)
		]
		db = _Db(rows={"Project": many})
		answer = _module(_Frappe(db, allowed={"Project"}))["search"]("Dana")
		(projects,) = answer["groups"]
		self.assertEqual(len(db.queries), 1, "one query when the first page is all readable")
		self.assertEqual(projects["total"], NS["CANDIDATES"])
		self.assertTrue(projects["more"])
		self.assertEqual(projects["see_all"]["route_options"], {"project_name": ["like", "%Dana%"]})

	def test_one_failure_never_stops_the_rest_and_is_logged_once(self):
		db = _Db(rows={"Contact": CONTACTS, "Project": PROJECTS}, fail={"Project"})
		fake = _Frappe(db)
		ns = _module(fake)
		answer = ns["search"]("Dana")
		self.assertEqual([g["key"] for g in answer["groups"]], ["people"])
		ns["search"]("Dana W")
		self.assertEqual(
			fake.errors, ["Desk search: Project not searched"], "once per doctype per hour, not per keystroke"
		)
		self.assertEqual(
			fake.deferred, [True], "deferred, so a rolled-back request cannot take the row with it"
		)

	def test_related_links(self):
		counts = {
			("Project", (("customer", "Dana Whitfield Residence"),)): 5,
			("Opportunity", (("party_name", "Dana Whitfield Residence"),)): 4,
		}
		answer, _fake, _db = self.run_search(counts=counts, links={"Danae Corp": []})
		people = answer["groups"][0]
		top = people["rows"][0]
		self.assertEqual(
			top["related"],
			[
				{
					"label": "Dana Whitfield Residence",
					"kind": "Account",
					"route": ["Form", "Customer", "Dana Whitfield Residence"],
				},
				{
					"label": "5 projects",
					"route": ["List", "Project"],
					"route_options": {"customer": "Dana Whitfield Residence"},
				},
				{
					"label": "4 opportunities",
					"route": ["List", "Opportunity"],
					"route_options": {"party_name": "Dana Whitfield Residence"},
				},
			],
		)
		self.assertEqual(
			people["rows"][1]["related"],
			[{"label": "Tuscan Gardens", "kind": "Account", "route": ["Form", "Customer", "Tuscan Gardens"]}],
			"a zero count is no link",
		)
		self.assertEqual(
			(top["subtitle"], people["rows"][1]["subtitle"]),
			("", ""),
			"the account link replaces the account line",
		)
		account = answer["groups"][1]["rows"][0]
		self.assertEqual([r["label"] for r in account["related"]], ["5 projects", "4 opportunities"])
		self.assertNotIn("related", answer["groups"][2]["rows"][0], "projects get no related links")

	def test_a_contact_without_the_custom_account_uses_its_dynamic_link(self):
		row = {
			"_spec": {"related": "contact"},
			"_row": {"name": "Pat Doe", "custom_account": ""},
			"name": "Pat Doe",
		}
		fake = _Frappe(_Db(), links={"Pat Doe": ["Doe Ranch"]})
		ns = _module(fake)
		self.assertEqual(ns["related_for"](row)[0]["route"], ["Form", "Customer", "Doe Ranch"])

	def test_lead_links_its_opportunities(self):
		counts = {("Opportunity", (("opportunity_from", "Lead"), ("party_name", "Lake Days"))): 1}
		ns = _module(_Frappe(_Db(), counts=counts))
		row = {"_spec": {"related": "lead"}, "_row": {}, "name": "Lake Days"}
		self.assertEqual(
			ns["related_for"](row),
			[
				{
					"label": "1 opportunity",
					"route": ["List", "Opportunity"],
					"route_options": {"opportunity_from": "Lead", "party_name": "Lake Days"},
				}
			],
		)

	def test_every_searched_doctype_has_a_group_and_a_type(self):
		groups = {k for k, _l in NS["GROUPS"]}
		for spec in NS["SPECS"]:
			self.assertIn(spec["group"], groups, spec["doctype"])
			self.assertTrue(spec["title"] and spec["match"], spec["doctype"])
		self.assertEqual(
			{s["doctype"] for s in NS["SPECS"]},
			{
				"Contact",
				"Lead",
				"Customer",
				"Project",
				"Opportunity",
				"Quotation",
				"Sales Order",
				"Sales Invoice",
				"Serial No",
				"Sapphire Maintenance Contract",
				"Sapphire Maintenance Record",
				"Supplier",
				"Item",
			},
			"Nik's picks, 2026-11-04 (Employees were left out)",
		)


class TestWiring(unittest.TestCase):
	def test_the_script_is_in_the_global_bundle(self):
		imports = re.findall(r'^import "([^"]+)";', read(BUNDLE), flags=re.M)
		self.assertIn("./global_enhancements/desk_search.js", imports)

	def test_the_flat_150_patch_is_gone(self):
		old = code(OLD)
		self.assertNotIn("search_global_docs", old)
		self.assertNotIn("global_search_debounced", old)
		self.assertNotIn("make_global_search", old)

	def test_it_asks_the_endpoint_quietly(self):
		src = code(MODULE)
		self.assertIn('"erpnext_enhancements.api.desk_search.search"', src)
		self.assertIn('root.fetch("/api/method/" + METHOD, {', src)
		self.assertNotIn("frappe.call(", src, "frappe.call pops a dialog on a server error")
		self.assertIn('method: "POST"', src)
		self.assertIn('"X-Frappe-CSRF-Token": fr.csrf_token', src)
		self.assertNotIn("?txt=", src, "nothing typed goes in a URL")

	def test_it_wraps_the_box_frappe_builds(self):
		src = code(MODULE)
		self.assertIn("proto.setup_event_listeners = function", src)
		self.assertIn('Object.defineProperty(aw, "list"', src)
		self.assertIn("aw.item = function", src)
		self.assertIn('ul.addEventListener(\n\t\t\t\t"click"', src)
		self.assertIn("true\n\t\t\t);", src, "capture phase, ahead of Awesomplete's own listener")

	def test_the_stylesheet_has_the_classes_the_script_sets(self):
		css = read(CSS)
		src = code(MODULE)
		for cls in (
			"ee-ds-list",
			"ee-ds-head",
			"ee-ds-headed",
			"ee-ds-seeall",
			"ee-ds-line",
			"ee-ds-main",
			"ee-ds-top",
			"ee-ds-title",
			"ee-ds-type",
			"ee-ds-sub",
			"ee-ds-actions",
			"ee-ds-call",
			"ee-ds-mail",
			"ee-ds-related",
			"ee-ds-rel",
			"ee-ds-rel-kind",
		):
			self.assertIn(cls, src, cls)
			self.assertIn("." + cls, css, cls)
		self.assertIn("@media (max-width: 767px)", css[css.index("DESK SEARCH") :])


class TestHarness(unittest.TestCase):
	def test_ci_runs_both(self):
		ci = read(CI)
		self.assertIn("node scripts/test_desk_search.js", ci)
		self.assertIn("python -m unittest erpnext_enhancements.tests.test_desk_search -v", ci)

	@unittest.skipUnless(shutil.which("node"), "node is not installed")
	def test_the_harness_passes(self):
		result = subprocess.run(
			["node", str(HARNESS)], cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=120
		)
		self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
		self.assertIn("all checks passed", result.stdout)


if __name__ == "__main__":
	unittest.main()
