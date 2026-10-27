# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Dock per job (v1.734.0): the shipped dock file, the profiles, and the layers they write. Bench-free.

frappe 16.50 draws the Dock from one app's layers (its file, the site's, each person's) and keeps
no per-role layer, so ``setup/dock_profiles.py`` writes the site's and each person's from a profile.
Two ways for that to go quietly wrong are pinned here. A profile naming an entry the rail does not
hold is skipped without a word, so a typo in an entry would just be missing from somebody's dock.
And a layer written whole replaces the rail, so writing over one a person arranged would throw
their arrangement away (Nik, 2026-10-23: "Keep their own").

Since v1.750.0 the field technicians' profile also decides their Desk home grid
(``trim_home_grid``), and the rail and the grid must list the same eight things. Pinned here too,
along with the two desk pages that carry the rail to the kiosk and Stock Scan.

Installs its own ``frappe`` stub in ``setUpModule``, which is why it has its own CI step.

Run: python -m unittest erpnext_enhancements.tests.test_dock_profiles
"""

import hashlib
import json
import re
import sys
import types
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
DOCK_FILE = APP / "dock" / "erpnext_enhancements" / "erpnext_enhancements.json"
HOOKS = APP / "hooks.py"
BOOT = APP / "boot.py"
PATCHES_TXT = APP / "patches.txt"

# ERPNext v16.50.0's own dock (erpnext/dock/erpnext/erpnext.json), as prod held it on 2026-10-23.
ERPNEXT_DOCK = [
	("Sidebar", name)
	for name in (
		"Setup",
		"Accounts",
		"Selling",
		"Buying",
		"Stock",
		"Manufacturing",
		"Projects",
		"Assets",
		"Quality",
		"CRM",
		"Support",
		"Portal",
	)
]

# ERPNext's own Accounts entry, as its dock file titles it. The Customer row must not read the
# same: Customer is relabelled "Accounts" on prod, and Manage Dock lists both by title.
ERPNEXT_ACCOUNTS_TITLE = "Accounts"

# Every release of the dock file: its `modified`, and a hash of its rows. frappe re-imports the
# file only when `modified` is later than the row in the database (modules/import_file.py), so a
# change to the rows that leaves `modified` alone never reaches a site that already has the dock.
# Change the rows, move `modified` forward, and add a line here.
SHIPPED_DOCKS = {
	"2026-10-23 12:00:00.000000": "57f2f9138bc9cd0cb6ec2eb63dd0e3d05339a383900c4abd64bb39656035e858",
	"2026-10-23 16:00:00.000000": "c0f45e1fbb5e83c5fb47d4fd7768bc5f26ca3629b1cf68e0b09f9a77d59ec90a",
	"2026-10-27 12:00:00.000000": "bc0d11f00b0429f2553ce3d8360fb6b72c8a6a75a0a4f42db40cf1d2a9184bd7",
}

STATE = {}
dp = None
patch = None


def _json(path):
	return json.loads(path.read_text(encoding="utf-8"))


def _shipped_keys():
	return [dp._key(row) for row in _json(DOCK_FILE)["items"]]


# --- the stub ---------------------------------------------------------------------------------


class _Row(dict):
	def __getattr__(self, key):
		try:
			return self[key]
		except KeyError:
			raise AttributeError(key) from None


class _Dock:
	def __init__(self, record=None):
		record = record or {}
		self.name = record.get("name")
		self.app = record.get("app")
		self.user = record.get("user", "")
		self.standard = record.get("standard", 0)
		self.modified_by = record.get("modified_by")
		self.items = [_Row(row) for row in record.get("items", [])]

	def set(self, field, value):
		assert field == "items"
		self.items = [_Row(row) for row in value]

	def append(self, field, row):
		assert field == "items"
		self.items.append(_Row(row))

	def save(self, ignore_permissions=False):
		STATE["saves"].append((self.user, STATE["session_user"]))
		if STATE.get("fail_save"):
			raise RuntimeError("save refused")
		self.modified_by = STATE["session_user"]
		docks = STATE["docks"]
		if not self.name:
			self.name = f"dock-{len(docks) + 1}"
		record = {
			"name": self.name,
			"app": self.app,
			"user": self.user or "",
			"standard": self.standard,
			"modified_by": self.modified_by,
			"items": [dict(row) for row in self.items],
		}
		docks[:] = [d for d in docks if d["name"] != self.name] + [record]


def _matches(row, filters):
	for field, wanted in (filters or {}).items():
		value = row.get(field)
		if isinstance(wanted, list | tuple):
			op, values = wanted
			if op == "in" and value not in values:
				return False
			if op == "not in" and value in values:
				return False
		elif value != wanted:
			return False
	return True


def _get_all(doctype, filters=None, fields=None, order_by=None, pluck=None):
	if doctype in STATE["fail_get_all"]:
		raise RuntimeError(f"{doctype} gone")
	if doctype == "Dock":
		rows = STATE["docks"]
	elif doctype == "Dock Item":
		rows = [
			{**item, "parenttype": "Dock", "parent": dock["name"], "idx": i}
			for dock in STATE["docks"]
			for i, item in enumerate(dock["items"], 1)
		]
	elif doctype == "User":
		rows = STATE["users"]
	elif doctype == "User Role Profile":
		if STATE.get("no_role_profile_table"):
			raise RuntimeError("Table 'tabUser Role Profile' doesn't exist")
		rows = [
			{"parenttype": "User", "parent": user["name"], "role_profile": profile}
			for user in STATE["users"]
			for profile in user.get("_profiles", ())
		]
	else:
		raise AssertionError(doctype)
	out = [row for row in rows if _matches(row, filters)]
	if order_by:
		assert order_by == "idx asc", order_by
		out.sort(key=lambda row: row["idx"])
	return [_Row({key: row.get(key) for key in fields}) for row in out]


def _install_frappe_stub():
	frappe = types.ModuleType("frappe")
	frappe.get_all = _get_all
	frappe.get_doc = lambda doctype, name: _Dock(next(d for d in STATE["docks"] if d["name"] == name))
	frappe.new_doc = lambda doctype: _Dock()

	def delete_doc(doctype, name, ignore_permissions=False, force=False):
		STATE["deleted"].append(name)
		STATE["docks"][:] = [d for d in STATE["docks"] if d["name"] != name]

	def set_value(doctype, name, field, value, update_modified=True):
		STATE["set_value"].append((doctype, name, field, value, update_modified))
		for dock in STATE["docks"]:
			if dock["name"] == name:
				dock[field] = value

	def has_column(doctype, column):
		if not STATE["has_dock_field"]:
			raise RuntimeError("no such column")
		return True

	frappe.delete_doc = delete_doc
	frappe.db = types.SimpleNamespace(
		set_value=set_value,
		has_column=has_column,
		table_exists=lambda doctype, cached=True: STATE["dock_tables"],
		rollback=lambda: STATE.__setitem__("rollbacks", STATE["rollbacks"] + 1),
	)
	frappe.log_error = lambda *a, **k: STATE["errors"].append(k.get("title") or a)
	frappe.get_traceback = lambda: "traceback"
	frappe.session = types.SimpleNamespace(user="Administrator")
	frappe.get_roles = lambda user=None: list(STATE["roles"].get(user, ()))
	sys.modules["frappe"] = frappe


def setUpModule():
	global dp, patch
	_install_frappe_stub()
	for name in (
		"erpnext_enhancements.setup.dock_profiles",
		"erpnext_enhancements.patches.set_dock_site_layer",
	):
		sys.modules.pop(name, None)
	from erpnext_enhancements.patches import set_dock_site_layer as p
	from erpnext_enhancements.setup import dock_profiles as d

	dp, patch = d, p


def _reset(with_docks=True):
	STATE.clear()
	STATE.update(
		{
			"docks": [],
			"users": [],
			"saves": [],
			"set_value": [],
			"deleted": [],
			"errors": [],
			"rollbacks": 0,
			"fail_get_all": set(),
			"has_dock_field": True,
			"dock_tables": True,
			"session_user": "Administrator",
			"roles": {},
		}
	)
	if with_docks:
		_standard("erpnext", ERPNEXT_DOCK)
		_standard("erpnext_enhancements", _shipped_keys())


def _standard(app, keys):
	STATE["docks"].append(
		{
			"name": app,
			"app": app,
			"user": "",
			"standard": 1,
			"modified_by": "Administrator",
			"items": [dp._row(key, 0) for key in keys],
		}
	)


def _user(email, profiles=(), chosen=None):
	STATE["users"].append(
		{
			"name": email,
			"enabled": 1,
			"user_type": "System User",
			"role_profile_name": profiles[0] if profiles else None,
			"custom_dock_profile": chosen,
			"_profiles": tuple(profiles),
		}
	)


def _layer(user):
	return next(
		(d for d in STATE["docks"] if d["app"] == "erpnext" and d["user"] == user and not d["standard"]), None
	)


def _rail(user):
	"""The entries a layer shows, in order."""
	return [dp._key(row) for row in _layer(user)["items"] if not row["hidden"]]


def _arranged_by(user, by, keys):
	STATE["docks"].append(
		{
			"name": f"own-{user or 'site'}",
			"app": "erpnext",
			"user": user,
			"standard": 0,
			"modified_by": by,
			"items": [dp._row(key, 0) for key in keys],
		}
	)


# --- the shipped dock file --------------------------------------------------------------------


class TheShippedDockTest(unittest.TestCase):
	def setUp(self):
		self.dock = _json(DOCK_FILE)

	def test_it_is_this_apps_own_dock_mounted_on_erpnexts(self):
		self.assertEqual(self.dock["doctype"], "Dock")
		self.assertEqual(self.dock["name"], "erpnext_enhancements")
		self.assertEqual(self.dock["app"], "erpnext_enhancements")
		self.assertEqual(self.dock["standard"], 1)
		self.assertEqual(self.dock["user"], "")
		self.assertEqual(self.dock["mount_on"], "erpnext")

	def test_it_sits_where_frappe_imports_it(self):
		"""frappe 16.50 walks <app>/dock/<name>/<name>.json (model/sync.py APP_ROOTED_DOCTYPES)."""
		self.assertEqual(DOCK_FILE.parent.name, self.dock["name"])
		self.assertEqual(DOCK_FILE.stem, self.dock["name"])
		self.assertEqual(DOCK_FILE.parent.parent, APP / "dock")

	def test_every_row_points_somewhere_and_reads_on_its_own(self):
		"""A row on an app's own dock has nothing below it, so Dock.validate wants its icon and title."""
		for row in self.dock["items"]:
			with self.subTest(row=row.get("title")):
				self.assertIn(row["link_type"], {"Sidebar", "Workspace", "URL"})
				self.assertTrue(row.get("icon") and row.get("title"))
				if row["link_type"] == "URL":
					self.assertTrue(row.get("url", "").startswith("/desk/"))
					self.assertFalse(row.get("link_to"))
				else:
					self.assertTrue(row.get("link_to"))

	def test_no_entry_twice(self):
		keys = _shipped_keys()
		self.assertEqual(len(keys), len(set(keys)))

	def test_every_workspace_row_names_a_workspace_we_ship(self):
		shipped = {_json(path)["name"] for path in APP.glob("*/workspace/*/*.json")}
		for link_type, target in _shipped_keys():
			if link_type == "Workspace":
				with self.subTest(workspace=target):
					self.assertIn(target, shipped)

	def test_opportunities_opens_the_stock_kanban(self):
		"""The board everyone works deals on: /desk/opportunity/view/kanban/<board>."""
		self.assertIn(dp.OPPORTUNITIES, _shipped_keys())
		self.assertRegex(dp.OPPORTUNITIES[1], r"^/desk/opportunity/view/kanban/Opportunity$")

	def test_suppliers_and_customers_open_their_lists(self):
		"""A dock row cannot open a DocType, so the two lists are URL rows (v1.736.0)."""
		rows = {dp._key(row): row for row in self.dock["items"]}
		self.assertEqual(dp.SUPPLIERS, ("URL", "/desk/supplier"))
		self.assertEqual(dp.CUSTOMERS, ("URL", "/desk/customer"))
		self.assertEqual(rows[dp.SUPPLIERS]["title"], "Suppliers")
		self.assertIn("Accounts", rows[dp.CUSTOMERS]["title"])
		self.assertNotEqual(rows[dp.CUSTOMERS]["title"], ERPNEXT_ACCOUNTS_TITLE)

	def test_changed_rows_come_with_a_later_modified(self):
		"""Otherwise migrate skips the file on a site that already holds the dock."""
		digest = hashlib.sha256(json.dumps(self.dock["items"], sort_keys=True).encode()).hexdigest()
		self.assertIn(self.dock["modified"], SHIPPED_DOCKS, "add this release to SHIPPED_DOCKS")
		self.assertEqual(
			SHIPPED_DOCKS[self.dock["modified"]],
			digest,
			"the rows changed: move `modified` forward and add the new hash to SHIPPED_DOCKS",
		)
		self.assertEqual(self.dock["modified"], max(SHIPPED_DOCKS))


