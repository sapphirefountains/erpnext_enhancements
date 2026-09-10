# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The offsite backup schedule ships the files, and says so in the Log.

Bench-free and frappe-free. ``backup.py`` imports ``frappe`` at module scope, so
this suite reads its constants out of the **AST** rather than importing it -- the
same trick ``test_hook_targets_resolve`` uses, and the reason this can run on
every push instead of on the box at deploy time. unittest, not pytest, so it can
ride an existing ``python -m unittest`` step in ci.yml (a pytest-style suite
appended to a unittest module list is silently collected as nothing).

**The failure this exists to catch passes.** Whether a run ships the two file
archives is decided by one membership test::

    include_files = backup_type in FULL_TYPES

Drop the scheduled type out of ``FULL_TYPES`` -- by renaming it, by tidying the
tuple, by adding a second scheduled tier and forgetting one line -- and the
nightly run quietly goes back to being database-only. Nothing raises. The Offsite
Backup Log gets its green ``Success`` row, ``files_uploaded`` reads 1 instead of
3, the watchdog's *full* tier is satisfied because the scheduled type is still
what it queries, and the alert that exists for this never fires. The site keeps
being backed up every night, in the sense that mattered least; the first anyone
would learn of it is a restore that brings back every row and none of the
attachments, drawings or signed forms hanging off them. That is the exact shape
of the bug v1.385.0 fixed -- and that one was not a mistake in the code at
all, which is what makes it worth a test: the schedule did exactly what it said,
and what it said was wrong.

