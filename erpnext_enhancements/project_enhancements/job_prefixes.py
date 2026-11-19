"""Job number prefixes: SRV- for new Service jobs, EVT- for new Events jobs (v1.831.0).

Weekly Operations Review, 2026-11-19. James said the company snapshot looks cluttered: 35 open
Service projects make the design/build backlog look bigger than it is. Nik's pick the same day:

* a new Project of type **Service** is numbered ``SRV-#####``;
* a new Project of type **Events** is numbered ``EVT-#####``;
* Design, Build and every other type keep ``PRJ-#####``; product orders keep their Sales Order
  numbers (they have no Project);
* **only new jobs.** No existing Project is ever renamed: the 35 open Service and 3 open Events
  projects keep their PRJ numbers.

How a Project gets its name
---------------------------
Not from its naming series. Production names every Project through one **Document Naming Rule**,
prefix ``PRJ-``, 5 digits, no conditions, priority 0 (counter 787 on 2026-11-19), which this app
seeds on a fresh site (``patches/seed_project_naming_rule.py``). There is no ``PRJ-`` row in
``tabSeries`` at all; the ``PRJ-.#####`` naming-series Property Setter is an inert fallback.

Frappe tries a doctype's enabled Document Naming Rules highest ``priority`` first, and the first
rule whose conditions all match the document names it (``set_naming_from_document_naming_rule`` and
``DocumentNamingRule.apply``, frappe v16.56.0). So the new prefixes are two more rules on Project,
each with a single condition ``project_type = <type>`` and priority :data:`PRIORITY`, above the
``PRJ-`` rule. A Service project matches the SRV- rule; a Build project matches neither condition
and falls through to ``PRJ-``. No code runs at naming time; ``patches/seed_job_prefix_naming_rules``
creates the two rules once.

The number sequence
-------------------
**Each prefix counts on its own**: the first new Service job is ``SRV-00001``, the first new Events
job ``EVT-00001``, and PRJ- carries on from 787. A Document Naming Rule keeps its own ``counter``
and locks that one row while it takes the next number, so this needs no code and cannot hand two
inserts the same name. Continuing the PRJ counter across all three (``SRV-00788``, ``PRJ-00789``)
would need a counter the three rules share, which Frappe does not have: it would mean replacing the
rules with our own naming code, and that code would have to get the locking right. The cost of the
simple choice: a bare number such as "12" is no longer unique to one job. Desk search shows every
job ending in that number, and ``SRV 12`` or ``EVT 12`` narrows it (``api/desk_search.py``).

When the prefix is decided
--------------------------
Once, at insert. ``Document.insert`` runs ``before_insert`` and then ``set_new_name``, before
``validate`` (frappe v16.56.0), so the type has to be on the Project by the end of ``before_insert``.
Every path that creates a Project sets it by then:

* **Closed Won hand-off** (``crm_enhancements.api.create_project_from_opportunity_background``):
  ``product_sale.project_type_for_deal`` picks the type from the deal's value streams, Design over
  Build over Service over Events, before the insert. A deal that is Service only becomes SRV-; one
  that is Design and Service stays PRJ-; a control-panel order (Products) stays PRJ-.
* **Service calls** (``sapphire_maintenance.service_calls``): inserted as Service, so SRV-.
* **Event rentals** (``asset_management.rental_planner``, "create project"): inserted as Events, so
  EVT-.
* **Fountain Move jobs**: ``fountain_move_jobs.apply_on_insert`` (``before_insert``) types a ticked
  move job Service, so SRV-.
* **Project templates**: :func:`fill_type_from_template` (``before_insert``, after the move-job hook)
  gives a Project with no type the type of the template it is made from. So a job made from
  *Service Project Template*, *Service Call* or *Fountain Move* is SRV-, one from *Rent Project
  Template* EVT-. It never overwrites a type somebody chose.
* **Desk Quick Entry and the full form**: the Project Type field (labelled "Project Stage") is in
  Quick Entry, so whatever is picked there is read at insert. Left blank, the job is PRJ-.
* **AI tools** (FAC ``create_document``, Triton): the values arrive with the insert, so a
  ``project_type`` in them is read the same way.

A Project whose type is set or changed **after** it was created keeps the name it was given. Nothing
renames a Project: a rename would orphan every link that names it (Drive folder, QuickBooks job,
visit numbers such as ``PRJ-00179-2026-V001``, people's own notes). The Projects Dashboard and the
reports group jobs by ``project_type``, never by the prefix, so a PRJ- Service job still counts as
Service everywhere.
"""

import frappe

#: (prefix, Project Type). The prefix ends in a hyphen, as the PRJ- rule's does.
RULES = (
	("SRV-", "Service"),
	("EVT-", "Events"),
)

#: The prefix every other Project gets, from the rule ``patches/seed_project_naming_rule`` seeds.
DEFAULT_PREFIX = "PRJ-"

#: Digits after the prefix, as the PRJ- rule has.
DIGITS = 5

#: Above the PRJ- rule's 0, so the conditional rules are tried first. Frappe sorts ``priority desc``.
PRIORITY = 10


def prefix_for(project_type):
	"""The prefix a Project inserted with ``project_type`` is numbered under: SRV-, EVT- or PRJ-."""
	wanted = (project_type or "").strip()
	for prefix, rule_type in RULES:
		if wanted == rule_type:
			return prefix
	return DEFAULT_PREFIX


def _log(title):
	try:
		frappe.log_error(title=title, message=frappe.get_traceback())
	except Exception:
		pass


def fill_type_from_template(doc, method=None, *args, **kwargs):
	"""``before_insert``: a Project with no type takes the type of the template it is made from.

	The type is read once, when the Project is named, to choose SRV-, EVT- or PRJ- (see the module
	docstring), and ERPNext never copies a template's type onto the Project. So a job made from
	*Service Project Template* with the type left blank was PRJ- with no type for good. Only fills a
	blank: a type somebody chose always stands, even if the template says otherwise.

	Never raises, so a template that cannot be read never stops a Project being created.
	"""
	try:
		if (doc.get("project_type") or "").strip():
			return
		template = (doc.get("project_template") or "").strip()
		if not template:
			return
		project_type = frappe.db.get_value("Project Template", template, "project_type")
		if project_type:
			doc.project_type = project_type
	except Exception:
		_log("Job prefixes: could not read the project template's type")