# --- the profiles -----------------------------------------------------------------------------


class TheProfilesTest(unittest.TestCase):
	def test_every_entry_named_anywhere_is_on_the_rail(self):
		"""A key the rail does not hold is skipped in silence, so a typo would just go missing."""
		rail = set(ERPNEXT_DOCK) | set(_shipped_keys())
		for profile, keys in {"site default": dp.SITE_DEFAULT, **dp.PROFILES}.items():
			for key in keys:
				with self.subTest(profile=profile, entry=key):
					self.assertIn(key, rail)

	def test_the_site_default_is_what_nik_asked_for(self):
		"""Nik, 2026-10-23: "Buying, Stock, Projects, Opportunities Kanban (CRM)"."""
		self.assertEqual(dp.SITE_DEFAULT[:4], (dp.BUYING, dp.STOCK, dp.PROJECTS, dp.OPPORTUNITIES))

	def test_the_ceo_profile_is_what_nik_asked_for(self):
		"""Nik, 2026-10-23: "Projects, Opportunities, CRM, Supplier, Accounts (Customer)"."""
		self.assertEqual(
			dp.PROFILES["CEO"], (dp.PROJECTS, dp.OPPORTUNITIES, dp.CRM, dp.SUPPLIERS, dp.CUSTOMERS)
		)
		self.assertNotIn(dp.ACCOUNTS, dp.PROFILES["CEO"])

	def test_the_ceo_profile_is_only_ever_chosen(self):
		"""No Role Profile maps to it; Executive stays with Management."""
		self.assertNotIn("CEO", [profile for profile, _ in dp.BY_ROLE_PROFILE])
		self.assertEqual(dp.profile_for(None, {"Executive"}), "Management")
		self.assertEqual(dp.profile_for("CEO", {"Executive"}), "CEO")

	def test_sales_has_crm_selling_and_projects_but_not_buying(self):
		"""Nik's example: Brian 'has CRM and maybe Selling and Projects, but he wouldn't need Buying'."""
		sales = dp.PROFILES["Sales"]
		for key in (dp.CRM, dp.SELLING, dp.PROJECTS, dp.OPPORTUNITIES):
			self.assertIn(key, sales)
		self.assertNotIn(dp.BUYING, sales)

	def test_no_profile_names_an_entry_twice(self):
		for profile, keys in dp.PROFILES.items():
			with self.subTest(profile=profile):
				self.assertEqual(len(keys), len(set(keys)))

	def test_the_user_field_offers_exactly_the_profiles(self):
		field = next(
			row
			for row in _json(APP / "fixtures" / "custom_field.json")
			if row["name"] == "User-custom_dock_profile"
		)
		self.assertEqual(field["fieldname"], dp.DOCK_PROFILE_FIELD)
		self.assertEqual(field["fieldtype"], "Select")
		options = field["options"].split("\n")
		self.assertEqual(options[0], "")
		self.assertEqual(set(options[1:]), set(dp.PROFILES) | {dp.SITE_DEFAULT_OPTION})

	def test_role_profile_rules_name_real_profiles_and_role_profiles(self):
		shipped = {row["name"] for row in _json(APP / "fixtures" / "role_profile.json")}
		for profile, role_profiles in dp.BY_ROLE_PROFILE:
			with self.subTest(profile=profile):
				self.assertIn(profile, dp.PROFILES)
				self.assertEqual(sorted(role_profiles - shipped), [])


