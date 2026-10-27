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
// v1.746.0: the Account / Supplier rule both faces draw (every name invented).
const acct = (doctype, name, title) => ({ doctype, name, title });
const both = [acct("Customer", "CUST-0042", "Bluebird Water Gardens"), acct("Customer", "Harbor Plaza", ""), acct("Supplier", "Harbor Plaza", "Harbor Plaza")];
out.accounts = {
	project: ps.account_entries(both, "Project", "PROJ-1"),
	customer: ps.account_entries(both, "Customer", "Harbor Plaza"),
	supplier: ps.account_entries(both, "Supplier", "Harbor Plaza"),
	none: [ps.account_entries([], "Customer", "X"), ps.account_entries(undefined, "Project", "P"), ps.account_entries("oops", "Project", "P"), ps.account_entries([null, { doctype: "Customer" }], "Project", "P")],
	unsaved: ps.account_entries(both, "Customer", ""),
	opportunity: ps.account_entries(both, "Opportunity", "CUST-0042"),
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

	def test_the_account_supplier_rule(self):
		"""v1.746.0 (TASK-2026-02942): what both faces show for a row, from party_sources.js."""
		a = self.out["accounts"]
		bluebird = {
			"own": False,
			"doctype": "Customer",
			"name": "CUST-0042",
			"label": "Bluebird Water Gardens",
			"hover": "CUST-0042",
			"tag": None,
		}
		harbor_customer = {
			"own": False,
			"doctype": "Customer",
			"name": "Harbor Plaza",
			"label": "Harbor Plaza",
			"hover": None,
			"tag": None,
		}
		harbor_supplier = dict(harbor_customer, doctype="Supplier", tag="Supplier")
		# A job: every company a link, the server's order kept, the ID on hover only when it differs.
		self.assertEqual(a["project"], [bluebird, harbor_customer, harbor_supplier])
		# Its own form: the party is quiet, first and unlinked; a same-named party of the other kind is not it.
		self.assertEqual(a["customer"], [{"own": True, "words": "This account"}, bluebird, harbor_supplier])
		self.assertEqual(a["supplier"], [{"own": True, "words": "This supplier"}, bluebird, harbor_customer])
		# Neither: empty, which both faces show as a muted "None"; junk is dropped, not drawn.
		self.assertEqual(a["none"], [[], [], [], []])
		# Only a saved Customer or Supplier has an own party.
		self.assertEqual(a["unsaved"], a["project"])
		self.assertEqual(a["opportunity"], a["project"])


class TestTheAccountSupplierLine(unittest.TestCase):
	"""v1.746.0 (TASK-2026-02942): People & places says whose person or address each row is, as the
	Desk directory's Account / Supplier column does, by the one rule in party_sources.js."""

	def test_the_guided_list_draws_the_shared_rule(self):
		code = _code(UI_JS)
		self.assertIn("account_entries } from \"../../global_enhancements/party_sources.js\";", code)
		self.assertIn("this.accounts_line($meta, c);", code)
		self.assertIn("this.accounts_line($meta, a);", code)
		self.assertIn("account_entries(record && record.accounts, this.doctype, this.name)", code)
		self.assertNotIn("linked_to(", code)
		self.assertNotIn('__("Linked to {0}"', code)

	def test_the_guided_line_writes_text_never_markup(self):
		code = _code(UI_JS)
		body = code[code.index("accounts_line($meta, record) {") :]
		body = body[: body.index("\n\t}\n")]
		self.assertIn('.text(`${__("Account / Supplier")}:`)', body)
		self.assertIn('.text(__("None"))', body)
		self.assertIn('.text(e.words === "This supplier" ? __("This supplier") : __("This account"))', body)
		self.assertIn(".text(e.label)", body)
		self.assertIn('.attr("title", e.hover)', body)
		self.assertIn('.attr("href", frappe.utils.get_form_link(e.doctype, e.name))', body)
		self.assertIn(".text(__(e.tag))", body)
		for markup in (".html(", ".append(", "innerHTML", "`<"):
			self.assertNotIn(markup, body)

	def test_the_words_reach_the_translation_extractor(self):
		"""Frappe's extractor reads only __("...") literals, so the rule's words are written out
		where they are drawn (the 295c3bfa review). "Supplier" is a DocType name, in the catalogue
		from the DocType itself; the guided tools never write one, the Desk cell does."""
		for path in (UI_JS, CONTROLLER_JS):
			code = _code(path)
			self.assertIn('__("This account")', code, path.name)
			self.assertIn('__("This supplier")', code, path.name)
		self.assertIn('esc(__("Supplier"))', _code(CONTROLLER_JS))

	def test_the_desk_cell_draws_the_same_rule_behind_the_bundle_guard(self):
		code = _code(CONTROLLER_JS)
		self.assertIn('if (!rules || typeof rules.account_entries !== "function") return "";', code)
		self.assertIn(
			"rules.account_entries(record && record.accounts, frm.doctype, frm.doc && frm.doc.name)", code
		)

	def test_the_links_are_styled_as_links(self):
		css = _text(CSS)
		rule = css[css.index(".ge-tools-account a {") :]
		rule = rule[: rule.index("}")]
		self.assertIn("color: var(--sf-link);", rule)
		self.assertIn("min-height: var(--sf-touch-min);", rule)


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
		# v1.746.0: for the form a directory reply was requested for, else this.frm.
		self.assertIn("rules ? rules.primary_address_field((frm || this.frm).meta.fields || []) : null", code)
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


# v1.748.0: on the guided page the record's files are listed twice, in the files section under the steps
# (v1.711.0) and in the tools' Attachments group in the side column. Nik, 2026-10-27: "I expect the
# attachments in the right-hand column on the guided page." The real RecordTools runs here, mount to
# render, against a small stand-in for jQuery and the DOM (enough of both for what the files code calls)
# and a stand-in server, so an attach, a remove, a drop and a reload are each followed into both lists.
# render_meta, render_activity and the Actions button are stubbed: they are not the files.
FILES_RUNNER = r"""
import { pathToFileURL } from "node:url";
const [dir] = process.argv.slice(2);

class El {
	constructor(tag) {
		this.tag = tag;
		this.attrs = {};
		this.children = [];
		this.parent = null;
		this.own_text = "";
		this.handlers = [];
	}
	classes() {
		return (this.attrs.class || "").split(/\s+/).filter(Boolean);
	}
	text() {
		return this.own_text + this.children.map((c) => c.text()).join("");
	}
	contains(other) {
		for (let n = other; n; n = n.parent) if (n === this) return true;
		return false;
	}
	descendants() {
		const out = [];
		const walk = (n) => n.children.forEach((c) => {
			out.push(c);
			walk(c);
		});
		walk(this);
		return out;
	}
	focus(opts) {
		document.activeElement = this;
		this.focus_opts = opts || {};
	}
	// Not rendered: hidden, display:none (`gone`, the stylesheet's part), or inside a closed details.
	getClientRects() {
		for (let n = this; n; n = n.parent) {
			if ("hidden" in n.attrs || n.gone) return [];
			if (n.parent && n.parent.tag === "details" && !("open" in n.parent.attrs) && n.tag !== "summary") return [];
		}
		return [{}];
	}
}
// A browser moves focus to the body when the focused element leaves the document.
function lose_focus(el) {
	if (globalThis.document && el.contains(document.activeElement)) document.activeElement = document.body;
}

// One compound selector: a tag, .classes, [attr] and [attr="value"].
function compound(text) {
	const out = { tag: null, classes: [], attrs: [] };
	const re = /([a-z][a-z0-9]*)|\.([\w-]+)|\[([\w-]+)(?:="([^"]*)")?\]/iy;
	while (re.lastIndex < text.length) {
		const m = re.exec(text);
		if (!m) throw new Error("selector not understood: " + text);
		if (m[1]) out.tag = m[1].toLowerCase();
		else if (m[2]) out.classes.push(m[2]);
		else out.attrs.push([m[3], m[4]]);
	}
	return out;
}
function is(el, c) {
	if (c.tag && el.tag !== c.tag) return false;
	const classes = el.classes();
	if (!c.classes.every((k) => classes.includes(k))) return false;
	return c.attrs.every(([k, v]) => (v === undefined ? k in el.attrs : el.attrs[k] === v));
}
// "a b, c": each group's last compound is the element, the rest its ancestors below `top`.
function matches(el, selector, top) {
	return selector.split(",").some((group) => {
		const parts = group.trim().split(/\s+/).map(compound);
		if (!is(el, parts[parts.length - 1])) return false;
		let i = parts.length - 2;
		for (let n = el.parent; n && n !== top && i >= 0; n = n.parent) if (is(n, parts[i])) i -= 1;
		return i < 0;
	});
}
function detach(el) {
	lose_focus(el);
	if (el.parent) el.parent.children = el.parent.children.filter((c) => c !== el);
	el.parent = null;
}
function parse(html) {
	const m = html.trim().match(/^<([a-z][a-z0-9]*)([^>]*)>/i);
	if (!m) throw new Error("not an element: " + html);
	const el = new El(m[1].toLowerCase());
	const re = /([\w:-]+)(?:="([^"]*)")?/g;
	let a;
	while ((a = re.exec(m[2]))) el.attrs[a[1]] = a[2] === undefined ? "" : a[2];
	return el;
}

class Q {
	constructor(els) {
		this.els = els.filter(Boolean).filter((e, i, all) => all.indexOf(e) === i);
		this.els.forEach((e, i) => (this[i] = e));
		this.length = this.els.length;
	}
	first() { return new Q(this.els.slice(0, 1)); }
	each(fn) { this.els.forEach((e, i) => fn.call(e, i, e)); return this; }
	text(v) {
		if (v === undefined) return this.els.map((e) => e.text()).join("");
		this.empty();
		this.els.forEach((e) => (e.own_text = String(v)));
		return this;
	}
	attr(k, v) {
		if (k && typeof k === "object") {
			Object.keys(k).forEach((key) => this.attr(key, k[key]));
			return this;
		}
		if (v === undefined) return this.els[0] ? this.els[0].attrs[k] : undefined;
		this.els.forEach((e) => (e.attrs[k] = String(v)));
		return this;
	}
	removeAttr(k) { this.els.forEach((e) => delete e.attrs[k]); return this; }
	// The DOM reflects hidden, open and disabled as attributes.
	prop(k, v) {
		if (v === undefined) return this.els[0] ? k in this.els[0].attrs : undefined;
		this.els.forEach((e) => (v ? (e.attrs[k] = "") : delete e.attrs[k]));
		return this;
	}
	hasClass(c) { return this.els.some((e) => e.classes().includes(c)); }
	addClass(c) {
		this.els.forEach((e) => !e.classes().includes(c) && (e.attrs.class = (e.classes().concat(c)).join(" ")));
		return this;
	}
	removeClass(c) {
		this.els.forEach((e) => (e.attrs.class = e.classes().filter((k) => k !== c).join(" ")));
		return this;
	}
	toggleClass(c, on) { return on ? this.addClass(c) : this.removeClass(c); }
	on(events, handler) {
		events.split(/\s+/).forEach((ev) => this.els.forEach((e) => e.handlers.push({ ev, handler })));
		return this;
	}
	off(ns) {
		this.els.forEach((e) => (e.handlers = e.handlers.filter((h) => !h.ev.endsWith(ns))));
		return this;
	}
	one(events, handler) {
		const self = this;
		const once = function (event) {
			self.els.forEach((e) => (e.handlers = e.handlers.filter((h) => h.handler !== once)));
			return handler.call(this, event);
		};
		return this.on(events, once);
	}
	trigger(type) { this.els.forEach((e) => fire(e, type)); return this; }
	appendTo(target) {
		const parent = target.els[0];
		this.els.forEach((e) => {
			detach(e);
			e.parent = parent;
			parent.children.push(e);
		});
		return this;
	}
	append(child) { $(child).appendTo(this); return this; }
	empty() {
		this.els.forEach((e) => {
			e.children.forEach((c) => {
				lose_focus(c);
				c.parent = null;
			});
			e.children = [];
			e.own_text = "";
		});
		return this;
	}
	remove() { this.els.forEach(detach); return this; }
	find(selector) {
		return new Q([].concat(...this.els.map((e) => e.descendants().filter((d) => matches(d, selector, e)))));
	}
	closest(selector) {
		return new Q(this.els.map((e) => {
			for (let n = e; n; n = n.parent) if (matches(n, selector, null)) return n;
			return null;
		}));
	}
	children(selector) {
		return new Q([].concat(...this.els.map((e) => e.children.filter((c) => !selector || matches(c, selector, e)))));
	}
}
function $(x) {
	if (x === undefined || x === null) return new Q([]);
	if (x instanceof Q) return x;
	if (x instanceof El) return new Q([x]);
	if (Array.isArray(x)) return new Q(x);
	return new Q([parse(String(x))]);
}
function fire(el, type, extra) {
	const event = Object.assign(
		{ type, target: el, currentTarget: el, preventDefault() { this.defaultPrevented = true; }, stopPropagation() {} },
		extra || {}
	);
	el.handlers.filter((h) => h.ev.split(".")[0] === type).forEach((h) => h.handler.call(el, event));
	return event;
}

// ---- frappe, the server and the shells ----
globalThis.window = globalThis;
globalThis.$ = $;
globalThis.__ = (s, a) => (a ? s.replace(/\{(\d+)\}/g, (m, i) => a[i]) : s);
const BODY = new El("body");
globalThis.document = { body: BODY, activeElement: BODY, contains: (el) => BODY === el || BODY.contains(el) };
let wide = true;
globalThis.matchMedia = () => ({ matches: wide });
// frappe's dialogs as bootstrap shows and hides them: focus moves into one; on hide it is display:none
// before "hidden.bs.modal" fires, with the browser possibly still naming its button as focused.
function open_dialog() {
	const $wrapper = $('<div class="modal"></div>').appendTo($(BODY));
	$('<button type="button" class="btn"></button>').appendTo($wrapper)[0].focus();
	return { $wrapper };
}
function close_dialog(d) {
	d.$wrapper[0].gone = true;
	fire(d.$wrapper[0], "hidden");
	d.$wrapper.remove();
}
const uploads = [];
let asked = null;
let meta = {};
globalThis.frappe = {
	session: { user: "tester@example.com" },
	get_meta: () => meta,
	datetime: { prettyDate: () => "just now", str_to_user: (s) => String(s) },
	ui: {
		FileUploader: function (opts) {
			this.opts = opts;
			this.dialog = open_dialog();
			uploads.push(this);
		},
	},
	confirm: (message, yes) => {
		asked = { d: open_dialog(), yes };
		return asked.d;
	},
};
// The person answers frappe's question: Yes runs the remove, then the dialog hides.
function answer(yes) {
	const a = asked;
	asked = null;
	if (yes) a.yes();
	close_dialog(a.d);
}
const last_upload = () => uploads[uploads.length - 1];

const server = { rows: [], permissions: { write: 1, delete: 1 }, fail: false, removed: [] };
const row = (name, file_name, extra) => Object.assign({ name, file_name, file_url: "/files/" + file_name, file_size: 2048 }, extra || {});
const sf = {
	call(method, args) {
		if (method.endsWith(".record_info")) {
			if (server.fail) return Promise.reject(new Error("offline"));
			return Promise.resolve(JSON.parse(JSON.stringify({ attachments: server.rows, permissions: server.permissions, docstatus: 0 })));
		}
		if (method === "frappe.desk.form.utils.remove_attach") {
			server.removed.push(args.fid);
			server.rows = server.rows.filter((r) => r.name !== args.fid);
			return Promise.resolve({});
		}
		return Promise.reject(new Error("unexpected call " + method));
	},
	toast() {},
	state: { render() {} },
};

// The guided page's shell, as render_shell draws it (the parts the tools touch), tools beside the form.
function guided_root() {
	const root = $('<div class="ge-app" data-ge-tools="side"></div>').appendTo($(BODY));
	$('<div class="ge-band-actions"></div>').appendTo(root);
	$('<button type="button" class="ge-files-chip" hidden></button>').appendTo(root);
	const main = $('<main class="ge-main"></main>').appendTo(root);
	const stage = $('<section class="ge-stage"></section>').appendTo(main);
	$('<input type="text" class="ge-field">').appendTo(stage);
	$('<section class="ge-files" hidden></section>').appendTo(main);
	const aside = $('<div class="ge-aside"></div>').appendTo(root);
	$('<section class="ge-tools" hidden></section>').appendTo(aside);
	return root;
}
// The board's side panel: no files section, no chip.
function panel_root() {
	const root = $('<div class="fk-panel"></div>').appendTo($(BODY));
	$('<div class="fk-head-row"></div>').appendTo(root);
	$('<section class="ge-tools fk-tools" hidden></section>').appendTo(root);
	return root;
}

const { RecordTools } = await import(pathToFileURL(dir + "/form_kit/ui/record_tools.js").href);
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

async function mount(root, opts) {
	const o = opts || {};
	const ed = {
		$root: root,
		id_prefix: o.id_prefix || "ge",
		doctype: "Opportunity",
		name: "CRM-OPP-TEST-1",
		is_new: false,
		values: {},
		sf,
		files_open_default: () => !o.focus,
	};
	const t = new RecordTools(ed);
	t.draw_actions_button = () => {};
	t.render_meta = () => {};
	t.render_activity = () => {};
	t.mount();
	await wait(5);
	return t;
}

function read_list($body) {
	if (!$body.length) return null;
	return {
		files: $body.find(".ge-tools-file").els.map((e) => e.text()),
		hrefs: $body.find(".ge-tools-file").els.map((e) => e.attrs.href),
		attach: $body.find(".ge-tools-add").els.map((e) => e.text()),
		removes: $body.find(".ge-tools-item-btn").length,
		notes: $body.find(".ge-tools-none, .ge-tools-note").els.map((e) => e.text()),
	};
}
const GROUP = '.ge-tools-group[data-tool="attachments"]';
function snap(root) {
	const $chip = root.find(".ge-files-chip");
	return {
		dock: read_list(root.find(".ge-files .ge-files-body")),
		group: read_list(root.find(GROUP + " .ge-tools-body")),
		dock_count: root.find(".ge-files-count").text(),
		group_count: root.find(GROUP + " .ge-tools-count").text(),
		chip: $chip.length ? $chip.find(".ge-files-chip-text").text() : null,
		chip_aria: $chip.length ? $chip.attr("aria-label") || null : null,
		dock_open: root.find(".ge-files-group").length ? root.find(".ge-files-group").prop("open") : null,
		group_open: root.find(GROUP).prop("open"),
		group_in_tools: root.find(".ge-tools " + GROUP).length === 1,
		tool_order: root.find(".ge-tools").children(".ge-tools-group").els.map((e) => e.attrs["data-tool"]),
	};
}
// Where focus is: "group" / "dock" (which list), "page", "body", then the element and its kind.
function where(root) {
	const a = document.activeElement;
	if (!a || a === BODY) return "body";
	const inside = (sel) => root.find(sel).els.some((e) => e.contains(a));
	const place = inside(GROUP) ? "group" : inside(".ge-files") ? "dock" : root[0].contains(a) ? "page" : "elsewhere";
	const kind = { "ge-tools-add": ".attach", "ge-tools-item-btn": ".remove", "ge-files-chip": ".chip", "ge-field": ".field" };
	const k = Object.keys(kind).find((c) => a.classes().includes(c));
	return `${place} ${a.tag}${k ? kind[k] : ""}${document.contains(a) ? "" : " (detached)"}`;
}
const remove_btn = (root, sel, name) =>
	$(root.find(sel + " .ge-tools-item").els.find((li) => $(li).find(".ge-tools-file").text() === name)).find(".ge-tools-item-btn")[0];

const out = {};

// A record with three rows, two of one file name (the sidebar draws one, and removes both).
server.rows = [row("F1", "site-plan.pdf", { is_private: 1 }), row("F2", "site-plan.pdf"), row("F3", "pump-photo.jpg")];
const root = guided_root();
const t = await mount(root);
out.loaded = snap(root);

// Attach: the uploader's on_success reloads (300 ms later); both lists follow.
t.attach();
out.uploads_after_attach = uploads.length;
server.rows.push(row("F4", "valve-spec.pdf"));
last_upload().opts.on_success();
close_dialog(last_upload().dialog);
await wait(350);
out.attached = snap(root);

// Remove, from the side column's list: frappe's question, every row of that name, then both lists.
// Focus goes back to the Remove button as the question closes, and to that list's summary once the
// redraw takes the button away.
fire(remove_btn(root, GROUP, "site-plan.pdf"), "click");
answer(true);
out.focus_remove_side_closed = where(root);
await wait(5);
out.removed_fids = server.removed.slice();
out.removed = snap(root);
out.focus_remove_side = where(root);

// Remove, from the middle section's list: the same.
fire(remove_btn(root, ".ge-files", "valve-spec.pdf"), "click");
answer(true);
await wait(5);
out.removed_mid = snap(root);
out.focus_remove_mid = where(root);

// A file dropped on the page while a field has focus opens the uploader with it; on_success, both
// lists, and focus goes back to the field without scrolling the page.
root.find(".ge-field")[0].focus();
const dropped = fire(root[0], "drop", { originalEvent: { dataTransfer: { types: ["Files"], files: [{ name: "drop.png" }] } } });
out.drop_claimed = !!dropped.defaultPrevented;
out.drop_files = (last_upload().opts.files || []).length;
server.rows.push(row("F5", "drop.png"));
last_upload().opts.on_success();
close_dialog(last_upload().dialog);
await wait(350);
out.dropped = snap(root);
out.focus_drop = where(root);
out.focus_drop_quiet = !!(document.activeElement.focus_opts || {}).preventScroll;

// ---- focus, case by case ----
// "Remove", then No: back on that Remove button, nothing redrawn.
fire(remove_btn(root, ".ge-files", "pump-photo.jpg"), "click");
answer(false);
await wait(5);
out.focus_remove_no = where(root);
// "Attach a file" in the side list, a file uploaded: on the new list's Attach a file.
fire(root.find(GROUP + " .ge-tools-add")[0], "click");
server.rows.push(row("F9", "pond-liner.pdf"));
last_upload().opts.on_success();
close_dialog(last_upload().dialog);
out.focus_attach_closed = where(root);
await wait(350);
out.focus_attach = where(root);
// "Attach a file" in the middle section, the uploader closed with nothing: back on that button.
const mid_attach = root.find(".ge-files .ge-tools-add")[0];
fire(mid_attach, "click");
close_dialog(last_upload().dialog);
out.focus_attach_cancel = where(root);
out.focus_attach_cancel_same = document.activeElement === mid_attach;
// The redraw lands while the uploader is still open (more files to go): it waits for it to close.
fire(root.find(GROUP + " .ge-tools-add")[0], "click");
const open_uploader = last_upload();
t.reload();
await wait(5);
out.focus_while_open = where(root);
close_dialog(open_uploader.dialog);
out.focus_after_open = where(root);
// A realtime reload while a file's link in the middle section has focus: that list's summary.
root.find(".ge-files .ge-tools-file")[0].focus();
t.reload();
await wait(5);
out.focus_reload = where(root);
// Removed, and the person moved on to a field before the redraw: focus stays in the field.
fire(remove_btn(root, GROUP, "pond-liner.pdf"), "click");
answer(true);
root.find(".ge-field")[0].focus();
await wait(5);
out.focus_moved_on = where(root);
// A drop with nothing focused moves nothing.
BODY.focus();
fire(root[0], "drop", { originalEvent: { dataTransfer: { types: ["Files"], files: [{ name: "x.png" }] } } });
close_dialog(last_upload().dialog);
out.focus_drop_none = where(root);

// A reload after a change made elsewhere (another tab took every file away).
server.rows = [];
t.reload();
await wait(5);
out.reloaded_empty = snap(root);
// The chip says "Attach a file" now; its uploader closed with nothing: back on the chip.
root.find(".ge-files-chip")[0].focus();
t.chip_action();
close_dialog(last_upload().dialog);
out.focus_chip = where(root);

// max_attachments reached: no Attach a file in either list, the same note in both.
meta = { max_attachments: 2 };
server.rows = [row("F6", "a.pdf"), row("F7", "b.pdf")];
const full_root = guided_root();
await mount(full_root);
out.full = snap(full_root);
meta = {};

// No write: nothing to add or remove in either.
server.permissions = { write: 0, delete: 0 };
server.rows = [row("F8", "c.pdf")];
const read_root = guided_root();
await mount(read_root);
out.read_only = snap(read_root);
server.permissions = { write: 1, delete: 1 };

// The record's files could not be read: both lists say so.
server.fail = true;
const failed_root = guided_root();
await mount(failed_root);
out.failed = snap(failed_root);
server.fail = false;

// A narrow screen in focus mode: the section folds (focus), the group still starts open (the
// stylesheet shows it only beside the form, so whenever it shows it is open).
server.rows = [];
wide = false;
const narrow_root = guided_root();
await mount(narrow_root, { focus: true });
out.narrow_focus = snap(narrow_root);
wide = true;

// The board's side panel: one list, in the tools, closed as before; no section, no chip.
const p_root = panel_root();
await mount(p_root, { id_prefix: "fkp" });
out.panel = snap(p_root);

console.log(JSON.stringify(out));
"""


@unittest.skipUnless(shutil.which("node"), "node is not on PATH")
class TestTheFilesInBothPlaces(unittest.TestCase):
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
			(folder / "runner.mjs").write_text(FILES_RUNNER, encoding="utf-8")
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

	def assertSameInBoth(self, snap, files, count):
		self.assertIsNotNone(snap["dock"], "the files section is drawn")
		self.assertIsNotNone(snap["group"], "the tools' group is drawn")
		self.assertEqual(snap["dock"], snap["group"], "both lists are the same list")
		self.assertEqual(snap["dock"]["files"], files)
		self.assertEqual(snap["dock_count"], snap["group_count"])
		self.assertEqual(snap["dock_count"], str(count) if count else "")

	def test_both_places_list_the_same_files_with_the_same_count(self):
		s = self.out["loaded"]
		self.assertSameInBoth(s, ["site-plan.pdf", "pump-photo.jpg"], 2)
		self.assertEqual(s["chip"], "2 files")
		self.assertEqual(s["chip_aria"], "2 files attached")
		self.assertEqual(s["group"]["attach"], ["Attach a file"])
		self.assertEqual(s["group"]["removes"], 2)
		self.assertEqual(s["group"]["hrefs"], ["/files/site-plan.pdf", "/files/pump-photo.jpg"])

	def test_the_group_is_in_the_tools_where_it_was_and_starts_open(self):
		s = self.out["loaded"]
		self.assertTrue(s["group_in_tools"])
		self.assertEqual(s["tool_order"], ["attachments", "activity"])
		self.assertTrue(s["group_open"])
		self.assertTrue(s["dock_open"])

	def test_an_attach_refreshes_both(self):
		self.assertEqual(self.out["uploads_after_attach"], 1)
		s = self.out["attached"]
		self.assertSameInBoth(s, ["site-plan.pdf", "pump-photo.jpg", "valve-spec.pdf"], 3)
		self.assertEqual(s["chip"], "3 files")

	def test_a_remove_from_either_list_refreshes_both(self):
		# Both rows of the one file name go, as the Desk sidebar removes them.
		self.assertEqual(self.out["removed_fids"], ["F1", "F2"])
		self.assertSameInBoth(self.out["removed"], ["pump-photo.jpg", "valve-spec.pdf"], 2)
		self.assertEqual(self.out["removed"]["chip"], "2 files")
		self.assertSameInBoth(self.out["removed_mid"], ["pump-photo.jpg"], 1)
		self.assertEqual(self.out["removed_mid"]["chip"], "1 file")

	def test_a_drop_refreshes_both(self):
		self.assertTrue(self.out["drop_claimed"])
		self.assertEqual(self.out["drop_files"], 1, "the uploader opens with the dropped file")
		self.assertSameInBoth(self.out["dropped"], ["pump-photo.jpg", "drop.png"], 2)

	# Focus after a redraw (review of v1.748.0): the button used goes with the old list, and frappe
	# returns focus after a dialog only on a Form route, so without this it fell to <body>.

	def test_after_a_remove_focus_is_on_that_list_s_summary(self):
		self.assertEqual(
			self.out["focus_remove_side_closed"], "group button.remove", "as the question closes"
		)
		self.assertEqual(self.out["focus_remove_side"], "group summary", "once the redraw takes the button")
		self.assertEqual(self.out["focus_remove_mid"], "dock summary")

	def test_a_remove_answered_no_returns_to_its_button(self):
		self.assertEqual(self.out["focus_remove_no"], "dock button.remove")

	def test_after_an_attach_focus_is_on_that_list_s_attach_a_file(self):
		self.assertEqual(self.out["focus_attach_closed"], "group button.attach")
		self.assertEqual(
			self.out["focus_attach"], "group button.attach", "the new list's, not a detached one"
		)
		self.assertEqual(self.out["focus_attach_cancel"], "dock button.attach")
		self.assertTrue(self.out["focus_attach_cancel_same"])

	def test_a_redraw_while_the_uploader_is_open_waits_for_it(self):
		self.assertEqual(self.out["focus_while_open"], "elsewhere button", "the dialog keeps its focus")
		self.assertEqual(self.out["focus_after_open"], "group button.attach")

	def test_after_a_drop_focus_returns_to_the_field_without_scrolling(self):
		self.assertEqual(self.out["focus_drop"], "page input.field")
		self.assertTrue(self.out["focus_drop_quiet"])
		self.assertEqual(self.out["focus_drop_none"], "body", "nothing focused before, nothing moved")

	def test_the_chip_gets_focus_back(self):
		self.assertEqual(self.out["focus_chip"], "page button.chip")

	def test_a_realtime_redraw_keeps_focus_in_its_list(self):
		self.assertEqual(self.out["focus_reload"], "dock summary")

	def test_a_person_who_moved_on_keeps_their_focus(self):
		self.assertEqual(self.out["focus_moved_on"], "page input.field")

	def test_a_reload_refreshes_both_down_to_none(self):
		s = self.out["reloaded_empty"]
		self.assertSameInBoth(s, [], 0)
		self.assertEqual(
			s["dock"]["notes"], ["No files attached. Drop files anywhere on this page, or attach one."]
		)
		self.assertEqual(s["chip"], "Attach a file")

	def test_max_attachments_agrees_in_both(self):
		s = self.out["full"]
		self.assertSameInBoth(s, ["a.pdf", "b.pdf"], 2)
		self.assertEqual(s["group"]["attach"], [])
		self.assertEqual(s["group"]["notes"], ["The most files this record can hold (2) are attached."])
		self.assertEqual(s["chip"], "2 files")

	def test_no_write_agrees_in_both(self):
		s = self.out["read_only"]
		self.assertSameInBoth(s, ["c.pdf"], 1)
		self.assertEqual(s["group"]["attach"], [])
		self.assertEqual(s["group"]["removes"], 0)

	def test_a_failed_load_says_so_in_both(self):
		s = self.out["failed"]
		self.assertEqual(s["dock"], s["group"])
		self.assertEqual(
			s["group"]["notes"], ["The attachments could not be loaded. Reload the page to try again."]
		)
		self.assertEqual(s["group"]["files"], [])

	def test_focus_mode_folds_the_section_and_the_group_stays_open(self):
		s = self.out["narrow_focus"]
		self.assertFalse(s["dock_open"], "focus mode folds the section (files_open_default)")
		self.assertTrue(s["group_open"])
		self.assertSameInBoth(s, [], 0)

	def test_the_side_panel_keeps_one_list_in_the_tools(self):
		s = self.out["panel"]
		self.assertIsNone(s["dock"])
		self.assertIsNone(s["chip"])
		self.assertTrue(s["group_in_tools"])
		self.assertFalse(s["group_open"], "closed in the panel, as the activity is")
		self.assertEqual(s["group"]["files"], [])
		self.assertEqual(s["group"]["notes"], ["No files attached."], "the panel does not mention dropping")


class TestTheGroupShowsBesideTheForm(unittest.TestCase):
	"""The stylesheet, not the script, decides where the tools' Attachments group shows: it follows the
	view (data-ge-tools) and the window width without a redraw."""

	@classmethod
	def setUpClass(cls):
		css = _text(CSS)
		cls.block = css[css.index("/* ---- The files in the record tools as well (v1.748.0)") :]
		cls.rules = re.sub(r"/\*[\s\S]*?\*/", "", cls.block)

	def test_hidden_on_the_guided_page_unless_the_tools_sit_beside_the_form(self):
		self.assertRegex(
			self.rules, r'\.ge-app \.ge-tools-group\[data-tool="attachments"\] \{\s*display: none;\s*\}'
		)
		self.assertRegex(
			self.rules,
			r'@media \(min-width: 1024px\) \{\s*\.ge-app\[data-ge-tools="side"\] \.ge-aside '
			r'\.ge-tools-group\[data-tool="attachments"\] \{\s*display: block;',
		)

	def test_the_width_is_the_one_the_page_drops_the_side_column_at(self):
		css = _text(CSS)
		self.assertIn("@media (max-width: 1023px) {", css)
		self.assertIn('window.matchMedia("(min-width: 1024px)")', _code(UI_JS))

	def test_side_is_a_value_the_view_offers(self):
		view = _code(FORM_KIT_JS / "view.js")
		self.assertIn('tools: ["side", "below", "collapsed"],', view)
		self.assertIn('const tools = focus ? "collapsed" : v.tools;', view, "focus mode never shows it")

	def test_only_the_guided_page_is_touched(self):
		for line in self.rules.splitlines():
			if 'data-tool="attachments"' in line:
				self.assertTrue(line.strip().startswith(".ge-app"), line)
		self.assertNotIn(".fk-panel", self.rules)

	def test_tokens_only(self):
		self.assertNotRegex(self.block, r"#[0-9a-fA-F]{3,8}\b")
		self.assertNotRegex(self.block, r"\brgba?\(")
		self.assertIn("var(--sf-focus)", self.block)


if __name__ == "__main__":
	unittest.main()
