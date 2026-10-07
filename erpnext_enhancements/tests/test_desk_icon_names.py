# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Every icon name this app ships is one the desk can draw, and the v1.575.1 heal patch. Bench-free.

``frappe.utils.icon(name)`` draws ``<use href="#icon-<name>">`` into the sprites frappe and ERPNext
load. A name no sprite has draws nothing and raises nothing. On frappe 16.50.0 that left five blank
slots on our Dock rail (2026-10-07), from workspace icons named ``money``, ``service``, ``water``,
``assistant`` and ``mobile``, which no v16 sprite ever had, and ``book-marked``, which 16.50's lucide
upgrade renamed. The old desk drew our tiles from artwork and never looked the names up, so they
were wrong for months with nothing to show for it.

The check is an ALLOWLIST: ``tests/data/desk_icon_names.txt``, every name the sprites of a pinned
frappe + ERPNext release define, written by ``scripts/list_desk_icon_names.py``. ``test_hr_module``
keeps a denylist instead, on the grounds that an inventory rots on upgrade; but a denylist only
catches a name somebody has already been bitten by, and none of today's six was on it. The
inventory goes stale only when prod upgrades, and regenerating it is one command. The existing
``test_every_icon_is_in_v16s_sprites`` in ``test_knowledge_base_entry_points`` reads a sibling frappe
checkout and so skips in CI, which is how ``book-marked`` got through.

Installs its own ``frappe`` stub in ``setUpModule``, which is why it has its own CI step.

