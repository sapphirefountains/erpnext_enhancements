"""Seed the SRV- and EVT- Document Naming Rules for new Service and Events jobs (v1.831.0).

Nik, 2026-11-19: new Service jobs are numbered ``SRV-#####``, new Events jobs ``EVT-#####``, and
everything else keeps ``PRJ-#####``. Only new jobs: no Project is renamed. Why a Document Naming Rule,
how the rules are tried, and why each prefix counts on its own are in
``project_enhancements/job_prefixes.py``.

Per rule in :data:`job_prefixes.RULES` this creates, only when Project has no rule with that prefix
yet (enabled or disabled, so a rule somebody switched off stays off):

* ``document_type`` Project, ``prefix`` ``SRV-`` or ``EVT-``, 5 digits;
* one condition, ``project_type = Service`` or ``= Events``;
* ``priority`` 10, above the unconditional ``PRJ-`` rule's 0;
* ``counter`` after the highest existing Project already carrying that prefix, so a re-created rule
  can never hand out a name that exists. On production there are none, so the first is ``00001``.

What it never does: touch the ``PRJ-`` rule or its counter, rename a Project, or change any
Project's type. A Document Naming Rule is a record with a live counter, so it is seeded here and
not shipped as a fixture: a fixture would put the counter back on every migrate and hand out names
that already exist (the reason ``seed_project_naming_rule`` is a patch too).

Cannot raise: a failure on one rule writes one Error Log row naming the prefix and moves on, because
a patch that raises aborts ``bench migrate``, which on this repo is the deploy. Safe twice.
"""

import frappe

from erpnext_enhancements.project_enhancements import job_prefixes


def _highest_existing(prefix):
	"""The largest number already used after ``prefix`` on a Project, or 0."""
	row = frappe.db.sql(
		"SELECT MAX(CAST(SUBSTRING(name, %(start)s) AS UNSIGNED)) FROM `tabProject` WHERE name LIKE %(pattern)s",
		{"start": len(prefix) + 1, "pattern": prefix + "%"},
	)
	return int((row and row[0] and row[0][0]) or 0)


def seed_rule(prefix, project_type):
	"""Create the rule for one prefix. Returns ``"created"`` or ``"exists"``; raises on a failure."""
	if frappe.db.exists("Document Naming Rule", {"document_type": "Project", "prefix": prefix}):
		return "exists"
	rule = frappe.new_doc("Document Naming Rule")
	rule.document_type = "Project"
	rule.prefix = prefix
	rule.prefix_digits = job_prefixes.DIGITS
	rule.priority = job_prefixes.PRIORITY
	rule.disabled = 0
	rule.counter = _highest_existing(prefix)
	rule.append("conditions", {"field": "project_type", "condition": "=", "value": project_type})
	rule.insert(ignore_permissions=True)
	return "created"


def execute():
	for prefix, project_type in job_prefixes.RULES:
		try:
			result = seed_rule(prefix, project_type)
			frappe.logger().info(f"seed_job_prefix_naming_rules: {prefix} {result}")
		except Exception:
			try:
				frappe.log_error(
					title=f"Job prefixes: could not seed the {prefix} naming rule",
					message=frappe.get_traceback(),
				)
			except Exception:
				pass
