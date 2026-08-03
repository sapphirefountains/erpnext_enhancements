// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// Training Course form — the doors into the authoring flow.
//
// The heavy authoring UI is the Training Builder page (Phase 3); this is only
// how you get into it, plus the publish/assign actions that have to exist
// somewhere before that page does. Everything here calls
// api/training_author.py, which re-checks permissions server-side — a
// whitelisted method is callable directly whatever buttons we choose to draw.

// The assignment-rule value field is a Dynamic Link resolved through
// `applies_to_doctype`, and that field MUST be populated before the document is
// sent to the server. Frappe validates links in `Document.insert` at line 727 —
// before `before_insert`, before naming, and long before `validate` — so there
// is no server-side hook early enough to derive it. Stamping it here is not a
// convenience; without it every rule except "All Employees" fails to save with
// "Applies To DocType must be set first". The controller keeps its own copy of
// this mapping as a backstop for later saves and for anything built in Python.
const RULE_TARGET_DOCTYPES = {
	"All Employees": "",
	Department: "Department",
	Designation: "Designation",
	"Role Profile": "Role Profile",
	Role: "Role",
	"Employee Grade": "Employee Grade",
	"Employment Type": "Employment Type",
};

frappe.ui.form.on("Training Assignment Rule", {
	applies_to(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		frappe.model.set_value(cdt, cdn, "applies_to_doctype", RULE_TARGET_DOCTYPES[row.applies_to] ?? "");
		// The old value belongs to the previous target doctype and would now be a
		// link into the wrong table.
		frappe.model.set_value(cdt, cdn, "applies_to_value", null);
	},

	assign_rules_add(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		frappe.model.set_value(cdt, cdn, "applies_to_doctype", RULE_TARGET_DOCTYPES[row.applies_to] ?? "");
	},
});

frappe.ui.form.on("Training Course", {
	refresh(frm) {
		backfill_rule_doctypes(frm);
		if (frm.is_new()) return;

		const is_manager = frappe.user.has_role(["Training Manager", "System Manager"]);
		frm.__training_is_manager = is_manager;

		frappe.call({
			method: "frappe.client.get_list",
			args: {
				doctype: "Training Course Version",
				filters: { course: frm.doc.name, docstatus: 0 },
				fields: ["name", "version_number", "submitted_for_review"],
				limit_page_length: 1,
			},
			callback(r) {
				const draft = (r.message || [])[0];
				render_actions(frm, draft, is_manager);
				render_status_banner(frm, draft);
			},
		});
	},
});

// Repairs rows saved before this script existed, or created through the API, so
// opening and re-saving such a course does not hit the same link error.
function backfill_rule_doctypes(frm) {
	let changed = false;
	for (const row of frm.doc.assign_rules || []) {
		const expected = RULE_TARGET_DOCTYPES[row.applies_to] ?? "";
		if ((row.applies_to_doctype || "") !== expected) {
			row.applies_to_doctype = expected;
			changed = true;
		}
	}
	if (changed) frm.refresh_field("assign_rules");
}

function render_actions(frm, draft, is_manager) {
	frm.clear_custom_buttons();

	if (!draft) {
		frm.add_custom_button(__("New Draft Version"), () => new_draft(frm));
	} else {
		frm.add_custom_button(__("Open Draft {0}", [`V${draft.version_number}`]), () =>
			frappe.set_route("Form", "Training Course Version", draft.name)
		);
		if (!draft.submitted_for_review) {
			frm.add_custom_button(__("Send For Review"), () => submit_for_review(frm, draft));
		}
		if (is_manager) {
			frm.add_custom_button(__("Publish"), () => publish(frm, draft)).addClass("btn-primary");
		}
	}

	if (is_manager && frm.doc.status === "Published") {
		frm.add_custom_button(__("Assign To…"), () => assign(frm), __("Learners"));
		frm.add_custom_button(__("Retire Course"), () => retire(frm), __("Learners"));
	}

	// The builder shipped in Phase 3, but this button went on saying it had not
	// for three releases — the placeholder outlived the thing it was standing in
	// for. Anyone who trusted it never found the builder at all.
	const $builder = frm.add_custom_button(__("Open Builder"), () => open_builder(frm));
	// Primary only when nothing else already is: a manager looking at a draft has
	// Publish highlighted, and two primary buttons side by side just make the
	// author guess which one is the safe click.
	if (draft && !is_manager) $builder.addClass("btn-primary");
}

function open_builder(frm) {
	// `handle_route` reads `course` from the query string first and falls back to
	// route_options, so this works whether the page is already mounted or is being
	// opened cold.
	frappe.route_options = { course: frm.doc.name };
	frappe.set_route("training-builder");
}

