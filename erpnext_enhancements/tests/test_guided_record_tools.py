"""Bench-free tests for the guided page's record tools (v1.686.0).

Nik, 2026-10-14: "We shouldn't have lost any fidelity or information on any of the UI/UX changes."
The guided page (and the board's side panel, which runs the same editor) replaced the Desk form and
with it the form's sidebar, timeline, dashboard and toolbar. ``public/js/form_kit/ui/record_tools.js``
brings them back for any saved record of any guided DocType; its decisions are the pure module
``public/js/form_kit/record_tools.js`` and the directory rules it shares with the Desk widget,
``public/js/global_enhancements/party_sources.js``. Those run here under node (copied beside a
``package.json`` that says ``"type": "module"``, as test_guided_entry does). The server half,
``form_kit/record_info.py`` behind ``form_kit.api.record_info``, runs against a frappe stub. The rest
is static: the shells draw the section, the editor mounts it, nothing singles out a DocType by name,
every request is sf_kit's call, every dialog joins the history guard, and the Desk form's own buttons
call the same shared functions instead of a second copy.

Run: python -m unittest erpnext_enhancements.tests.test_guided_record_tools -v
"""

import ast
import json
import re
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

APP = REPO_ROOT / "erpnext_enhancements"
JS = APP / "public" / "js"
FORM_KIT_JS = JS / "form_kit"
RULES_JS = FORM_KIT_JS / "record_tools.js"
UI_JS = FORM_KIT_JS / "ui" / "record_tools.js"
EDITOR_JS = FORM_KIT_JS / "ui" / "editor.js"
SIDE_PANEL_JS = FORM_KIT_JS / "ui" / "side_panel.js"
PAGE_JS = APP / "enhancements_core" / "page" / "guided_entry" / "guided_entry.js"
CSS = APP / "public" / "css" / "guided_entry.bundle.css"
SOURCES_JS = JS / "global_enhancements" / "party_sources.js"
ACTIONS_JS = JS / "global_enhancements" / "record_actions.js"
CONTROLLER_JS = JS / "global_enhancements" / "unified_tab_controller.js"
DESK_BUNDLE = JS / "erpnext_enhancements.bundle.js"
API_PY = APP / "form_kit" / "api.py"
INFO_PY = APP / "form_kit" / "record_info.py"


def _text(path):
	return path.read_text(encoding="utf-8")


def _code(path):
	"""The script with comments stripped: an absence check must not be satisfied by its comment."""
	src = re.sub(r"/\*[\s\S]*?\*/", "", _text(path))
	return "\n".join(line for line in src.splitlines() if not line.strip().startswith("//"))