So: every type the scheduler can start must be a full type, and the Log must be
able to record every type that can be started.
"""

import ast
import io
import json
import os
import re
import unittest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKUP = os.path.join(APP_DIR, "offsite_backup", "backup.py")
HOOKS = os.path.join(APP_DIR, "hooks.py")
DOCTYPE_DIR = os.path.join(APP_DIR, "offsite_backup", "doctype")
LOG_JSON = os.path.join(DOCTYPE_DIR, "offsite_backup_log", "offsite_backup_log.json")
SETTINGS_JSON = os.path.join(DOCTYPE_DIR, "offsite_backup_settings", "offsite_backup_settings.json")

# Labels that exist only on rows written before the schedule became one nightly full
# backup: `Daily` was the database-only run, `Weekly` the Sunday full one. They must
# stay in the Log's Select options -- a Select rejects a value it does not list, and
# these rows are the archive's own history -- and must stay *out* of the types
# anything can start.
LEGACY_TYPES = ("Daily", "Weekly")


def _module_tuples(path):
	"""Module-level ``NAME = (...)`` string tuples, read without importing."""
	with io.open(path, encoding="utf-8") as handle:
		tree = ast.parse(handle.read(), filename=path)

	found = {}
	for node in tree.body:
		if not isinstance(node, ast.Assign) or len(node.targets) != 1:
			continue
		target = node.targets[0]
		if not isinstance(target, ast.Name):
			continue
		try:
			value = ast.literal_eval(node.value)
		except ValueError:
			# e.g. ALL_TYPES = SCHEDULED_TYPES + MANUAL_TYPES -- resolved below.
			continue
		if isinstance(value, tuple) and all(isinstance(item, str) for item in value):
			found[target.id] = value
	return found


def _select_options(doctype_json, fieldname):
	with io.open(doctype_json, encoding="utf-8") as handle:
		doc = json.load(handle)
	for field in doc["fields"]:
		if field.get("fieldname") == fieldname:
			return tuple((field.get("options") or "").split("\n"))
	raise AssertionError(f"{fieldname} is not a field on {doctype_json}")


def _field_default(doctype_json, fieldname):
	with io.open(doctype_json, encoding="utf-8") as handle:
		doc = json.load(handle)
	for field in doc["fields"]:
		if field.get("fieldname") == fieldname:
			return field.get("default")
	raise AssertionError(f"{fieldname} is not a field on {doctype_json}")


class TestOffsiteBackupSchedule(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.types = _module_tuples(BACKUP)
		cls.scheduled = cls.types["SCHEDULED_TYPES"]
		cls.full = cls.types["FULL_TYPES"]
		cls.manual = cls.types["MANUAL_TYPES"]
		# ALL_TYPES is an expression, not a literal, so it is reconstructed the way
		# the module builds it rather than re-parsed.
		cls.all_types = cls.scheduled + cls.manual
		with io.open(BACKUP, encoding="utf-8") as handle:
			cls.backup_source = handle.read()
		with io.open(HOOKS, encoding="utf-8") as handle:
			cls.hooks_source = handle.read()

	# ------------------------------------------------------- the load-bearing one

	def test_every_scheduled_run_is_a_full_run(self):
		"""No automatic run may ship the database alone.

		The whole point of the 02:00 job is to keep something a restore can be
		performed from. A database dump without the file archives satisfies every
		signal this module emits and none of that purpose.
		"""
		self.assertTrue(self.scheduled, "SCHEDULED_TYPES is empty -- nothing is backed up on a schedule.")
		for backup_type in self.scheduled:
			self.assertIn(
				backup_type,
				self.full,
				f"{backup_type!r} is scheduled but not in FULL_TYPES, so the automatic run would "
				"ship the database without the public and private file archives -- and would log a "
				"green Success row while doing it.",
			)

	def test_include_files_is_still_decided_by_full_types(self):
		"""The assertion above is only worth anything while this is how it is decided."""
		self.assertIn(
			"include_files = backup_type in FULL_TYPES",
			self.backup_source,
			"execute_backup no longer decides include_files from FULL_TYPES, so "
			"test_every_scheduled_run_is_a_full_run is now checking nothing.",
		)

	# ------------------------------------------------------------- the wiring

	def test_hooks_schedules_exactly_the_scheduled_types(self):
		"""One cron dump job, and it is the nightly one.

		A second scheduled dump is not a harmless duplicate: the guard in
		``_schedule`` logs a Skipped row rather than queueing, so whichever job
		loses the race simply does not happen.
		"""
		dump_jobs = re.findall(
			r'"(?P<cron>[^"]+)": \["erpnext_enhancements\.offsite_backup\.backup\.(?P<fn>run_\w+)"\]',
			self.hooks_source,
		)
		dump_jobs = [(cron, fn) for cron, fn in dump_jobs if fn != "watchdog"]
		self.assertEqual(
			dump_jobs,
			[("0 2 * * *", "run_nightly_backup")],
			"hooks.py should register exactly one offsite backup dump job: run_nightly_backup at "
			"02:00. Found: " + repr(dump_jobs),
		)

	def test_watchdog_still_runs(self):
		self.assertIn(
			'"0 8 * * *": ["erpnext_enhancements.offsite_backup.backup.watchdog"]',
			self.hooks_source,
			"The staleness watchdog is the only check that catches nothing running at all.",
		)

	def test_log_can_record_every_type_that_can_be_started(self):
		"""A Select rejects a value it does not list, and _start_log runs first.

		If a startable type is missing from the options the run cannot even insert
		its own Running row -- which is also the concurrency guard, so the failure
		is not confined to the run that hit it.
		"""
		options = _select_options(LOG_JSON, "backup_type")
		for backup_type in self.all_types:
			self.assertIn(
				backup_type, options, f"{backup_type!r} can be started but the Log cannot record it."
			)

	def test_legacy_types_stay_listed_and_stay_unstartable(self):
		options = _select_options(LOG_JSON, "backup_type")
		for backup_type in LEGACY_TYPES:
			self.assertIn(
				backup_type,
				options,
				f"{backup_type!r} is dropped from the Log's options, so the rows already written "
				"under it can no longer be saved.",
			)
			self.assertNotIn(
				backup_type,
				self.all_types,
				f"{backup_type!r} is a historical label. Starting a new run under it would "
				"retroactively re-describe what those old rows mean.",
			)

	def test_weekly_remains_a_full_type_for_the_watchdog(self):
		"""The Sunday runs that did happen were genuinely full backups.

		Dropping ``Weekly`` from FULL_TYPES would make the watchdog's full tier read
		"no full backup has ever completed successfully" on the first morning after
		a deploy, before the first Nightly row exists.
		"""
		self.assertIn("Weekly", self.full)

	# ------------------------------------------------------- thresholds agree

	def test_watchdog_fallback_matches_the_shipped_default(self):
		"""The code's fallback and the form's default must be the same number.

		They are read by different people -- the fallback when the field is empty,
		the default by whoever opens the form -- and a disagreement between them is
		how a dial stops being trusted.
		"""
		pairs = (
			("alert_if_older_than_hours", "cint(settings.alert_if_older_than_hours) or "),
			("alert_if_full_older_than_hours", "cint(settings.alert_if_full_older_than_hours) or "),
		)
		for fieldname, prefix in pairs:
			match = re.search(re.escape(prefix) + r"(\d+)", self.backup_source)
			self.assertIsNotNone(match, f"No fallback found for {fieldname}.")
			self.assertEqual(
				match.group(1),
				str(_field_default(SETTINGS_JSON, fieldname)),
				f"{fieldname}: backup.py falls back to {match.group(1)} but the form ships "
				f"{_field_default(SETTINGS_JSON, fieldname)}.",
			)

	def test_min_keep_default_covers_a_week_of_nights(self):
		"""min_keep counts OBJECTS, and a full run uploads three of them.

		The floor is expressed in the wrong unit for the thing it protects, which is
		why it silently shrank from a fortnight to four and a half nights when the
		schedule went nightly-full.
		"""
		artefacts_per_full_run = 3
		min_keep = int(_field_default(SETTINGS_JSON, "min_keep"))
		self.assertGreaterEqual(
			min_keep,
			7 * artefacts_per_full_run,
			f"min_keep ships at {min_keep} objects, which is fewer than seven nights of full "
			f"backups ({7 * artefacts_per_full_run} objects).",
		)


if __name__ == "__main__":
	unittest.main()