class WhoGetsWhichTest(unittest.TestCase):
	"""Prod's people on 2026-10-23, by the Role Profiles they hold."""

	def test_each_job(self):
		cases = {
			"Brian": ({"Sales", "Sales Team", "Training Learner", "Contract Manager"}, "Sales"),
			"Rich": ({"Sales Team", "Sales & Marketing", "Training Learner"}, "Sales"),
			"Lisa": ({"Finance Team", "Finance", "Accounting", "PO Creators", "Production Team"}, "Finance"),
			"Clegg": ({"Production Team", "PO Creators", "Projects & Operations"}, "Production Lead"),
			"Logan": ({"Design Team", "Production Team", "Training Learner"}, "Design"),
			"Austin": ({"Production Team", "Training Learner"}, "Field Tech"),
			"Lian": ({"Technician", "Production Team"}, "Field Tech"),
			"Nik": (set(), None),
			"Kendalyn": ({"HR", "Training Learner"}, None),
		}
		for person, (held, expected) in cases.items():
			with self.subTest(person=person):
				self.assertEqual(dp.profile_for(None, held), expected)

	def test_the_user_field_wins(self):
		self.assertEqual(dp.profile_for("Purchasing", set()), "Purchasing")
		self.assertEqual(dp.profile_for("Management", {"Sales"}), "Management")

	def test_site_default_means_no_layer_of_their_own(self):
		self.assertIsNone(dp.profile_for("Site Default", {"Sales"}))

	def test_an_unknown_choice_falls_back_to_the_role_profiles(self):
		self.assertEqual(dp.profile_for("Retired Profile", {"Sales"}), "Sales")


