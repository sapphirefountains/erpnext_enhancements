# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Monday's Item naming digest: who gets it, which rows, and when it stays silent. Bench-free.

``inventory_enhancements.item_naming_digest`` (v1.532.0; Nik, 2026-09-24, TASK-2026-02238)
emails last week's new Items that fail the naming rules to the Purchasing Agent. Three things
here would be wrong without looking wrong:

* **One definition of compliant.** The rows come from ``item_naming_rules.audit`` over the
  WHOLE catalogue, restricted afterwards. Restricting first would pass a new Item named
  exactly like an old one; asserted below with the real rules module, not a stub.
* **Silence is a feature.** No recipients, no new Items, or nothing failing: no email. An
  "all clear" every Monday trains its reader to delete it unread.
* **Tombstones are not new items.** A ``(deleted)`` code is a QuickBooks artefact.

Installs its own ``frappe``, ``email_style`` and ``item_naming`` stubs in ``setUpModule``
(execution time), which is why it has its own CI step. ``item_naming_rules`` is the real one.

Run: python -m unittest erpnext_enhancements.tests.test_item_naming_digest
"""

import ast
import sys
import types
import unittest
from pathlib import Path

from erpnext_enhancements.inventory_enhancements import item_naming_rules as rules

APP = Path(__file__).resolve().parents[1]
HOOKS = APP / "hooks.py"

OLD = [
	{
		"item_code": "806-020",
		"item_name": 'ELBOW, 90, SOC, PVC, 2" SCH80',
		"item_group": "Products",
		"stock_uom": "Unit",
	},
	{
		"item_code": "GMCB-1B-1",
		"item_name": "CIRCUIT BREAKER, SUPPLEMENTARY, GLADIATOR, 1 AMP",
		"item_group": "Electrical",
		"stock_uom": "Nos",
	},
]
NEW = [
	# Passes on its own AND against the corpus.
	{
		"item_code": "2622-015",
		"item_name": 'VALVE, BALL, UTILITY, SOC, PVC, 1-1/2", EPDM',
		"item_group": "Products",
		"stock_uom": "Unit",
	},
	# Clean in isolation; collides with GMCB-1B-1's name only when the whole corpus is audited.
	{
		"item_code": "GMCB-1B-2",
		"item_name": "CIRCUIT BREAKER, SUPPLEMENTARY, GLADIATOR, 1 AMP",
		"item_group": "Electrical",
		"stock_uom": "Nos",
	},
	# A FIX and nothing worse: an approved category, in mixed case.
	{"item_code": "22-1044", "item_name": "Pipe, primer", "item_group": "Products", "stock_uom": "Nos"},
	# A tombstone created in the window: never listed.
	{
		"item_code": "PDT-00099 (deleted)",
		"item_name": "PDT-00099 (deleted)",
		"item_group": "All Item Groups",
		"stock_uom": "Nos",
	},
]
OWNERS = {row["item_code"]: f"owner-{i}@example.com" for i, row in enumerate(NEW)}

STATE = {}
digest = None


def _reset(recipients="parker.bailey@sapphirefountains.com", recent=None, corpus=None, sendmail_raises=False):
	STATE.clear()
	STATE.update(
		recipients=recipients,
		recent=[{"item_code": c, "owner": o} for c, o in OWNERS.items()] if recent is None else recent,
		corpus=OLD + NEW if corpus is None else corpus,
		sent=[],
		errors=[],
		tables=[],
		sendmail_raises=sendmail_raises,
	)


def _install_stubs():
	frappe = types.ModuleType("frappe")
	frappe._ = lambda s: s

	def get_single_value(doctype, field):
		assert (doctype, field) == ("Inventory Scanner Settings", "naming_digest_recipients")
		if isinstance(STATE["recipients"], Exception):
			raise STATE["recipients"]
		return STATE["recipients"]

	def get_all(doctype, filters=None, fields=None, order_by=None, **kwargs):
		assert doctype == "Item"
		assert "creation" in (filters or {}), "the window must be a creation filter"
		return [dict(row) for row in STATE["recent"]]

	def sendmail(**kwargs):
		if STATE["sendmail_raises"]:
			raise RuntimeError("smtp down")
		STATE["sent"].append(kwargs)

	frappe.db = types.SimpleNamespace(get_single_value=get_single_value)
	frappe.get_all = get_all
	frappe.sendmail = sendmail
	frappe.log_error = lambda *a, **k: STATE["errors"].append(a)
	frappe.get_traceback = lambda: "traceback"
	sys.modules["frappe"] = frappe

	utils = types.ModuleType("frappe.utils")
	utils.add_days = lambda d, n: f"{d}{n:+d}d"
	utils.format_date = lambda d=None: str(d)
	utils.get_fullname = lambda user: f"Full {user}"
	utils.get_url_to_form = lambda dt, name: f"https://example.invalid/desk/{dt}/{name}"
	utils.get_url_to_report = lambda name: f"https://example.invalid/desk/query-report/{name}"
	utils.now_datetime = lambda: "2026-10-05 07:00"
	sys.modules["frappe.utils"] = utils
	frappe.utils = utils

	style = types.ModuleType("erpnext_enhancements.email_style")
	style.kpis = lambda cards: f"[kpis {cards}]"
	style.p = lambda text: f"[p {text}]"
	style.note = lambda text: f"[note {text}]"
	style.button = lambda url, label: f"[button {url} {label}]"
	style.wrap = lambda body, **kw: f"[wrap {kw.get('title')} {body}]"

	def table(headers, rows):
		STATE["tables"].append((headers, rows))
		return "[table]"

	style.table = table
	sys.modules["erpnext_enhancements.email_style"] = style
	import erpnext_enhancements

	erpnext_enhancements.email_style = style

	naming = types.ModuleType("erpnext_enhancements.inventory_enhancements.item_naming")
	naming.read_corpus = lambda: ([dict(r) for r in STATE["corpus"]], {"total": len(STATE["corpus"])})
	naming.read_brands = lambda: []
	sys.modules["erpnext_enhancements.inventory_enhancements.item_naming"] = naming
	import erpnext_enhancements.inventory_enhancements as pkg

	pkg.item_naming = naming


def setUpModule():
	global digest
	_install_stubs()
	sys.modules.pop("erpnext_enhancements.inventory_enhancements.item_naming_digest", None)
	from erpnext_enhancements.inventory_enhancements import item_naming_digest as mod

	digest = mod


class ParseRecipientsTest(unittest.TestCase):
	def test_commas_newlines_and_semicolons(self):
		self.assertEqual(
			digest.parse_recipients("a@x.com, b@x.com\nc@x.com;d@x.com\r\n"),
			["a@x.com", "b@x.com", "c@x.com", "d@x.com"],
		)

	def test_blank_and_none_mean_nobody(self):
		for value in (None, "", "  ", "\n,\n"):
			self.assertEqual(digest.parse_recipients(value), [], repr(value))

	def test_duplicates_dropped_case_insensitively_keeping_the_first(self):
		self.assertEqual(digest.parse_recipients("Parker@X.com, parker@x.com"), ["Parker@X.com"])

	def test_junk_is_dropped_rather_than_sinking_the_send(self):
		self.assertEqual(digest.parse_recipients("parker, not an@email, ok@x.com"), ["ok@x.com"])


class SelectionTest(unittest.TestCase):
	def setUp(self):
		self.rows = rules.audit(OLD + NEW, brands=())

	def test_only_the_weeks_failing_live_items_worst_first(self):
		selected = digest.select_rows(self.rows, OWNERS)
		self.assertEqual([r["item_code"] for r in selected], ["GMCB-1B-2", "22-1044"])
		self.assertEqual([r["verdict"] for r in selected], [rules.VERDICT_STOP, rules.VERDICT_FIX])

	def test_the_collision_is_found_only_because_the_whole_corpus_was_audited(self):
		alone = digest.select_rows(rules.audit(NEW, brands=()), OWNERS)
		self.assertNotIn("GMCB-1B-2", [r["item_code"] for r in alone])

	def test_old_failing_items_are_not_this_weeks_business(self):
		rows = rules.audit([*OLD, {"item_code": "OLD-1", "item_name": "junk"}], brands=())
		self.assertEqual(digest.select_rows(rows, OWNERS), [])

	def test_tombstones_are_never_listed(self):
		selected = digest.select_rows(self.rows, OWNERS)
		self.assertNotIn("PDT-00099 (deleted)", [r["item_code"] for r in selected])

	def test_week_counts_use_the_rules_summary(self):
		self.assertEqual(digest.week_counts(self.rows, OWNERS), {"new": 3, "passing": 1})


class FormattingTest(unittest.TestCase):
	def test_what_to_fix_lists_stops_then_fixes_and_leaves_out_notes(self):
		findings = [
			{"severity": rules.NOTE, "message": "a note"},
			{"severity": rules.FIX, "message": "fix me"},
			{"severity": rules.STOP, "message": "stop me"},
		]
		self.assertEqual(digest.what_to_fix(findings), "stop me fix me")
		self.assertEqual(digest.what_to_fix(None), "")

	def test_table_rows_link_the_item_and_name_its_creator(self):
		rows = digest.select_rows(rules.audit(OLD + NEW, brands=()), OWNERS)
		cells = digest.table_rows(rows, OWNERS, url_for=lambda c: f"u/{c}", name_for=lambda u: f"N {u}")
		self.assertEqual(cells[0][0], ("u/GMCB-1B-2", "GMCB-1B-2"))
		self.assertEqual(cells[0][1], "CIRCUIT BREAKER, SUPPLEMENTARY, GLADIATOR, 1 AMP")
		self.assertEqual(cells[0][2], "N owner-1@example.com")
		self.assertIn("GMCB-1B-1", cells[0][3])
		self.assertTrue(all(len(row) == 4 for row in cells), "four columns is the most a phone takes")


class SendTest(unittest.TestCase):
	def test_sends_one_email_to_the_configured_recipients(self):
		_reset(recipients="parker.bailey@sapphirefountains.com\nnik@example.com")
		digest.send_weekly_digest()
		self.assertEqual(len(STATE["sent"]), 1)
		mail = STATE["sent"][0]
		self.assertEqual(mail["recipients"], ["parker.bailey@sapphirefountains.com", "nik@example.com"])
		self.assertIn("2 new Item(s) to fix", mail["subject"])
		self.assertTrue(mail["message"].startswith("[wrap "), "the body must go through email_style.wrap")
		headers, rows = STATE["tables"][0]
		self.assertEqual(len(headers), 4)
		self.assertEqual([row[0][1] for row in rows], ["GMCB-1B-2", "22-1044"])
		self.assertEqual(rows[0][0][0], "https://example.invalid/desk/Item/GMCB-1B-2")

	def test_no_recipients_sends_nothing(self):
		for value in (None, "", "not-an-address"):
			_reset(recipients=value)
			digest.send_weekly_digest()
			self.assertEqual(STATE["sent"], [], repr(value))

	def test_a_missing_field_before_migrate_is_a_quiet_no_op(self):
		_reset(recipients=RuntimeError("Field naming_digest_recipients does not exist"))
		digest.send_weekly_digest()
		self.assertEqual((STATE["sent"], STATE["errors"]), ([], []))

	def test_no_new_items_sends_nothing(self):
		_reset(recent=[])
		digest.send_weekly_digest()
		self.assertEqual(STATE["sent"], [])

	def test_nothing_failing_sends_nothing(self):
		_reset(recent=[{"item_code": "2622-015", "owner": "a@x.com"}])
		digest.send_weekly_digest()
		self.assertEqual(STATE["sent"], [])

	def test_a_send_failure_is_logged_not_raised(self):
		_reset(sendmail_raises=True)
		digest.send_weekly_digest()
		self.assertEqual(len(STATE["errors"]), 1)

	def test_a_long_week_is_capped_and_says_so(self):
		many = [
			{
				"item_code": f"NEW-{i:03d}",
				"item_name": f"thing {i}",
				"item_group": "Products",
				"stock_uom": "Nos",
			}
			for i in range(digest.DIGEST_ROW_LIMIT + 5)
		]
		_reset(recent=[{"item_code": r["item_code"], "owner": "a@x.com"} for r in many], corpus=OLD + many)
		digest.send_weekly_digest()
		self.assertEqual(len(STATE["tables"][0][1]), digest.DIGEST_ROW_LIMIT)
		self.assertIn(
			f"first {digest.DIGEST_ROW_LIMIT} of {digest.DIGEST_ROW_LIMIT + 5}", STATE["sent"][0]["message"]
		)


class WiringTest(unittest.TestCase):
	def test_scheduled_monday_seven(self):
		tree = ast.parse(HOOKS.read_text(encoding="utf-8"))
		sched = next(
			node.value
			for node in tree.body
			if isinstance(node, ast.Assign)
			and any(getattr(t, "id", None) == "scheduler_events" for t in node.targets)
		)
		cron = next(
			v for k, v in zip(sched.keys, sched.values, strict=False) if getattr(k, "value", None) == "cron"
		)
		entries = {k.value: ast.literal_eval(v) for k, v in zip(cron.keys, cron.values, strict=False)}
		self.assertIn(
			"erpnext_enhancements.inventory_enhancements.item_naming_digest.send_weekly_digest",
			entries.get("0 7 * * 1", []),
		)


if __name__ == "__main__":
	unittest.main()