Run: python -m unittest erpnext_enhancements.tests.test_desk_icon_names
"""

import json
import re
import sys
import types
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
REPO = APP.parent
NAMES_TXT = APP / "tests" / "data" / "desk_icon_names.txt"
PATCHES_TXT = APP / "patches.txt"

# frappe.utils.icon("literal" ...) in our own scripts. Vendored libraries are not ours to fix.
JS_CALL = re.compile(r"""frappe\.utils\.icon\(\s*["']([^"']+)["']""")

STATE = {}
patch = None


def _names():
	lines = NAMES_TXT.read_text(encoding="utf-8").splitlines()
	return {line for line in lines if line and not line.startswith("#")}


def _json(path):
	return json.loads(path.read_text(encoding="utf-8"))


def _icon_values(obj, keys=("icon", "header_icon")):
	"""Every string under an icon key, at any depth."""
	if isinstance(obj, dict):
		for key, value in obj.items():
			if key in keys and isinstance(value, str):
				yield value
			else:
				yield from _icon_values(value, keys)
	elif isinstance(obj, list):
		for value in obj:
			yield from _icon_values(value, keys)


def _desk_jsons():
	"""Workspaces, plus every sidebar and dock file in either v16 layout."""
	yield from APP.glob("*/workspace/*/*.json")
	yield from APP.glob("workspace_sidebar/*.json")
	yield from APP.glob("*/sidebar/*/*.json")
	yield from APP.glob("dock/**/*.json")


def _workspaces():
	return {_json(path)["name"]: path for path in APP.glob("*/workspace/*/*.json")}


def _drawable(name, names):
	# An emoji is drawn as text; everything else goes through the sprite.
	return not name.isascii() or name in names


class TheInventoryTest(unittest.TestCase):
	"""A wrong or truncated inventory would pass everything below, so check it first."""

	def test_it_is_a_whole_release(self):
		names = _names()
		self.assertGreater(len(names), 2000)
		for known in ("house", "settings", "wrench", "calculator-duotone", "es-line-add"):
			self.assertIn(known, names)

	def test_it_does_not_carry_the_names_that_drew_blank(self):
		self.assertEqual(sorted(set(patch.BROKEN) & _names()), [])

	def test_it_names_the_release_it_came_from(self):
		head = NAMES_TXT.read_text(encoding="utf-8").splitlines()[:4]
		self.assertTrue(
			any(re.search(r"frappe v16\.\d+\.\d+ \+ erpnext v16\.\d+\.\d+", line) for line in head)
		)


class EveryShippedIconDrawsTest(unittest.TestCase):
	def test_workspace_sidebar_and_dock_files(self):
		names = _names()
		paths = list(_desk_jsons())
		self.assertGreater(len(paths), 30)
		for path in paths:
			for icon in _icon_values(_json(path)):
				with self.subTest(file=str(path.relative_to(REPO)), icon=icon):
					self.assertTrue(_drawable(icon, names), f"no v16 sprite draws {icon!r}")

	def test_literal_icon_calls_in_our_scripts(self):
		names = _names()
		for path in (APP / "public").rglob("*.js"):
			if "lib" in path.parts or path.name.endswith(".min.js"):
				continue
			for icon in JS_CALL.findall(path.read_text(encoding="utf-8", errors="replace")):
				with self.subTest(file=str(path.relative_to(REPO)), icon=icon):
					self.assertTrue(_drawable(icon, names), f"no v16 sprite draws {icon!r}")


class ThePatchAgreesWithTheFilesTest(unittest.TestCase):
	def test_each_area_heals_to_its_workspace_json_icon(self):
		workspaces = _workspaces()
		for area, icon in patch.ICONS.items():
			with self.subTest(area=area):
				self.assertIn(area, workspaces)
				self.assertEqual(_json(workspaces[area])["icon"], icon)

	def test_each_area_heals_to_its_desk_tile_glyph(self):
		"""The rail and the /desk tile show the same picture. Knowledge Base's tile artwork was
		built from lucide's pre-16.50 name for the same book, so it is mapped across."""
		from erpnext_enhancements.setup.desktop_icon_map import TILES

		renamed = {"book-marked": "book-bookmark"}
		for area, icon in patch.ICONS.items():
			with self.subTest(area=area):
				glyph = TILES[area][1]
				self.assertEqual(icon, renamed.get(glyph, glyph))

	def test_every_heal_target_draws_and_every_broken_name_does_not(self):
		names = _names()
		self.assertEqual(sorted(set(patch.ICONS.values()) - names), [])
		self.assertEqual(sorted(set(patch.BROKEN) & names), [])

	def test_no_workspace_still_ships_a_broken_name(self):
		for path in _workspaces().values():
			with self.subTest(file=path.name):
				self.assertNotIn(_json(path).get("icon"), patch.BROKEN)

	def test_registered_post_model_sync(self):
		text = PATCHES_TXT.read_text(encoding="utf-8")
		post = text[text.index("[post_model_sync]") :]
		self.assertIn("\nerpnext_enhancements.patches.heal_desk_icon_names\n", post)


# --- the patch, against a stub -------------------------------------------------------------


def _reset(tables=("Workspace", "Sidebar", "Dock Item")):
	STATE.clear()
	STATE.update(
		{
			"tables": {doctype: [] for doctype in tables},
			"set_value": [],
			"commits": 0,
			"rollbacks": 0,
			"clear_cache": 0,
			"errors": [],
			"fail_get_all": set(),
		}
	)


def _row(doctype, **fields):
	fields.setdefault("name", f"row-{len(STATE['tables'][doctype]) + 1}")
	STATE["tables"][doctype].append(fields)


def _icon(doctype, name):
	field = "header_icon" if doctype == "Sidebar" else "icon"
	return next(row for row in STATE["tables"][doctype] if row["name"] == name)[field]


def _install_frappe_stub():
	frappe = types.ModuleType("frappe")

	def table_exists(doctype, cached=True):
		return doctype in STATE["tables"]

	def get_all(doctype, filters=None, fields=None):
		if doctype in STATE["fail_get_all"]:
			raise RuntimeError("database gone")
		[(field, (op, values))] = filters.items()
		assert op == "in", op
		return [
			{key: row.get(key) for key in fields}
			for row in STATE["tables"][doctype]
			if row.get(field) in values
		]

	def set_value(doctype, name, field, value, update_modified=True):
		STATE["set_value"].append((doctype, name, field, value, update_modified))
		for row in STATE["tables"][doctype]:
			if row["name"] == name:
				row[field] = value

	def commit():
		STATE["commits"] += 1

	def rollback():
		STATE["rollbacks"] += 1

	def clear_cache(*args, **kwargs):
		STATE["clear_cache"] += 1

	frappe.db = types.SimpleNamespace(
		table_exists=table_exists, set_value=set_value, commit=commit, rollback=rollback
	)
	frappe.get_all = get_all
	frappe.clear_cache = clear_cache
	frappe.log_error = lambda *a, **k: STATE["errors"].append(k.get("title") or a)
	sys.modules["frappe"] = frappe


def setUpModule():
	global patch
	_install_frappe_stub()
	sys.modules.pop("erpnext_enhancements.patches.heal_desk_icon_names", None)
	from erpnext_enhancements.patches import heal_desk_icon_names as p

	patch = p


def _production_2026_10_07():
	"""The rows prod held when the rail was found blank, plus neighbours that must survive."""
	_row("Workspace", name="QuickBooks Online", icon="money")
	_row("Workspace", name="Support Hub", icon="service")
	_row("Workspace", name="Knowledge Base", icon="book-marked")
	_row("Workspace", name="Projects", icon="project")
	_row("Sidebar", name="QuickBooks Online (Custom)", header_icon="money")
	_row("Sidebar", name="Devices", header_icon="mobile")
	_row("Sidebar", name="Water Engineering (Custom)", header_icon="water")
	_row("Dock Item", name="d1", link_type="Sidebar", link_to="QuickBooks Online (Custom)", icon="money")
	_row("Dock Item", name="d2", link_type="Sidebar", link_to="AI Governance (Custom)", icon="assistant")
	_row("Dock Item", name="d3", link_type="Sidebar", link_to="Finance Hub", icon="hammer")
	_row("Dock Item", name="d4", link_type="Workspace", link_to="Sapphire Maintenance", icon="service")


class HealsTest(unittest.TestCase):
	def setUp(self):
		_reset()
		_production_2026_10_07()

	def test_every_table_gets_the_areas_icon(self):
		patch.execute()
		self.assertEqual(_icon("Workspace", "QuickBooks Online"), "book-open-check")
		self.assertEqual(_icon("Workspace", "Support Hub"), "life-buoy")
		self.assertEqual(_icon("Workspace", "Knowledge Base"), "book-bookmark")
		self.assertEqual(_icon("Sidebar", "QuickBooks Online (Custom)"), "book-open-check")
		self.assertEqual(_icon("Sidebar", "Devices"), "smartphone")
		self.assertEqual(_icon("Sidebar", "Water Engineering (Custom)"), "droplets")
		self.assertEqual(_icon("Dock Item", "d1"), "book-open-check")
		self.assertEqual(_icon("Dock Item", "d2"), "bot")
		self.assertEqual(_icon("Dock Item", "d4"), "wrench")

	def test_a_row_with_a_working_icon_is_never_touched(self):
		"""Finance Hub's rail entry draws a hammer someone may have chosen; it is not broken."""
		patch.execute()
		self.assertEqual(_icon("Dock Item", "d3"), "hammer")
		self.assertEqual(_icon("Workspace", "Projects"), "project")
		touched = {(doctype, name) for doctype, name, *_ in STATE["set_value"]}
		self.assertNotIn(("Dock Item", "d3"), touched)
		self.assertNotIn(("Workspace", "Projects"), touched)

	def test_nothing_bumps_modified(self):
		patch.execute()
		self.assertEqual(len(STATE["set_value"]), 9)
		self.assertTrue(all(update_modified is False for *_, update_modified in STATE["set_value"]))

	def test_the_cache_is_cleared_once_after_a_change(self):
		patch.execute()
		self.assertEqual(STATE["clear_cache"], 1)
		self.assertEqual(STATE["commits"], 3)

	def test_a_second_run_changes_nothing(self):
		patch.execute()
		before = list(STATE["set_value"])
		patch.execute()
		self.assertEqual(STATE["set_value"], before)
		self.assertEqual(STATE["clear_cache"], 1)


class LeavesWhatIsNotOursTest(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_a_broken_name_on_someone_elses_row_is_left(self):
		_row("Sidebar", name="Lagoon Things", header_icon="money")
		_row("Dock Item", name="u1", link_type="URL", link_to=None, icon="water")
		_row("Workspace", name="QuickBooks Online Archive", icon="money")
		patch.execute()
		self.assertEqual(STATE["set_value"], [])
		self.assertEqual(STATE["clear_cache"], 0)

	def test_only_the_exact_conversion_suffix_names_an_area(self):
		self.assertEqual(patch.area("QuickBooks Online (Custom)"), "QuickBooks Online")
		self.assertEqual(patch.area("QuickBooks Online"), "QuickBooks Online")
		self.assertIsNone(patch.area("QuickBooks Online(Custom)"))
		self.assertIsNone(patch.area("QuickBooks Online (Custom) (Custom)"))
		self.assertIsNone(patch.area(None))
		self.assertIsNone(patch.area(""))


class NeverRaisesTest(unittest.TestCase):
	def setUp(self):
		_reset()
		_production_2026_10_07()

	def test_a_site_before_the_new_desk_has_no_sidebar_or_dock_tables(self):
		_reset(tables=("Workspace",))
		_row("Workspace", name="Water Engineering", icon="water")
		patch.execute()
		self.assertEqual(_icon("Workspace", "Water Engineering"), "droplets")
		self.assertEqual(STATE["errors"], [])

	def test_one_failing_table_does_not_stop_the_others(self):
		STATE["fail_get_all"].add("Sidebar")
		patch.execute()
		self.assertEqual(STATE["rollbacks"], 1)
		self.assertEqual(STATE["errors"], ["heal_desk_icon_names: Sidebar"])
		self.assertEqual(_icon("Sidebar", "Devices"), "mobile")
		self.assertEqual(_icon("Workspace", "QuickBooks Online"), "book-open-check")
		self.assertEqual(_icon("Dock Item", "d1"), "book-open-check")


if __name__ == "__main__":
	unittest.main()