class RowsForTest(unittest.TestCase):
	def test_shown_in_order_then_everything_else_hidden(self):
		base = [("Sidebar", "A"), ("Sidebar", "B"), ("Sidebar", "C"), ("URL", "/desk/x")]
		rows = dp.rows_for((("URL", "/desk/x"), ("Sidebar", "B")), base)
		self.assertEqual(
			dp._signature(rows),
			[("URL", "/desk/x", 0), ("Sidebar", "B", 0), ("Sidebar", "A", 1), ("Sidebar", "C", 1)],
		)
		self.assertEqual(
			rows[0], {"link_type": "URL", "link_to": None, "url": "/desk/x", "hidden": 0, "added": 0}
		)
		self.assertEqual(rows[1]["link_to"], "B")
		self.assertIsNone(rows[1]["url"])

	def test_an_entry_the_rail_lacks_is_skipped_and_none_repeats(self):
		base = [("Sidebar", "A"), ("Sidebar", "B")]
		rows = dp.rows_for((("Sidebar", "Gone"), ("Sidebar", "B"), ("Sidebar", "B")), base)
		self.assertEqual(dp._signature(rows), [("Sidebar", "B", 0), ("Sidebar", "A", 1)])

	def test_every_row_is_a_reference(self):
		"""Each names an entry already below it, so none is an add needing an icon and title."""
		rows = dp.rows_for(dp.PROFILES["Management"], ERPNEXT_DOCK)
		self.assertTrue(all(row["added"] == 0 for row in rows))


# --- the writers, against the stub ------------------------------------------------------------