function render_status_banner(frm, draft) {
	if (frm.doc.status === "Published" && draft) {
		frm.dashboard.set_headline(
			__("Learners are on the published version. Draft V{0} is not visible to them until it is published.", [
				draft.version_number,
			])
		);
	}
}

function new_draft(frm) {
	frappe.call({
		method: "erpnext_enhancements.api.training_author.create_draft_version",
		args: { course: frm.doc.name },
		freeze: true,
		freeze_message: __("Copying the current version…"),
		callback(r) {
			if (r.message) frappe.set_route("Form", "Training Course Version", r.message);
		},
	});
}

function submit_for_review(frm, draft) {
	frappe.prompt(
		[{ fieldname: "notes", fieldtype: "Small Text", label: __("What changed?"), reqd: 1 }],
		(values) => {
			frappe.call({
				method: "erpnext_enhancements.api.training_author.submit_for_review",
				args: { course_version: draft.name, notes: values.notes },
				freeze: true,
				callback() {
					frappe.show_alert({ message: __("Sent for review"), indicator: "green" });
					frm.reload_doc();
				},
			});
		},
		__("Send For Review"),
		__("Send")
	);
}

function publish(frm, draft) {
	// The Minor/Material choice is the whole versioning contract, so it is asked
	// as a deliberate question with the consequence spelled out — not a quiet
	// default somebody clicks past.
	frappe.prompt(
		[
			{
				fieldname: "change_type",
				fieldtype: "Select",
				label: __("What kind of change is this?"),
				reqd: 1,
				options: [
					"Minor Edit (keep completions)",
					"Material Change (require retake)",
				].join("\n"),
				description: __(
					"A minor edit — a typo, clearer wording, a better image — leaves everyone's completion valid. A material change means the course now teaches something different, so previous completions are superseded and everyone is asked to take it again."
				),
			},
			{
				fieldname: "release_notes",
				fieldtype: "Small Text",
				label: __("Release notes"),
				description: __("The only record of what changed. Write it for whoever asks in a year."),
			},
		],
		(values) => {
			frappe.call({
				method: "erpnext_enhancements.api.training_author.publish_version",
				args: {
					course_version: draft.name,
					change_type: values.change_type,
					release_notes: values.release_notes,
				},
				freeze: true,
				freeze_message: __("Publishing…"),
				callback(r) {
					const out = r.message || {};
					let msg = __("Published version {0}.", [out.version_number]);
					if (out.superseded_completions) {
						msg += " " + __("{0} completion(s) superseded and retakes assigned.", [
							out.superseded_completions,
						]);
					}
					frappe.msgprint({ title: __("Published"), indicator: "green", message: msg });
					frm.reload_doc();
				},
			});
		},
		__("Publish {0}", [`V${draft.version_number}`]),
		__("Publish")
	);
}

function assign(frm) {
	frappe.prompt(
		[
			{
				fieldname: "employees",
				fieldtype: "MultiSelectList",
				label: __("Employees"),
				reqd: 1,
				get_data(txt) {
					return frappe.db.get_link_options("Employee", txt, { status: "Active" });
				},
			},
			{ fieldname: "due_date", fieldtype: "Date", label: __("Due date") },
		],
		(values) => {
			frappe.call({
				method: "erpnext_enhancements.api.training_author.assign_course",
				args: {
					course: frm.doc.name,
					employees: values.employees,
					due_date: values.due_date,
				},
				freeze: true,
				callback(r) {
					const out = r.message || {};
					frappe.show_alert({
						message: out.queued
							? __("Assigning {0} people in the background…", [out.queued])
							: __("{0} assignment(s) created", [out.created || 0]),
						indicator: "green",
					});
				},
			});
		},
		__("Assign This Course"),
		__("Assign")
	);
}

function retire(frm) {
	frappe.prompt(
		[{ fieldname: "reason", fieldtype: "Small Text", label: __("Why?"), reqd: 1 }],
		(values) => {
			frappe.confirm(
				__("Retire this course and cancel every outstanding assignment on it?"),
				() => {
					frappe.call({
						method: "erpnext_enhancements.api.training_author.retire_course",
						args: { course: frm.doc.name, reason: values.reason },
						freeze: true,
						callback(r) {
							const out = r.message || {};
							frappe.msgprint(
								__("Retired. {0} outstanding assignment(s) cancelled.", [
									out.cancelled_assignments || 0,
								])
							);
							frm.reload_doc();
						},
					});
				}
			);
		},
		__("Retire Course"),
		__("Retire")
	);
}