RUNNER = r"""
import { pathToFileURL } from "node:url";
const [dir] = process.argv.slice(2);
const t = await import(pathToFileURL(dir + "/form_kit/record_tools.js").href);
const kit = await import(pathToFileURL(dir + "/form_kit/index.js").href);
const ps = await import(pathToFileURL(dir + "/global_enhancements/party_sources.js").href);
const out = {};
const opp = t.tools_config("Opportunity");
const yes = () => true;
const labels = (items) => items.map((i) => i.label);

out.index_exports = ["create_items", "describe_version", "activity_feed", "connection_filters"].every((k) => typeof kit[k] === "function");
out.create = {
	open_lead: labels(t.create_items(opp, { status: "Open", opportunity_from: "Lead" }, yes)),
	open_customer: labels(t.create_items(opp, { status: "Open", opportunity_from: "Customer" }, yes)),
	lost: labels(t.create_items(opp, { status: "Lost", opportunity_from: "Lead" }, yes)),
	no_quotation_right: labels(t.create_items(opp, { status: "Open", opportunity_from: "Lead" }, (d) => d !== "Quotation")),
	unconfigured: labels(t.create_items(t.tools_config("Project"), {}, yes)),
	methods: t.create_items(opp, { status: "Open" }, yes).map((i) => i.method),
};
// The real path: the editor's values hold only flow fields (contact_email is hidden on the form, so
// in no step); record_info's `emails` fill them in.
const flow_values = { custom_opportunity_name: "Plaza fountain", contact_email: null };
const comms = [
	{ type: "Automated Message", medium: "Email", subject: "Reminder", sender: "bot@example.com" },
	{ type: "Communication", medium: "Phone", subject: "Call", sender: "x@example.com" },
	{ type: "Communication", medium: "Email", subject: "Quote for the plaza", sender: "client@example.com" },
	{ type: "Communication", medium: "Email", subject: "Older", sender: "old@example.com" },
];
out.recipient = {
	flow_only: t.recipient_for(opp, flow_values),
	from_info: t.recipient_for(opp, t.with_emails(flow_values, { contact_email: "a@example.com", primary_contact_email: "p@example.com" })),
	fallback: t.recipient_for(opp, t.with_emails(flow_values, { contact_email: "", primary_contact_email: "p@example.com" })),
	typed_wins: t.recipient_for(opp, t.with_emails({ contact_email: "typed@example.com" }, { contact_email: "a@example.com" })),
	other: t.recipient_for({}, { email_id: "b@example.com" }),
	other_email: t.recipient_for({}, { email: "c@example.com" }),
	none: t.recipient_for({}, {}),
};
out.start = {
	with_email: t.email_start(opp, t.with_emails(flow_values, { contact_email: "a@example.com" }), comms, "Plaza fountain", "CRM-OPP-1"),
	no_address: t.email_start(opp, flow_values, comms, "Plaza fountain", "CRM-OPP-1"),
	already_re: t.email_start(opp, {}, [{ type: "Communication", medium: "Email", subject: "RE: Quote", sender: "c@example.com" }], "T", "CRM-OPP-1"),
	no_email_yet: t.email_start(opp, t.with_emails(flow_values, { contact_email: "a@example.com" }), comms.slice(0, 2), "Plaza fountain", "CRM-OPP-1"),
};
out.subject = [t.email_subject("Plaza fountain", "CRM-OPP-1"), t.email_subject("", "CRM-OPP-1"), t.email_subject("About #CRM-OPP-1", "CRM-OPP-1")];
out.files = {
	url_plain: t.file_url({ file_url: "/files/a b#1.pdf" }),
	url_web: t.file_url({ file_url: "https://example.com/x#y" }),
	url_name: t.file_url({ file_name: "files/x.png" }),
	url_bare: t.file_url({ file_name: "x.png" }),
	unique: t.unique_files([{ name: "1", file_name: "a.pdf" }, { name: "2", file_name: "a.pdf" }, { name: "3", file_name: "b.pdf" }]).map((f) => f.name),
	sizes: [0, 512, 2048, 1536000, 25 * 1024 * 1024, 3 * 1024 * 1024 * 1024].map(t.size_text),
	kinds: [{ file_type: "pdf" }, { file_name: "photo.JPG" }, { file_url: "https://x.y/z" }, { file_name: "noext" }].map(t.kind_text),
	remove: [
		t.can_remove_files({ write: 1 }),
		t.can_remove_files({ write: 0 }),
		t.can_remove_files({ protect_attached_files: 1, docstatus: 0, write: 1 }),
		t.can_remove_files({ protect_attached_files: 1, docstatus: 1, write: 1, delete: 1 }),
		t.can_remove_files({ protect_attached_files: 1, docstatus: 2, write: 1, delete: 0 }),
		t.can_remove_files({ protect_attached_files: 1, docstatus: 2, write: 1, delete: 1 }),
	],
	add: [
		t.can_add_file({ write: 1 }),
		t.can_add_file({ write: 0 }),
		t.can_add_file({ write: 1, max_attachments: 2, count: 1 }),
		t.can_add_file({ write: 1, max_attachments: 2, count: 2 }),
	],
};
const label = (f) => ({ status: "Status", expected_closing: "Close Date", items: "Items" })[f] || null;
const child = (table, f) => (table === "items" && f === "qty" ? "Quantity" : null);
out.version = {
	changed: t.describe_version(JSON.stringify({ changed: [["status", "Open", "Quotation"], ["secret", "a", "b"], ["expected_closing", null, "2026-10-14"]] }), label, child),
	capped: t.describe_version({ changed: [["status", 1, 2], ["status", 2, 3], ["status", 3, 4], ["status", 4, 5]] }, label, child)[0].parts.length,
	hidden_only: t.describe_version({ changed: [["secret", "a", "b"]] }, label, child),
	rows: t.describe_version({ row_changed: [["items", 0, "x", [["qty", 1, 2], ["rate", 1, 2]]]] }, label, child),
	added: t.describe_version({ added: [["items", {}], ["items", {}], ["hidden_table", {}]] }, label, child),
	docstatus: t.describe_version({ changed: [["docstatus", 0, 1]] }, label, child),
	comment: t.describe_version({ comment: "Imported <b>value</b>" }, label, child),
	bad: t.describe_version("{not json", label, child),
	long_value: t.version_value("<p>" + "x".repeat(60) + "</p>"),
	empty_value: t.version_value(null),
	html_value: t.version_value("<b>a</b> &amp; b"),
};
out.feed = t.activity_feed(
	{
		created: { at: "2026-10-01 09:00:00", by: "u1", by_name: "One" },
		modified: { at: "2026-10-14 10:00:00", by: "u2", by_name: "Two" },
		versions: [{ name: "V1", at: "2026-10-10 08:00:00", owner: "u2", owner_name: "Two", data: JSON.stringify({ changed: [["status", "Open", "Lost"]] }) }],
		communications: [{ name: "C1", at: "2026-10-12 08:00:00", sender: "x@example.com", sender_name: "X" }],
		logs: [{ kind: "attachment", at: "2026-10-11 08:00:00", owner: "u1", owner_name: "One", text: "a.pdf" }],
		milestones: [{ at: "2026-10-09 08:00:00", owner: "u1", owner_name: "One", track_field: "status", value: "Open" }],
	},
	(data) => t.describe_version(data, label, child)
).map((i) => i.kind);
out.tags = [t.clean_tag("  big,  job "), t.clean_tag("")];
const dashboard = {
	fieldname: "opportunity",
	transactions: [{ items: ["Quotation", "Request for Quotation", "Supplier Quotation"] }, { label: "Travel", items: ["Travel Trip"] }, { label: "Hidden", items: ["Secret"] }],
	non_standard_fieldnames: { "Travel Trip": "travel_for_name" },
	dynamic_links: { travel_for_name: ["Opportunity", "travel_for"] },
};
const groups = t.connection_groups(dashboard, (d) => d !== "Secret" && d !== "Supplier Quotation");
out.connections = {
	groups,
	doctypes: t.connection_doctypes(groups),
	counts: t.connection_counts({ external_links_found: [{ doctype: "Quotation", count: 2, open_count: 1 }], internal_links_found: [{ doctype: "Project", count: 1, names: ["PROJ-1"] }] }),
	filter_plain: t.connection_filters(dashboard, "Quotation", "CRM-OPP-1", { count: 2 }),
	filter_dynamic: t.connection_filters(dashboard, "Travel Trip", "CRM-OPP-1", {}),
	filter_names: t.connection_filters(dashboard, "Project", "CRM-OPP-1", { names: ["PROJ-1"], internal: true }),
	filter_internal_none: t.connection_filters(dashboard, "Project", "CRM-OPP-1", { names: [], internal: true }),
	filter_no_field: t.connection_filters({}, "Quotation", "X", {}),
};
out.print = [
	t.can_print({ print: 1 }),
	t.can_print({ print: 0 }),
	t.can_print({ print: 1, issingle: 1 }),
	t.can_print({ print: 1, is_submittable: 1, docstatus: 0 }),
	t.can_print({ print: 1, is_submittable: 1, docstatus: 0, allow_print_for_draft: 1 }),
	t.can_print({ print: 1, is_submittable: 1, docstatus: 1 }),
	t.can_print({ print: 1, is_submittable: 1, docstatus: 2 }),
];
out.widgets = [t.directory_widgets(["contact_list_html", "address_list_html"]), t.directory_widgets(["contact_list_html"]), t.directory_widgets([])];
out.sources = {
	opp: ps.party_sources("Opportunity", { name: "CRM-OPP-1", opportunity_from: "Lead", party_name: "LEAD-1" }, []),
	project: ps.party_sources("Project", { name: "PROJ-1", customer: "Acme", custom_opportunity: "CRM-OPP-1", stakeholders: [{ supplier: "Acme" }, { link_doctype: "Customer", link_name: "Acme" }] }, [{ fieldname: "stakeholders", fieldtype: "Table" }]),
	not_array: ps.party_sources("Project", { name: "P", stakeholders: "oops" }, [{ fieldname: "stakeholders", fieldtype: "Table" }]),
	address_field: [
		ps.primary_address_field([{ fieldname: "customer_primary_address" }]),
		ps.primary_address_field([{ fieldname: "primary_address", fieldtype: "Link" }]),
		ps.primary_address_field([{ fieldname: "primary_address", fieldtype: "Text" }]),
	],
	address_name: ps.primary_address_name({ customer_address: "ADDR-2" }, [{ fieldname: "primary_address", fieldtype: "Link" }]),
	party: [ps.form_party("Opportunity", { name: "O", opportunity_from: "Prospect", party_name: "P" }), ps.form_party("Project", { name: "P", customer: "C" }), ps.form_party("Customer", { name: "C" }), ps.form_party("Opportunity", { opportunity_from: "Lead", party_name: "L" })],
	account: [ps.is_account_doctype("Customer"), ps.is_account_doctype("Opportunity")],
};
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(shutil.which("node"), "node is not on PATH")
class TestTheDecisionsUnderNode(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		node = shutil.which("node")
		with tempfile.TemporaryDirectory() as tmp:
			folder = Path(tmp)
			(folder / "package.json").write_text('{"type": "module"}', encoding="utf-8")
			shutil.copytree(FORM_KIT_JS, folder / "form_kit")
			(folder / "global_enhancements").mkdir()
			shutil.copy(SOURCES_JS, folder / "global_enhancements" / "party_sources.js")
			(folder / "runner.mjs").write_text(RUNNER, encoding="utf-8")
			proc = subprocess.run(
				[node, str(folder / "runner.mjs"), str(folder)],
				capture_output=True,
				text=True,
				encoding="utf-8",
				timeout=60,
			)
		if proc.returncode != 0:
			raise AssertionError(f"node failed:\n{proc.stderr}")
		cls.out = json.loads(proc.stdout)

	def test_the_kit_exports_them(self):
		self.assertTrue(self.out["index_exports"])

	def test_the_create_menu_is_erpnexts_own(self):
		c = self.out["create"]
		# ERPNext v16 opportunity.js refresh(): SQ, RFQ, Customer (unless one), Quotation; none when Lost.
		self.assertEqual(
			c["open_lead"], ["Supplier Quotation", "Request For Quotation", "Customer", "Quotation"]
		)
		self.assertEqual(c["open_customer"], ["Supplier Quotation", "Request For Quotation", "Quotation"])
		self.assertEqual(c["lost"], [])
		self.assertNotIn("Quotation", c["no_quotation_right"])
		self.assertEqual(c["unconfigured"], [])
		for method in c["methods"]:
			self.assertTrue(method.startswith("erpnext.crm.doctype.opportunity.opportunity.make_"), method)

	def test_a_new_email_starts_as_the_form_s(self):
		r = self.out["recipient"]
		# The flow alone never has the address: that was the empty To.
		self.assertEqual(r["flow_only"], "")
		self.assertEqual(r["from_info"], "a@example.com")
		self.assertEqual(r["fallback"], "p@example.com")
		self.assertEqual(r["typed_wins"], "typed@example.com")
		self.assertEqual(r["other"], "b@example.com")
		self.assertEqual(r["other_email"], "c@example.com")
		self.assertEqual(r["none"], "")
		s = self.out["start"]
		# frappe v16 setup_subject_and_recipients: To the email field; "Re:" the latest email.
		self.assertEqual(
			s["with_email"],
			{"recipients": "a@example.com", "subject": "Re: Quote for the plaza (#CRM-OPP-1)"},
		)
		# No address on the record: To the latest email's sender.
		self.assertEqual(s["no_address"]["recipients"], "client@example.com")
		self.assertEqual(s["already_re"]["subject"], "RE: Quote (#CRM-OPP-1)")
		self.assertEqual(
			s["no_email_yet"], {"recipients": "a@example.com", "subject": "Plaza fountain (#CRM-OPP-1)"}
		)
		self.assertEqual(
			self.out["subject"],
			["Plaza fountain (#CRM-OPP-1)", "CRM-OPP-1 (#CRM-OPP-1)", "About #CRM-OPP-1"],
		)

	def test_the_ui_fills_the_email_fields_from_record_info(self):
		code = _code(UI_JS)
		self.assertIn("return rules.with_emails(this.ed.values, this.info && this.info.emails);", code)
		self.assertIn("rules.email_start(this.config, values,", code)

	def test_reply_quotes_the_email_in_full(self):
		code = _code(UI_JS)
		body = code[code.index("new_email(reply) {") :]
		body = body[: body.index("\n\t}\n")]
		self.assertIn('"frappe.desk.form.load.get_communications"', body)
		self.assertIn("frappe.dom.remove_script_and_style(found.content)", body)
		self.assertIn("if (asked_here()) open(cut);", body)
		self.assertIn("asked_here() ? new frappe.views.CommunicationComposer(opts) : null", body)

	def test_attachments_follow_the_sidebar(self):
		f = self.out["files"]
		self.assertEqual(f["url_plain"], "/files/a%20b%231.pdf")
		self.assertEqual(f["url_web"], "https://example.com/x#y")
		self.assertEqual(f["url_name"], "/files/x.png")
		self.assertEqual(f["url_bare"], "/files/x.png")
		self.assertEqual(f["unique"], ["1", "3"])
		self.assertEqual(f["sizes"], ["", "512 B", "2 KB", "1.5 MB", "25 MB", "3 GB"])
		self.assertEqual(f["kinds"], ["PDF", "JPG", "Link", ""])
		self.assertEqual(f["remove"], [True, False, True, False, False, True])
		self.assertEqual(f["add"], [True, False, True, False])

	def test_versions_read_as_the_timeline_reads_them(self):
		v = self.out["version"]
		self.assertEqual(
			v["changed"],
			[
				{
					"kind": "changed",
					"parts": [
						{"label": "Status", "from": "Open", "to": "Quotation"},
						{"label": "Close Date", "from": '""', "to": "2026-10-14"},
					],
				}
			],
		)
		self.assertEqual(v["capped"], 3)
		# A field the person may not see is never named, and a version naming only those is left out.
		self.assertEqual(v["hidden_only"], [])
		self.assertEqual(
			v["rows"],
			[{"kind": "rows_changed", "parts": [{"label": "Quantity", "from": "1", "to": "2", "row": 1}]}],
		)
		self.assertEqual(v["added"], [{"kind": "added", "tables": [{"label": "Items", "count": 2}]}])
		self.assertEqual(v["docstatus"], [{"kind": "submitted"}])
		self.assertEqual(v["comment"], [{"kind": "comment", "text": "Imported value"}])
		self.assertEqual(v["bad"], [])
		self.assertEqual(v["long_value"], "x" * 40 + "…")
		self.assertEqual(v["empty_value"], '""')
		self.assertEqual(v["html_value"], "a & b")

	def test_the_feed_is_newest_first(self):
		self.assertEqual(
			self.out["feed"], ["edited", "communication", "log", "version", "milestone", "created"]
		)

	def test_tags(self):
		self.assertEqual(self.out["tags"], ["big job", ""])

	def test_connections_follow_the_dashboard(self):
		c = self.out["connections"]
		self.assertEqual(
			c["groups"],
			[
				{"label": "", "items": ["Quotation", "Request for Quotation"]},
				{"label": "Travel", "items": ["Travel Trip"]},
			],
		)
		self.assertEqual(c["doctypes"], ["Quotation", "Request for Quotation", "Travel Trip"])
		self.assertEqual(
			c["counts"]["Quotation"], {"count": 2, "open_count": 1, "names": [], "internal": False}
		)
		self.assertTrue(c["counts"]["Project"]["internal"])
		self.assertEqual(c["filter_plain"], {"opportunity": "CRM-OPP-1"})
		self.assertEqual(c["filter_dynamic"], {"travel_for": "Opportunity", "travel_for_name": "CRM-OPP-1"})
		self.assertEqual(c["filter_names"], {"name": ["in", ["PROJ-1"]]})
		self.assertIsNone(c["filter_internal_none"])
		self.assertIsNone(c["filter_no_field"])

	def test_print_follows_can_print_doc(self):
		self.assertEqual(self.out["print"], [True, False, False, False, True, True, False])

	def test_the_directory_shows_where_the_form_has_it(self):
		self.assertEqual(
			self.out["widgets"],
			[
				{"contacts": True, "addresses": True},
				{"contacts": True, "addresses": False},
				{"contacts": False, "addresses": False},
			],
		)

	def test_the_directory_sources_are_the_widget_s(self):
		s = self.out["sources"]
		self.assertEqual(
			s["opp"], [{"doctype": "Opportunity", "name": "CRM-OPP-1"}, {"doctype": "Lead", "name": "LEAD-1"}]
		)
		self.assertEqual(
			s["project"],
			[
				{"doctype": "Project", "name": "PROJ-1"},
				{"doctype": "Customer", "name": "Acme"},
				{"doctype": "Opportunity", "name": "CRM-OPP-1"},
				{"doctype": "Supplier", "name": "Acme"},
			],
		)
		self.assertEqual(s["not_array"], [{"doctype": "Project", "name": "P"}])
		self.assertEqual(s["address_field"], ["customer_primary_address", "primary_address", None])
		self.assertEqual(s["address_name"], "ADDR-2")
		self.assertEqual(
			s["party"],
			[
				{"link_doctype": "Prospect", "link_name": "P"},
				{"link_doctype": "Customer", "link_name": "C"},
				None,
				None,
			],
		)
		self.assertEqual(s["account"], [True, False])


# ---------------------------------------------------------------------------------------- server


class _Dict(dict):
	def __getattr__(self, key):
		try:
			return self[key]
		except KeyError:
			return None

	def __setattr__(self, key, value):
		self[key] = value


class _Field(_Dict):
	pass


class _Meta:
	def __init__(self, fields):
		self.fields = {f["fieldname"]: _Field(f) for f in fields}

	def get_field(self, fieldname):
		return self.fields.get(fieldname)

	def get_permlevel_access(self, ptype="read"):
		return [0]


def setUpModule():
	global record_info, STUB
	STUB = types.SimpleNamespace(docinfo=None, todos=[], events=[], meta=_Meta([]), values={}, checked=[])
	frappe = types.ModuleType("frappe")
	frappe._dict = _Dict
	frappe.response = _Dict()
	frappe.utils = types.SimpleNamespace(
		get_fullname=lambda user: {"u1": "User One", "u2": "User Two"}.get(user, user)
	)
	frappe.get_meta = lambda doctype: STUB.meta

	def get_all(doctype, filters=None, fields=None, **kwargs):
		return [_Dict(name=n, owner="u1", creation="2026-10-13 10:00:00") for n in filters["name"][1]]

	frappe.get_all = get_all

	def get_value(doctype, name, fields, as_dict=False, order_by=None):
		return _Dict({f: STUB.values.get(f) for f in fields})

	frappe.db = types.SimpleNamespace(get_value=get_value)
	desk = types.ModuleType("frappe.desk")
	form = types.ModuleType("frappe.desk.form")
	load = types.ModuleType("frappe.desk.form.load")

	def get_docinfo(doc=None, doctype=None, name=None):
		frappe.response["docinfo"] = STUB.docinfo

	load.get_docinfo = get_docinfo
	erpnext = types.ModuleType("erpnext")
	crm = types.ModuleType("erpnext.crm")
	utils = types.ModuleType("erpnext.crm.utils")
	# Only the open half of get_open_activities is read (the closed history is never shown).
	utils.get_open_todos = lambda doctype, name: STUB.todos
	utils.get_open_events = lambda doctype, name: STUB.events

	def get_open_activities(doctype, name):
		raise AssertionError("record_info must not run the closed-history queries")

	utils.get_open_activities = get_open_activities

	def has_permission(doctype, ptype="read", doc=None, throw=False, **kwargs):
		STUB.checked.append((doctype, doc, throw))
		return True

	frappe.has_permission = has_permission
	sys.modules.update(
		{
			"frappe": frappe,
			"frappe.desk": desk,
			"frappe.desk.form": form,
			"frappe.desk.form.load": load,
			"erpnext": erpnext,
			"erpnext.crm": crm,
			"erpnext.crm.utils": utils,
		}
	)
	sys.modules.pop("erpnext_enhancements.form_kit.record_info", None)
	import importlib.util

	spec = importlib.util.spec_from_file_location("ge_record_info_under_test", INFO_PY)
	record_info = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(record_info)


def tearDownModule():
	for key in (
		"frappe",
		"frappe.desk",
		"frappe.desk.form",
		"frappe.desk.form.load",
		"erpnext",
		"erpnext.crm",
		"erpnext.crm.utils",
	):
		sys.modules.pop(key, None)


class TestRecordInfo(unittest.TestCase):
	def setUp(self):
		STUB.docinfo = _Dict(
			attachments=[
				{
					"name": "F1",
					"file_name": "plan.pdf",
					"file_url": "/files/plan.pdf",
					"file_size": 10,
					"is_private": 1,
				}
			],
			communications=[
				{
					"name": "C1",
					"communication_type": "Communication",
					"communication_medium": "Email",
					"subject": "Re: <b>quote</b>",
					"sender": "x@example.com",
					"sender_full_name": "X",
					"content": "<p>Hello</p><script>alert(1)</script><p>World &amp; more</p>",
					"communication_date": "2026-10-12 08:00:00",
					"delivery_status": "Sent",
					"attachments": '[{"file_url": "/files/a.pdf"}]',
				}
			],
			versions=[{"name": "V1", "owner": "u2", "creation": "2026-10-10", "data": '{"changed": []}'}],
			assignments=[
				{"name": "T1", "owner": "u1", "description": "<p>Call <i>them</i></p>", "status": "Open"}
			],
			assignment_logs=[
				{
					"owner": "u2",
					"creation": "2026-10-11",
					"comment_type": "Assigned",
					"content": "<b>u2</b> assigned",
				}
			],
			attachment_logs=[],
			info_logs=[],
			workflow_logs=[],
			milestones=[],
			permissions={"read": 1, "write": 1, "email": 1, "print": 0, "if_owner": {}},
			shared=[{"user": "u2", "read": 1, "write": 1, "share": 0, "everyone": 0}],
			tags="big,,job",
			is_document_followed=1,
			document_email="",
		)
		STUB.todos = [
			{"name": "TD1", "description": "<p>Site visit</p>", "allocated_to": "u1", "date": "2026-10-20"}
		]
		STUB.events = []
		STUB.meta = _Meta([{"fieldname": "custom_drive_folder_id", "permlevel": 0}])
		STUB.values = {
			"owner": "u1",
			"creation": "2026-10-01",
			"modified_by": "u2",
			"modified": "2026-10-14",
			"docstatus": 0,
			"custom_drive_folder_id": "abc",
		}

	def test_it_returns_text_never_markup(self):
		out = record_info.build("Opportunity", "CRM-OPP-1")
		comm = out["communications"][0]
		self.assertEqual(comm["text"], "Hello\nWorld & more")
		self.assertEqual(comm["subject"], "Re: quote")
		self.assertEqual(comm["attachments"], 1)
		self.assertEqual(out["assignments"][0]["text"], "Call them")
		self.assertEqual(out["assignments"][0]["user_name"], "User One")
		self.assertEqual(out["logs"][0]["text"], "u2 assigned")
		self.assertEqual(out["open"]["tasks"][0]["text"], "Site visit")

	def test_open_activities_keep_erpnext_s_permission_check(self):
		STUB.checked.clear()
		record_info.build("Opportunity", "CRM-OPP-1")
		self.assertIn(("Opportunity", "CRM-OPP-1", True), STUB.checked)

	def test_it_answers_in_message_and_leaves_the_response_clean(self):
		import frappe

		frappe.response.pop("docinfo", None)
		out = record_info.build("Opportunity", "CRM-OPP-1")
		self.assertNotIn("docinfo", frappe.response)
		self.assertEqual(out["tags"], ["big", "job"])
		self.assertTrue(out["followed"])
		self.assertEqual(out["permissions"]["write"], 1)
		self.assertEqual(out["permissions"]["print"], 0)
		self.assertNotIn("if_owner", out["permissions"])
		self.assertEqual(out["shared"][0]["user_name"], "User Two")
		self.assertEqual(out["created"]["by_name"], "User One")
		self.assertEqual(out["attachments"][0]["owner_name"], "User One")
		self.assertEqual(out["drive_folder"], {"id": "abc", "missing": 0})

	def test_the_email_fields_come_back_where_readable(self):
		STUB.meta = _Meta(
			[
				{"fieldname": "contact_email", "permlevel": 0},
				{"fieldname": "primary_contact_email", "permlevel": 0},
				{"fieldname": "email_id", "permlevel": 3},
			]
		)
		STUB.values.update({"contact_email": "a@example.com", "primary_contact_email": None, "email_id": "x"})
		# email_id sits at a permlevel the caller cannot read (the stub reads level 0 only).
		self.assertEqual(
			record_info.build("Opportunity", "X")["emails"],
			{"contact_email": "a@example.com", "primary_contact_email": ""},
		)

	def test_a_drive_folder_field_the_doctype_lacks_is_empty(self):
		STUB.meta = _Meta([])
		self.assertEqual(record_info.build("Opportunity", "X")["drive_folder"], {"id": "", "missing": 0})

	def test_plain_cuts_long_text(self):
		self.assertEqual(record_info.plain("x" * 50, 10), "x" * 9 + "…")
		self.assertEqual(record_info.plain(None), "")


class TestTheEndpoint(unittest.TestCase):
	def test_record_info_is_a_permission_checked_get(self):
		api = _text(API_PY)
		self.assertIn('@frappe.whitelist(methods=["GET"])\ndef record_info(doctype, name):', api)
		body = api[api.index("def record_info(doctype, name):") :]
		self.assertIn("_require_configured(doctype)", body)
		self.assertIn("record_info_rules.build(doctype, name)", body)
		info = _text(INFO_PY)
		# frappe's own get_docinfo, which checks read on the record (get_lazy_doc check_permission).
		self.assertIn("from frappe.desk.form.load import get_docinfo", info)
		self.assertIn("get_docinfo(doctype=doctype, name=name)", info)

	def test_record_info_whitelists_nothing_itself(self):
		tree = ast.parse(_text(INFO_PY))
		for node in ast.walk(tree):
			if isinstance(node, ast.FunctionDef):
				for deco in node.decorator_list:
					self.assertNotIn("whitelist", ast.unparse(deco), node.name)


class TestTheShellsAndTheEditor(unittest.TestCase):
	def test_the_page_draws_the_section(self):
		self.assertIn('<section class="ge-tools" hidden></section>', _code(PAGE_JS))

	def test_the_side_panel_draws_it_under_the_comments(self):
		code = _code(SIDE_PANEL_JS)
		self.assertIn(
			'$(\'<section class="ge-tools fk-tools" hidden></section>\').insertAfter(this.$root.find(".fk-comments"));',
			code,
		)

	def test_the_editor_mounts_it_with_the_comments(self):
		code = _code(EDITOR_JS)
		self.assertIn('import { RecordTools } from "./record_tools.js";', code)
		body = code[code.index("mount_comments() {") :]
		self.assertIn("this.mount_tools();", body[: body.index("\n\t}\n")])
		tools = code[code.index("mount_tools() {") :]
		self.assertIn("new RecordTools(this)", tools[: tools.index("\n\t}\n")])

	def test_a_new_record_has_none(self):
		code = _code(UI_JS)
		self.assertIn("if (ed.is_new || !ed.name || !ed.doctype) {", code)
		self.assertIn('$box.empty().prop("hidden", true);', code)


class TestEveryGuidedDocType(unittest.TestCase):
	"""Nik, 2026-10-14: "now and in the future": decided by meta, permissions and values."""

	def test_the_tools_never_single_out_a_doctype(self):
		code = _code(UI_JS)
		self.assertIsNone(re.search(r"this\.doctype\s*[!=]==\s*[\"']", code))
		self.assertIsNone(
			re.search(r'"(Opportunity|Lead|Prospect|Project|Customer|Supplier|Quotation|Item)"', code),
			"the per-DocType list is RECORD_TOOLS in form_kit/record_tools.js",
		)

	def test_the_only_per_doctype_list_is_the_config(self):
		code = _code(RULES_JS)
		self.assertIn("export const RECORD_TOOLS = {", code)
		after = code[code.index("export function tools_config(doctype) {") :]
		self.assertIsNone(
			re.search(r'"(Opportunity|Lead|Project|Customer)"', after.replace('"Customer",', ""))
		)


class TestSafety(unittest.TestCase):
	def test_every_request_is_sf_kit_s_call(self):
		code = _code(UI_JS)
		self.assertNotIn("frappe.call(", code)
		self.assertNotIn("frappe.xcall(", code)
		self.assertNotIn("$.ajax", code)
		self.assertIn(
			'this.sf()\n\t\t\t.call(API + "record_info", { doctype, name }, { quiet: true, method: "GET" })',
			code,
		)

	def test_no_user_string_is_written_as_html(self):
		code = _code(UI_JS)
		self.assertNotIn(".html(", code)
		self.assertNotIn("innerHTML", code)
		self.assertNotIn(".append(`", code)
		# MultiCheck labels are HTML in frappe: escaped there.
		self.assertIn(
			'label: detail ? `${esc(name)} <span class="text-muted">— ${esc(detail)}</span>` : esc(name),',
			code,
		)
		# frappe.confirm messages are HTML: the names in them are escaped.
		self.assertIn("frappe.utils.escape_html(label)", code)

	def test_every_dialog_joins_the_history_guard(self):
		code = _code(UI_JS)
		self.assertIn("party.open_dialog(open)", code)
		for opener in (
			"new frappe.ui.form.AssignToDialog(",
			"new frappe.ui.FileUploader(",
			"new frappe.views.CommunicationComposer(",
			"new frappe.views.InteractionComposer(",
			"frappe.prompt(",
			"record_actions.open_reminder(",
			"record_actions.open_generate_contract(",
		):
			with self.subTest(opener=opener):
				self.assertIn(opener, code)
		# frappe.confirm only inside this.dialog(() => ...).
		for match in re.finditer(r"frappe\.confirm\(", code):
			before = code[max(0, match.start() - 40) : match.start()]
			self.assertIn("this.dialog(() =>", before)

	def test_new_task_never_touches_another_record_s_form(self):
		# frappe v16's InteractionComposer reads the GLOBAL cur_frm (render_attach lists its files; the
		# insert callback reloads it). On this page it is whatever form was open last.
		code = _code(UI_JS)
		body = code[code.index("new_interaction(kind) {") :]
		body = body[: body.index("\n\t}\n")]
		self.assertIn(
			"const foreign = !!saved && !(saved.doctype === doctype && saved.docname === name);", body
		)
		self.assertIn("if (foreign) window.cur_frm = null;", body)
		self.assertIn("if (window.cur_frm === null && route_str() === route) window.cur_frm = saved;", body)
		self.assertIn("if (!creating) restore();", body)
		self.assertIn(".finally(() => setTimeout(restore, 0));", body)
		# This record's own form lists its files itself: not passed twice.
		self.assertIn("attachments: own_form ? [] : this.record_files(),", body)
		self.assertIn("attachments: this.record_files(),", code[code.index("new_email(reply) {") :])

	def test_import_contacts_never_puts_a_name_in_an_attribute(self):
		# frappe v16's MultiCheck writes option.value unescaped into data-unit="...".
		for path in (UI_JS, CONTROLLER_JS):
			with self.subTest(path=path.name):
				code = _code(path)
				self.assertIn("value: String(index),", code)
				self.assertIn(".map((index) => (", code)
				self.assertNotIn("value: c.name,", code)

	def test_generate_contract_saves_first_and_primary_says_only_what_saved(self):
		code = _code(UI_JS)
		self.assertIn(
			"this.leave(() => this.dialog(() => record_actions.open_generate_contract(doctype, name)))", code
		)
		self.assertIn('if (unsaved()) return "unsaved";', code)

	def test_the_assign_dialog_is_kept_per_record(self):
		code = _code(UI_JS)
		self.assertIn("kept && kept.doctype === doctype && kept.docname === name", code)
		self.assertIn("this.assign_to = assign;", code)

	def test_the_menu_is_the_planner_kit_s(self):
		code = _code(UI_JS)
		self.assertIn('frappe.require(["planner_kit.bundle.js", "planner_kit.bundle.css"]', code)
		self.assertIn('kit.menu({ anchor, title: this.title(), items, owner: "ge-tools" })', code)
		# Loaded with the tools, ahead of the first tap: a menu measured before its stylesheet lands is
		# placed wrongly (frappe.require does not wait for a stylesheet).
		self.assertIn("this.kit();", code)


class TestTheDeskFormSharesTheCode(unittest.TestCase):
	def test_the_form_buttons_call_record_actions(self):
		self.assertIn(
			"actions.open_generate_contract(source_doctype, frm.doc.name)", _code(JS / "contracts.js")
		)
		self.assertIn(
			"actions.open_drive_folder(folder_id, missing)",
			_code(JS / "global_enhancements" / "drive_folder_button.js"),
		)
		self.assertIn(
			"actions.open_reminder({",
			_code(APP / "project_enhancements" / "doctype" / "opportunity" / "opportunity.js"),
		)
		self.assertNotIn("create_contract`", _code(JS / "contracts.js"))

	def test_the_widget_calls_party_sources(self):
		code = _code(CONTROLLER_JS)
		self.assertIn("rules ? rules.party_sources(frm.doctype, frm.doc, frm.meta.fields || []) : []", code)
		self.assertIn("rules ? rules.primary_address_field(this.frm.meta.fields || []) : null", code)
		self.assertIn("return rules.form_party(frm.doctype, frm.doc || {});", code)

	def test_a_tab_on_the_old_bundle_gets_an_empty_directory_not_a_type_error(self):
		code = _code(CONTROLLER_JS)
		self.assertIn("function ee_party_rules() {", code)
		self.assertIn(
			'return (typeof erpnext_enhancements !== "undefined" && erpnext_enhancements.party_sources) || null;',
			code,
		)
		# Every use goes through the guard.
		self.assertNotIn("erpnext_enhancements.party_sources.", code)

	def test_party_names_in_link_existing_are_escaped(self):
		for path in (CONTROLLER_JS, UI_JS):
			with self.subTest(path=path.name):
				self.assertIn("const party_name = frappe.utils.escape_html(party.link_name);", _code(path))

	def test_the_desk_bundle_installs_both(self):
		code = _code(DESK_BUNDLE)
		self.assertIn('import * as record_actions from "./global_enhancements/record_actions.js";', code)
		self.assertIn('import * as party_sources from "./global_enhancements/party_sources.js";', code)
		self.assertIn("window.erpnext_enhancements.record_actions = Object.assign({}, record_actions);", code)
		self.assertIn("window.erpnext_enhancements.party_sources = Object.assign({}, party_sources);", code)

	def test_the_shared_modules_do_nothing_at_import(self):
		for path in (SOURCES_JS, ACTIONS_JS, RULES_JS):
			with self.subTest(path=path.name):
				code = _code(path)
				top = [
					line
					for line in code.splitlines()
					if line
					and not line.startswith(
						(
							"\t",
							" ",
							"}",
							")",
							"]",
							"export function",
							"export const",
							"const",
							"function",
							"import",
						)
					)
				]
				self.assertEqual(top, [], "top-level statements")


class TestTheStyles(unittest.TestCase):
	def test_the_section_is_styled_with_tokens(self):
		css = _text(CSS)
		block = css[css.index("/* ---- The record tools (v1.686.0") :]
		# The box is the view settings' one `.ge-tools` rule; where it sits is theirs (apply_view).
		self.assertEqual(css.count("\n.ge-tools {"), 1)
		self.assertIn(".ge-tools[hidden] {", css)
		self.assertNotIn(".ge-layout > .ge-tools", css, "placement belongs to the view settings")
		self.assertNotIn("grid-row: 1 / span 2", block)
		self.assertIn("min-height: var(--sf-touch-min);", block)
		self.assertIsNone(re.search(r"#[0-9a-fA-F]{3,8}\b", block), "tokens only")
		self.assertIsNone(re.search(r"\brgba?\(", block), "tokens only")


# v1.688.1: Reply reads the full email (a round trip) before it opens; the board's side panel switches
# records in place, so the answer may land while another record is on screen. The real new_email runs
# here against a stand-in editor and frappe, and the read is settled by hand after a switch.
REPLY_RUNNER = r"""
import { pathToFileURL } from "node:url";
const [dir] = process.argv.slice(2);
globalThis.__ = (s, a) => (a ? s.replace(/\{(\d+)\}/g, (m, i) => a[i]) : s);
const opened = [];
globalThis.frappe = {
	session: { user_email: "me@example.com" },
	utils: { escape_html: (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;") },
	dom: { remove_script_and_style: (h) => h.replace(/<script[\s\S]*?<\/script>/gi, "") },
	views: { CommunicationComposer: function (opts) { opened.push(opts); } },
};
const rules = await import(pathToFileURL(dir + "/form_kit/record_tools.js").href);
const { RecordTools } = await import(pathToFileURL(dir + "/form_kit/ui/record_tools.js").href);
const tick = () => new Promise((r) => setTimeout(r, 0));
const reply = { name: "COMM-1", subject: "Quote", sender: "client@example.com", recipients: "me@example.com", text: "Hi <b>there</b>" };
function tools() {
	const t = Object.create(RecordTools.prototype);
	const read = {};
	Object.assign(t, {
		ed: { doctype: "Opportunity", name: "CRM-OPP-1", is_new: false, values: {} },
		doctype: "Opportunity",
		name: "CRM-OPP-1",
		mount_seq: 1,
		config: rules.tools_config("Opportunity"),
		info: { communications: [], emails: {}, attachments: [] },
	});
	t.sf = () => ({ call: (method, args) => { read.args = args; return new Promise((res, rej) => Object.assign(read, { res, rej })); } });
	// Counted: asking for a dialog takes party.js's double-tap token, so a stale reply must not ask.
	t.dialogs = 0;
	t.dialog = (make) => {
		t.dialogs += 1;
		make();
	};
	t.title = () => "Plaza fountain";
	t.reload = () => {};
	return { t, read };
}
// The editor moves to another record, as the side panel does (and the tools re-mount on it).
function switch_record(t, doctype, name) {
	Object.assign(t.ed, { doctype, name });
	Object.assign(t, { doctype, name });
	t.mount_seq += 1;
}
// One case on fresh tools, counting only what it opened.
async function run(steps) {
	opened.length = 0;
	const c = tools();
	await steps(c);
	await tick();
	const first = opened[0];
	return {
		opened: opened.length,
		content: first && first.last_email.content,
		asked: c.read.args.name,
		dialogs: c.t.dialogs,
		made: c.made,
	};
}
// The dialog as party.js open_dialog makes it on the guided page: only after the history guard loads.
function slow_dialog(c) {
	const later = [];
	c.t.dialog = (make) => later.push(make);
	return () => {
		c.made = later.length;
		later.forEach((make) => make());
	};
}
const full = [{ name: "COMM-1", content: "<p>Hi there</p><script>x()</script>" }];
const out = {};

out.same = await run((c) => {
	c.t.new_email(reply);
	c.read.res(full);
});
out.switched = await run((c) => {
	c.t.new_email(reply);
	switch_record(c.t, "Opportunity", "CRM-OPP-2");
	c.read.res(full);
});
out.switched_failed = await run((c) => {
	c.t.new_email(reply);
	switch_record(c.t, "Opportunity", "CRM-OPP-2");
	c.read.rej(new Error("offline"));
});
// The same name under another doctype is another record.
out.other_doctype = await run((c) => {
	c.t.new_email(reply);
	switch_record(c.t, "Project", "CRM-OPP-1");
	c.read.res(full);
});
// Away and back before the read answers: the person is on the record they asked from.
out.round_trip = await run((c) => {
	c.t.new_email(reply);
	switch_record(c.t, "Opportunity", "CRM-OPP-2");
	switch_record(c.t, "Opportunity", "CRM-OPP-1");
	c.read.res(full);
});
out.fallback = await run((c) => {
	c.t.new_email(reply);
	c.read.rej(new Error("offline"));
});
// A realtime refresh re-mounts the tools on the same record.
out.remounted = await run((c) => {
	c.t.new_email(reply);
	c.t.mount_seq += 1;
	c.read.res([]);
});
out.slow_dialog_switched = await run(async (c) => {
	const show = slow_dialog(c);
	c.t.new_email(reply);
	c.read.res(full);
	await tick();
	switch_record(c.t, "Opportunity", "CRM-OPP-2");
	show();
});
out.slow_dialog_same = await run(async (c) => {
	const show = slow_dialog(c);
	c.t.new_email(reply);
	c.read.res(full);
	await tick();
	show();
});

console.log(JSON.stringify(out));
"""


@unittest.skipUnless(shutil.which("node"), "node is not on PATH")
class TestReplyFollowsTheRecord(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		node = shutil.which("node")
		with tempfile.TemporaryDirectory() as tmp:
			folder = Path(tmp)
			(folder / "package.json").write_text('{"type": "module"}', encoding="utf-8")
			shutil.copytree(FORM_KIT_JS, folder / "form_kit")
			(folder / "global_enhancements").mkdir()
			shutil.copy(SOURCES_JS, folder / "global_enhancements" / "party_sources.js")
			shutil.copy(ACTIONS_JS, folder / "global_enhancements" / "record_actions.js")
			(folder / "runner.mjs").write_text(REPLY_RUNNER, encoding="utf-8")
			proc = subprocess.run(
				[node, str(folder / "runner.mjs"), str(folder)],
				capture_output=True,
				text=True,
				encoding="utf-8",
				timeout=60,
			)
		if proc.returncode != 0:
			raise AssertionError(f"node failed:\n{proc.stderr}")
		cls.out = json.loads(proc.stdout)

	CUT = "Hi &lt;b&gt;there&lt;/b&gt;"

	def test_on_the_same_record_the_full_email_is_quoted(self):
		self.assertEqual(self.out["same"]["opened"], 1)
		self.assertEqual(self.out["same"]["content"], "<p>Hi there</p>")
		self.assertEqual(self.out["same"]["asked"], "CRM-OPP-1")

	def test_after_a_switch_the_old_reply_never_opens(self):
		self.assertEqual(self.out["switched"]["opened"], 0, "nothing opened over the new record")
		self.assertEqual(self.out["switched_failed"]["opened"], 0, "nor when the read fails")
		# Nor is a dialog asked for: that would hold party.js's double-tap token, and a tap on the new
		# record's Reply in that moment would be ignored.
		self.assertEqual(self.out["switched"]["dialogs"], 0)
		self.assertEqual(self.out["switched_failed"]["dialogs"], 0)

	def test_the_same_name_under_another_doctype_is_another_record(self):
		self.assertEqual(self.out["other_doctype"]["opened"], 0)

	def test_away_and_back_before_the_answer_still_opens_it(self):
		self.assertEqual(self.out["round_trip"]["opened"], 1)
		self.assertEqual(self.out["round_trip"]["content"], "<p>Hi there</p>")

	def test_a_failed_read_still_quotes_the_cut_copy(self):
		self.assertEqual(self.out["fallback"]["opened"], 1)
		self.assertEqual(self.out["fallback"]["content"], self.CUT)

	def test_a_reload_of_the_same_record_still_opens_it(self):
		# A realtime refresh re-mounts the tools on the same record: the person still gets the reply.
		self.assertEqual(self.out["remounted"]["opened"], 1)
		self.assertEqual(self.out["remounted"]["content"], self.CUT)

	def test_a_switch_while_the_dialog_loads_opens_nothing(self):
		# The dialog was asked for while the record was on screen, and is made after it left.
		self.assertEqual(self.out["slow_dialog_switched"]["made"], 1)
		self.assertEqual(self.out["slow_dialog_switched"]["opened"], 0)
		self.assertEqual(self.out["slow_dialog_same"]["made"], 1)
		self.assertEqual(self.out["slow_dialog_same"]["opened"], 1)


if __name__ == "__main__":
	unittest.main()