class SiteLayerTest(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_the_rail_is_erpnexts_then_ours(self):
		self.assertEqual(dp.rail_base(), ERPNEXT_DOCK + _shipped_keys())

	def test_creates_the_site_layer_naming_every_entry(self):
		self.assertEqual(dp.write_site_layer(), "created")
		layer = _layer("")
		self.assertEqual(_rail(""), list(dp.SITE_DEFAULT))
		self.assertEqual(len(layer["items"]), len(ERPNEXT_DOCK) + len(_shipped_keys()))
		self.assertEqual(layer["modified_by"], "Administrator")

	def test_the_patch_replaces_niks_hand_made_layer_once(self):
		"""Prod, 2026-10-23 08:56: Buying, Stock, Projects, CRM, saved by Nik in Manage Dock."""
		hand = [dp.BUYING, dp.STOCK, dp.PROJECTS, dp.CRM]
		_arranged_by("", "nikolas.bradshaw@sapphirefountains.com", hand)
		self.assertEqual(dp.write_site_layer(), "kept")
		self.assertEqual(_rail(""), hand)
		patch.execute()
		self.assertEqual(_rail(""), list(dp.SITE_DEFAULT))
		self.assertEqual(_layer("")["modified_by"], "Administrator")
		self.assertEqual(STATE["errors"], [])

	def test_a_second_run_writes_nothing(self):
		dp.write_site_layer()
		saves = len(STATE["saves"])
		self.assertEqual(dp.write_site_layer(), "unchanged")
		patch.execute()
		self.assertEqual(len(STATE["saves"]), saves)

	def test_a_site_before_the_new_desk_is_left_alone(self):
		_reset(with_docks=False)
		self.assertIsNone(dp.write_site_layer(force=True))
		patch.execute()
		self.assertEqual(STATE["docks"], [])

	def test_the_patch_cannot_raise(self):
		STATE["fail_get_all"].add("Dock")
		patch.execute()
		self.assertEqual(STATE["errors"], ["set_dock_site_layer"])
		self.assertEqual(STATE["rollbacks"], 1)

	def test_registered_post_model_sync(self):
		text = PATCHES_TXT.read_text(encoding="utf-8")
		post = text[text.index("[post_model_sync]") :]
		self.assertIn("\nerpnext_enhancements.patches.set_dock_site_layer\n", post)


class EveryonesLayerTest(unittest.TestCase):
	def setUp(self):
		_reset()
		_user("brian@x", ("Sales", "Sales Team"))
		_user("parker@x", chosen="Purchasing")
		_user("austin@x", ("Production Team",))
		_user("nik@x")

	def test_each_person_gets_their_jobs_rail(self):
		dp.apply_dock_profiles()
		self.assertEqual(_rail("brian@x"), list(dp.PROFILES["Sales"]))
		self.assertEqual(_rail("parker@x"), list(dp.PROFILES["Purchasing"]))
		self.assertEqual(_rail("austin@x"), list(dp.PROFILES["Field Tech"]))
		self.assertNotIn(dp.BUYING, _rail("brian@x"))

	def test_the_ceo_gets_his_five_and_everyone_else_holds_them_hidden(self):
		_user("james@x", chosen="CEO")
		dp.apply_dock_profiles()
		self.assertEqual(
			_rail("james@x"), [dp.PROJECTS, dp.OPPORTUNITIES, dp.CRM, dp.SUPPLIERS, dp.CUSTOMERS]
		)
		for person in ("brian@x", "parker@x", "austin@x", ""):
			hidden = {dp._key(row) for row in _layer(person)["items"] if row["hidden"]}
			with self.subTest(person=person or "site"):
				self.assertIn(dp.SUPPLIERS, hidden)
				self.assertIn(dp.CUSTOMERS, hidden)

	def test_new_rows_reach_layers_written_before_them(self):
		"""v1.734.0's layers lack the two rows; the next migrate adds them, hidden."""
		_reset(with_docks=False)
		_standard("erpnext", ERPNEXT_DOCK)
		_standard(
			"erpnext_enhancements", [k for k in _shipped_keys() if k not in (dp.SUPPLIERS, dp.CUSTOMERS)]
		)
		_user("brian@x", ("Sales",))
		dp.apply_dock_profiles()
		self.assertNotIn(dp.SUPPLIERS, [dp._key(row) for row in _layer("brian@x")["items"]])
		STATE["docks"][:] = [d for d in STATE["docks"] if d["name"] != "erpnext_enhancements"]
		_standard("erpnext_enhancements", _shipped_keys())
		dp.apply_dock_profiles()
		rows = {dp._key(row): row["hidden"] for row in _layer("brian@x")["items"]}
		self.assertEqual(rows[dp.SUPPLIERS], 1)
		self.assertEqual(rows[dp.CUSTOMERS], 1)
		self.assertEqual(_rail("brian@x"), list(dp.PROFILES["Sales"]))

	def test_no_profile_means_the_shared_dock(self):
		dp.apply_dock_profiles()
		self.assertIsNone(_layer("nik@x"))
		self.assertEqual(_rail(""), list(dp.SITE_DEFAULT))

	def test_every_layer_names_the_whole_rail(self):
		"""The hidden rows are what Manage Dock offers back."""
		dp.apply_dock_profiles()
		self.assertEqual(len(_layer("brian@x")["items"]), len(dp.rail_base()))

	def test_a_layer_someone_arranged_is_theirs(self):
		_arranged_by("brian@x", "brian@x", [dp.PROJECTS, dp.OPPORTUNITIES])
		dp.apply_dock_profiles()
		self.assertEqual(_rail("brian@x"), [dp.PROJECTS, dp.OPPORTUNITIES])

	def test_a_layer_still_the_apps_follows_the_profile(self):
		_arranged_by("brian@x", "Administrator", [dp.PROJECTS])
		dp.apply_dock_profiles()
		self.assertEqual(_rail("brian@x"), list(dp.PROFILES["Sales"]))

	def test_losing_a_profile_drops_the_apps_layer_but_never_a_persons(self):
		_arranged_by("nik@x", "Administrator", [dp.PROJECTS])
		_user("logan@x")
		_arranged_by("logan@x", "logan@x", [dp.PROJECTS])
		dp.apply_dock_profiles()
		self.assertIsNone(_layer("nik@x"))
		self.assertEqual(_rail("logan@x"), [dp.PROJECTS])

	def test_disabled_people_and_administrator_are_not_touched(self):
		_user("gone@x", ("Sales",))
		STATE["users"][-1]["enabled"] = 0
		_user("Administrator", ("Sales",))
		dp.apply_dock_profiles()
		self.assertIsNone(_layer("gone@x"))
		self.assertIsNone(_layer("Administrator"))
		self.assertIsNone(dp.apply_for_user("Administrator"))

	def test_a_second_run_writes_nothing(self):
		dp.apply_dock_profiles()
		saves = len(STATE["saves"])
		dp.apply_dock_profiles()
		self.assertEqual(len(STATE["saves"]), saves)

	def test_before_the_field_exists_role_profiles_still_decide(self):
		STATE["has_dock_field"] = False
		dp.apply_dock_profiles()
		self.assertEqual(_rail("brian@x"), list(dp.PROFILES["Sales"]))
		self.assertIsNone(_layer("parker@x"))

	def test_a_frappe_without_multi_profile_users_reads_role_profile_name(self):
		STATE["no_role_profile_table"] = True
		dp.apply_dock_profiles()
		self.assertEqual(_rail("brian@x"), list(dp.PROFILES["Sales"]))

	def test_one_person_failing_does_not_stop_the_rest_or_the_migrate(self):
		STATE["fail_save"] = True
		dp.apply_dock_profiles()
		self.assertIn("Dock profiles not applied", STATE["errors"])
		STATE["fail_save"] = False
		STATE["errors"].clear()
		original = dp.write_layer

		def flaky(user, rows, force=False):
			if user == "brian@x":
				raise RuntimeError("one bad row")
			return original(user, rows, force=force)

		dp.write_layer = flaky
		try:
			dp.apply_dock_profiles()
		finally:
			dp.write_layer = original
		self.assertEqual(STATE["errors"], ["Dock profile not applied: brian@x"])
		self.assertEqual(_rail("parker@x"), list(dp.PROFILES["Purchasing"]))

	def test_a_site_before_the_new_desk_is_left_alone(self):
		STATE["docks"].clear()
		dp.apply_dock_profiles()
		self.assertEqual(STATE["docks"], [])
		self.assertEqual(STATE["errors"], [])

	def test_a_site_without_the_dock_tables_is_not_even_queried(self):
		STATE["dock_tables"] = False
		STATE["fail_get_all"].add("Dock")
		dp.apply_dock_profiles()
		self.assertIsNone(dp.apply_for_user("brian@x"))
		self.assertEqual(STATE["errors"], [])


class UserSaveTest(unittest.TestCase):
	"""User on_update, run as whoever saved the User."""

	def setUp(self):
		_reset()
		_user("parker@x")
		STATE["session_user"] = "nik@x"

	def _doc(self, before, **now):
		person = next(u for u in STATE["users"] if u["name"] == now.get("name", "parker@x"))
		person.update({k: v for k, v in now.items() if k in ("custom_dock_profile", "enabled")})
		values = {
			"name": person["name"],
			"enabled": person["enabled"],
			"user_type": "System User",
			"custom_dock_profile": person["custom_dock_profile"],
			"role_profile_name": person["role_profile_name"],
			"role_profiles": [{"role_profile": p} for p in person["_profiles"]],
		}
		doc = _Row(values)
		doc.get_doc_before_save = lambda: None if before is None else _Row(before)
		return doc

	def test_choosing_a_profile_applies_it_even_over_their_own(self):
		_arranged_by("parker@x", "parker@x", [dp.PROJECTS])
		dp.on_user_update(
			self._doc({"enabled": 1, "custom_dock_profile": None}, custom_dock_profile="Purchasing")
		)
		self.assertEqual(_rail("parker@x"), list(dp.PROFILES["Purchasing"]))

	def test_the_layer_reads_as_the_apps_although_nik_saved_the_user(self):
		dp.on_user_update(
			self._doc({"enabled": 1, "custom_dock_profile": None}, custom_dock_profile="Purchasing")
		)
		self.assertEqual(STATE["saves"], [("parker@x", "nik@x")])
		self.assertEqual(_layer("parker@x")["modified_by"], "Administrator")

	def test_a_save_that_changes_none_of_the_three_does_nothing(self):
		before = {"enabled": 1, "custom_dock_profile": None, "role_profiles": []}
		dp.on_user_update(self._doc(before))
		self.assertEqual(STATE["saves"], [])

	def test_a_role_profile_change_keeps_their_own(self):
		_arranged_by("parker@x", "parker@x", [dp.PROJECTS])
		STATE["users"][0]["_profiles"] = ("Sales",)
		before = {"enabled": 1, "custom_dock_profile": None, "role_profiles": []}
		dp.on_user_update(self._doc(before))
		self.assertEqual(_rail("parker@x"), [dp.PROJECTS])

	def test_a_new_user_gets_their_profile(self):
		STATE["users"][0]["_profiles"] = ("Production Team",)
		dp.on_user_update(self._doc(None))
		self.assertEqual(_rail("parker@x"), list(dp.PROFILES["Field Tech"]))

	def test_a_disabled_user_is_left(self):
		dp.on_user_update(
			self._doc({"enabled": 1, "custom_dock_profile": None}, custom_dock_profile="Sales", enabled=0)
		)
		self.assertEqual(STATE["saves"], [])

	def test_never_raises(self):
		STATE["fail_get_all"].add("Dock")
		dp.on_user_update(self._doc({"enabled": 1, "custom_dock_profile": None}, custom_dock_profile="Sales"))
		self.assertEqual(STATE["errors"], ["Dock profile not applied: parker@x"])

	def test_administrator_guest_and_website_users_leave_before_anything_runs(self):
		for name, user_type in (
			("Administrator", "System User"),
			("Guest", "Website User"),
			("c@x", "Website User"),
		):
			doc = _Row({"name": name, "enabled": 1, "user_type": user_type})
			doc.get_doc_before_save = lambda: self.fail("read the previous version")
			dp.on_user_update(doc)
		self.assertEqual(STATE["saves"], [])
		self.assertEqual(STATE["errors"], [])


# --- the field technicians' home grid (v1.750.0) ----------------------------------------------

# Nik, 2026-10-27, in his order, then the three he added: the rail's entry and the home grid's tile.
NIKS_FIELD_TECH_DESK = [
	(("Workspace", "Sapphire Maintenance"), "Sapphire Maintenance"),
	(("Workspace", "Morning Briefing"), "Morning Briefing"),
	(("Sidebar", "CRM"), "CRM"),
	(("Sidebar", "Projects"), "Projects"),
	(("URL", "/desk/time-kiosk"), "Time Kiosk"),
	(("Workspace", "Travel"), "Travel"),
	(("Workspace", "Training"), "Training"),
	(("URL", "/desk/stock-scan"), "Stock Scan"),
]
FIELD_TECH_TILES = [tile for _entry, tile in NIKS_FIELD_TECH_DESK]

# ERPNext's own tiles among them; every other one is a tile this app makes.
ERPNEXT_TILES = {"CRM", "Projects"}


def _icon(name, **fields):
	return _Row({"name": name, "label": name, "hidden": 0, "parent_icon": None, "idx": 0, **fields})


def _site_grid():
	"""A grid as frappe builds it: the eight among others, CRM hidden and in ERPNext's folder."""
	return [
		_icon("Buying", parent_icon="ERPNext", idx=1),
		_icon("Stock Scan", idx=1),
		_icon("Time Kiosk", idx=1),
		_icon("Projects", parent_icon="ERPNext", idx=1),
		_icon("CRM", parent_icon="ERPNext", hidden=1, idx=1),
		_icon("ERPNext", hidden=1, idx=101),
		_icon("Accounting", icon_type="Folder", idx=1),
		_icon("Training"),
		_icon("Travel"),
		_icon("Morning Briefing"),
		_icon("Sapphire Maintenance", label="Maintenance"),
		_icon("Sales Hub"),
	]


class TheFieldTechDeskTest(unittest.TestCase):
	def test_the_rail_and_the_grid_are_what_nik_asked_for(self):
		self.assertEqual(list(dp.FIELD_TECH), NIKS_FIELD_TECH_DESK)
		self.assertEqual(list(dp.PROFILES["Field Tech"]), [entry for entry, _tile in NIKS_FIELD_TECH_DESK])
		self.assertEqual(list(dp.HOME_GRIDS["Field Tech"]), FIELD_TECH_TILES)

	def test_only_the_field_techs_grid_is_cut(self):
		self.assertEqual(set(dp.HOME_GRIDS), {"Field Tech"})

	def test_every_tile_is_one_a_site_has(self):
		"""A tile the grid does not hold is skipped in silence, so a typo would just go missing."""
		from erpnext_enhancements.setup.desktop_icon_map import TILES

		for tile in FIELD_TECH_TILES:
			with self.subTest(tile=tile):
				self.assertTrue(tile in TILES or tile in ERPNEXT_TILES)

	def test_the_kiosk_and_stock_scan_rows_open_desk_pages_that_leave_for_the_web_apps(self):
		"""A rail row opens through frappe.set_route, so it needs a desk page, which sends the
		browser on with location.replace so Back is not trapped on it."""
		for (link_type, url), www, page_dir in (
			(dp.TIME_KIOSK, "/kiosk", APP / "workforce" / "page" / "time_kiosk"),
			(dp.STOCK_SCAN, "/stock-scan", APP / "inventory_enhancements" / "page" / "stock_scan"),
		):
			with self.subTest(url=url):
				self.assertEqual(link_type, "URL")
				page = _json(page_dir / f"{page_dir.name}.json")
				self.assertEqual(url, "/desk/" + page["name"])
				self.assertEqual(page["page_name"], page["name"])
				script = (page_dir / f"{page_dir.name}.js").read_text(encoding="utf-8")
				self.assertRegex(script, r"window\.location\.replace\(['\"]" + re.escape(www) + r"['\"]\)")
				self.assertTrue((APP / "www" / f"{www.strip('/')}.html").exists())

	def test_the_stock_scan_page_admits_whoever_stock_scan_does(self):
		from erpnext_enhancements.inventory_enhancements.stock_scan_rules import SCAN_ROLES

		page = _json(APP / "inventory_enhancements" / "page" / "stock_scan" / "stock_scan.json")
		self.assertEqual({row["role"] for row in page["roles"]}, set(SCAN_ROLES))
		self.assertEqual(page["module"], "Inventory Enhancements")


class HomeGridTest(unittest.TestCase):
	def test_keeps_the_tiles_in_order_shown_and_at_the_top_level(self):
		grid = dp.home_grid(_site_grid(), dp.HOME_GRIDS["Field Tech"])
		self.assertEqual([icon["name"] for icon in grid], FIELD_TECH_TILES)
		self.assertEqual([icon["idx"] for icon in grid], list(range(1, 9)))
		self.assertTrue(all(icon["hidden"] == 0 and icon["parent_icon"] is None for icon in grid))
		self.assertEqual(grid[0]["label"], "Maintenance")
		self.assertIsInstance(grid[0], _Row)

	def test_copies_rather_than_changing_frappes_list(self):
		site = _site_grid()
		dp.home_grid(site, dp.HOME_GRIDS["Field Tech"])
		crm = next(icon for icon in site if icon["name"] == "CRM")
		self.assertEqual((crm["hidden"], crm["parent_icon"], crm["idx"]), (1, "ERPNext", 1))

	def test_a_tile_frappe_refused_stays_out(self):
		"""It hides; it never grants. A tile not in frappe's list for this person is not added."""
		site = [icon for icon in _site_grid() if icon["name"] != "Stock Scan"]
		grid = dp.home_grid(site, dp.HOME_GRIDS["Field Tech"])
		self.assertEqual([icon["name"] for icon in grid], FIELD_TECH_TILES[:-1])

	def test_none_of_them_leaves_the_grid_alone(self):
		self.assertIsNone(dp.home_grid([_icon("Buying")], dp.HOME_GRIDS["Field Tech"]))
		self.assertIsNone(dp.home_grid([], dp.HOME_GRIDS["Field Tech"]))


class TrimHomeGridTest(unittest.TestCase):
	"""Prod's people on 2026-10-27, by the Role Profiles they hold."""

	def setUp(self):
		_reset(with_docks=False)
		_user("tech@x", ("Production Team", "Training Learner"))
		_user("lead@x", ("Production Team", "Projects & Operations", "Maintenance Supervisor"))
		_user("finance@x", ("Finance Team", "Production Team", "Maintenance Reviewer"))
		_user("designer@x", ("Design Team", "Production Team"))
		_user("ops@x")
		_user("sales@x", ("Sales", "Sales Team"))

	def _boot(self, user, roles=()):
		dp.frappe.session.user = user
		STATE["roles"][user] = roles
		bootinfo = {"desktop_icons": _site_grid()}
		dp.trim_home_grid(bootinfo)
		return [icon["name"] for icon in bootinfo["desktop_icons"]]

	def tearDown(self):
		dp.frappe.session.user = "Administrator"

	def test_a_technician_gets_the_eight(self):
		self.assertEqual(self._boot("tech@x", ("Production Team", "Maintenance Manager")), FIELD_TECH_TILES)
		self.assertEqual(STATE["errors"], [])

	def test_production_team_alongside_another_job_keeps_the_whole_grid(self):
		whole = [icon["name"] for icon in _site_grid()]
		for person in ("lead@x", "finance@x", "designer@x", "ops@x", "sales@x"):
			with self.subTest(person=person):
				self.assertEqual(self._boot(person, ("Production Team",)), whole)

	def test_the_dock_profile_on_the_user_form_decides_both_ways(self):
		_user("marketing@x", ("Sales Team",), chosen="Field Tech")
		_user("tech2@x", ("Production Team",), chosen="Site Default")
		self.assertEqual(self._boot("marketing@x"), FIELD_TECH_TILES)
		self.assertEqual(len(self._boot("tech2@x")), len(_site_grid()))

	def test_system_and_workspace_managers_keep_the_whole_grid(self):
		for role in ("System Manager", "Workspace Manager"):
			with self.subTest(role=role):
				self.assertEqual(len(self._boot("tech@x", ("Production Team", role))), len(_site_grid()))

	def test_the_apps_screen_has_no_grid_to_cut(self):
		dp.frappe.session.user = "tech@x"
		bootinfo = {}
		dp.trim_home_grid(bootinfo)
		self.assertEqual(bootinfo, {})
		self.assertEqual(STATE["errors"], [])

	def test_administrator_and_a_disabled_or_unknown_user_are_left_alone(self):
		STATE["users"][0]["enabled"] = 0
		for person in ("Administrator", "tech@x", "nobody@x"):
			with self.subTest(person=person):
				self.assertEqual(len(self._boot(person)), len(_site_grid()))

	def test_never_raises(self):
		STATE["fail_get_all"].add("User")
		self.assertEqual(len(self._boot("tech@x")), len(_site_grid()))
		self.assertEqual(STATE["errors"], ["Home grid not trimmed at boot"])


class WiringTest(unittest.TestCase):
	def setUp(self):
		self.hooks = HOOKS.read_text(encoding="utf-8")

	def test_boot_trims_the_home_grid(self):
		"""``extend_bootinfo`` runs on every boot, cached or not, after frappe built the grid."""
		self.assertIn('extend_bootinfo = "erpnext_enhancements.boot.boot_session"', self.hooks)
		boot = BOOT.read_text(encoding="utf-8")
		body = boot[boot.index("def boot_session(bootinfo):") :]
		body = body[: body.index("\ndef ")]
		self.assertIn("\n\ttrim_home_grid(bootinfo)\n", body)
		self.assertIn("from erpnext_enhancements.setup.dock_profiles import trim_home_grid", boot)

	def test_after_migrate_applies_the_profiles(self):
		block = self.hooks[self.hooks.index("\nafter_migrate = [") :]
		block = block[: block.index("\n]")]
		self.assertIn('"erpnext_enhancements.setup.dock_profiles.apply_dock_profiles"', block)

	def test_a_user_save_applies_theirs(self):
		block = self.hooks[self.hooks.index('\t"User": {') :]
		block = block[: block.index("\n\t},")]
		self.assertRegex(
			block,
			re.compile(
				r'"on_update": \[[^\]]*"erpnext_enhancements\.setup\.dock_profiles\.on_user_update"', re.S
			),
		)


if __name__ == "__main__":
	unittest.main()
