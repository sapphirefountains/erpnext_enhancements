# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Knowledge Base Integrity (WI-080 PR 4): what only a write past the Knowledge Base's own code can
break. Zero rows on a healthy site.

Nobody holds write on an article or submit on a version, and the controllers refuse every write the
Knowledge Base's own code has not announced (``flags.kb_action``, ``flags.kb_publish``). The one
thing none of that stops is a System Manager writing past the ORM: ``frappe.db.set_value``, raw
SQL, or a batch-approved ``run_python_code`` card. This report is how that shows. The checks are
``reporting.integrity_problems`` (pure, and listed in its module docstring); this controller only
reads the rows.

**Why a Script Report.** The AI gate refuses any SQL that names ``Knowledge Article Version`` over
MCP (``assistant_tools/_gate.py``, ``DENYLIST_DOCTYPES``), including an operator's own integrity
query (ADR 0017), so the query lives here, where it runs as the signed-in KB Approver.

**Who.** KB Approver only (the report JSON's roles). ``ref_doctype`` is Knowledge Article Version,
so v16's ``get_report_doc`` also requires ``report`` permission on it (``desk/query_report.py:43-53``),
which only the two KB roles hold: a System Manager without a KB role who runs it is refused twice.
Both checks are against the *session* user, and an Auto Email Report's scheduled send runs as
Administrator, who holds every role, so v16 alone would email these rows for anyone who may create
one (Report Manager included). ``emailed_reports.guard_auto_email_report`` refuses that at save
unless the person saving it and the user it runs as are both KB Approvers.

**What it reads, and what it never reads.** Four bound queries:

* every article (:data:`reporting.INTEGRITY_ARTICLE_FIELDS`), its text included, to hash it: the
  article is published text;
* every version's **metadata** (:data:`reporting.INTEGRITY_VERSION_FIELDS`): no content field, so
  a draft's text is never selected;
* the **approved** text of the live versions only (``docstatus = 1``), to hash it;
* the Files attached to either doctype that are public, by name and attachment only.

No row quotes any of that text (see ``reporting``). ``prepared_report`` is off and its automation
disabled in the JSON, so a slow run is never turned into a background job whose result is stored as
a File, and never queued on the redis a deploy flushes.

**Through an AI tool.** FAC's ``generate_report`` runs a report through v16's
``frappe.desk.query_report.run``, which applies the role list and the ``ref_doctype`` check above,
so an assistant can run this only for a KB Approver, and what it gets back is names, numbers,
states, user ids and the rule broken. That is deliberate and not refused by the gate: this report
is the operators' integrity check, and a KB Approver asking an assistant whether the knowledge base
is healthy is the use it exists for. See the README.
"""

import frappe
from frappe import _

from erpnext_enhancements.knowledge_base import constants, reporting

ARTICLES_SQL = (
	"select " + ", ".join(reporting.INTEGRITY_ARTICLE_FIELDS) + " from `tabKnowledge Article` order by name"
)

#: Metadata only: states, numbers and user ids. The test pins that no content field is selected.
VERSIONS_SQL = (
	"select "
	+ ", ".join(reporting.INTEGRITY_VERSION_FIELDS)
	+ " from `tabKnowledge Article Version` order by name"
)

#: The approved text of the live versions, and only of submitted ones: a draft is never selected.
APPROVED_TEXT_SQL = (
	"select "
	+ ", ".join(reporting.APPROVED_TEXT_FIELDS)
	+ " from `tabKnowledge Article Version` where docstatus = 1 and name in %(names)s"
)

PUBLIC_FILES_SQL = (
	"select "
	+ ", ".join(reporting.PUBLIC_FILE_FIELDS)
	+ " from `tabFile` where attached_to_doctype in %(doctypes)s and ifnull(is_private, 0) = 0 order by name"
)


def execute(filters=None):
	articles = frappe.db.sql(ARTICLES_SQL, as_dict=True)
	versions = frappe.db.sql(VERSIONS_SQL, as_dict=True)
	live = tuple(sorted({row.get("live_version") for row in articles if row.get("live_version")}))
	approved = {}
	if live:
		for row in frappe.db.sql(APPROVED_TEXT_SQL, {"names": live}, as_dict=True):
			approved[row.get("name")] = row
	files = frappe.db.sql(PUBLIC_FILES_SQL, {"doctypes": tuple(sorted(constants.KB_DOCTYPES))}, as_dict=True)
	problems = reporting.integrity_problems(articles, versions, approved, files)
	if problems:
		message = _(
			"{0} problem(s) found. Each is something only a change made outside the knowledge base's "
			"own actions can cause."
		).format(len(problems))
	else:
		message = _(
			"No problems found in {0} article(s) and {1} version(s): every article matches the version "
			"that was approved, and no knowledge-base file is public."
		).format(len(articles), len(versions))
	return get_columns(), problems, message


def get_columns():
	return [
		{"fieldname": "check", "label": _("Check"), "fieldtype": "Data", "width": 140},
		{
			"fieldname": "article",
			"label": _("Article"),
			"fieldtype": "Link",
			"options": constants.ARTICLE_DOCTYPE,
			"width": 110,
		},
		{
			"fieldname": "version",
			"label": _("Version"),
			"fieldtype": "Link",
			"options": constants.VERSION_DOCTYPE,
			"width": 120,
		},
		{"fieldname": "detail", "label": _("Problem"), "fieldtype": "Data", "width": 640},
	]
