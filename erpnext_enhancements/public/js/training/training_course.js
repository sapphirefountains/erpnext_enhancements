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
	Position: "Position",
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
		if (frm.is_new()) {
			// The AI-authoring door works from a blank form too — it creates a NEW
			// course rather than editing this one. render_actions (below) is skipped
			// for new docs, so the button is added here for that case.
			add_triton_authoring_button(frm);
			return;
		}

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
	// The visual editor first, because it is the one an author who is not a
	// developer can use, and until v1.386.0 NOTHING linked to it at all -- grepping
	// for "training-canvas" outside its own directory returned two CHANGELOG lines
	// and its test file. It shipped, and the only way to reach it was to know the
	// URL and type it.
	const $canvas = frm.add_custom_button(__("Edit Visually"), () => open_canvas(frm));
	const $builder = frm.add_custom_button(__("Open Builder"), () => open_builder(frm));
	// Primary only when nothing else already is: a manager looking at a draft has
	// Publish highlighted, and two primary buttons side by side just make the
	// author guess which one is the safe click.
	if (draft && !is_manager) $canvas.addClass("btn-primary");

	// Added after clear_custom_buttons() (which would otherwise wipe it) so it
	// survives on an existing course too.
	add_triton_authoring_button(frm);
}

// A door into AI authoring: open the Triton assistant primed to draft a NEW
// course. The write still happens in ERPNext behind the review gate — Triton
// proposes a Course Spec and calls `author_training_course`, which creates an
// unpublished draft with every quiz question flagged for review — so this is only
// the way in, never a second authoring path. Shown to authors only, and only where
// the Triton widget is present (the global is defined by the app_include_js
// widget bundle whenever it loaded).
function add_triton_authoring_button(frm) {
	if (!frappe.user.has_role(["Training Author", "Training Manager", "System Manager"])) return;
	if (!(window.SapphireTriton && window.SapphireTriton.ask)) return;
	frm.add_custom_button(__("Draft a course with Triton AI"), () => {
		window.SapphireTriton.ask(
			__(
				"I'd like to author a new training course. Ask me what it should cover and any specifics, then draft it with draft_course_spec; once I'm happy, create it with author_training_course. It will be created as an unpublished draft for me to review before publishing."
			)
		);
	});
}

function open_canvas(frm) {
	// Same route_options handshake as the builder below, and the same reason: the
	// page reads `course` from the query string first and falls back to
	// route_options, so this works whether it is already mounted or opened cold.
	frappe.route_options = { course: frm.doc.name };
	frappe.set_route("training-canvas");
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

// Assigning a group is the thing that was missing, and it is why nobody was ever
// assigned anything: the engine has been complete since v1.207.0 and the only way
// to point it at a group was to hand-add a rule to a child table on this form and
// wait for a scheduled sweep. Prod carried zero rules and five assignments, ever.
//
// The dialog previews before it acts. "Assign to Production" reads identically
// whether Production has four people in it or none, and assigning fifteen people
// is not something anybody should do blind.
const ASSIGN_GROUPS = ["All Employees", "Department", "Designation", "Position", "Role Profile"];

function assign(frm) {
	const dialog = new frappe.ui.Dialog({
		title: __("Assign This Course"),
		fields: [
			{
				fieldname: "mode",
				fieldtype: "Select",
				label: __("Assign to"),
				options: [__("These people"), __("A whole group")].join("\n"),
				default: __("These people"),
				reqd: 1,
			},
			{
				fieldname: "employees",
				fieldtype: "MultiSelectList",
				label: __("Employees"),
				depends_on: `eval:doc.mode === "${__("These people")}"`,
				get_data(txt) {
					return frappe.db.get_link_options("Employee", txt, { status: "Active" });
				},
			},
			{
				fieldname: "target_type",
				fieldtype: "Select",
				label: __("Group"),
				options: ASSIGN_GROUPS.join("\n"),
				depends_on: `eval:doc.mode === "${__("A whole group")}"`,
				onchange: () => preview(dialog),
			},
			{
				fieldname: "target_value",
				fieldtype: "Dynamic Link",
				label: __("Which one"),
				options: "target_type",
				depends_on: `eval:doc.mode === "${__("A whole group")}" && doc.target_type && doc.target_type !== "All Employees"`,
				onchange: () => preview(dialog),
			},
			{ fieldname: "preview", fieldtype: "HTML" },
			{ fieldname: "due_date", fieldtype: "Date", label: __("Due date") },
		],
		primary_action_label: __("Assign"),
		primary_action(values) {
			const group = values.mode === __("A whole group");
			if (!group && !(values.employees || []).length) {
				frappe.msgprint(__("Pick at least one person."));
				return;
			}
			if (group && !values.target_type) {
				frappe.msgprint(__("Pick a group."));
				return;
			}
			frappe.call({
				method: group
					? "erpnext_enhancements.api.training_author.assign_course_to_group"
					: "erpnext_enhancements.api.training_author.assign_course",
				args: group
					? {
							course: frm.doc.name,
							target_type: values.target_type,
							target_value: values.target_value,
							due_date: values.due_date,
					  }
					: {
							course: frm.doc.name,
							employees: values.employees,
							due_date: values.due_date,
					  },
				freeze: true,
				callback(r) {
					const out = r.message || {};
					dialog.hide();
					frappe.show_alert({
						message: out.queued
							? __("Assigning {0} people in the background…", [out.queued])
							: __("{0} assignment(s) created", [out.created || 0]),
						indicator: "green",
					});
				},
			});
		},
	});
	dialog.show();
}

// Names them before the button is pressed. Resolved server-side through the same
// map the auto-assign engine uses, so the preview and the engine cannot disagree
// about what "every Junior Technician" means.
function preview(dialog) {
	const values = dialog.get_values(true) || {};
	const wrapper = dialog.fields_dict.preview.$wrapper;
	if (!values.target_type || (values.target_type !== "All Employees" && !values.target_value)) {
		wrapper.empty();
		return;
	}
	frappe.call({
		method: "erpnext_enhancements.api.training_author.resolve_assignment_group",
		args: { target_type: values.target_type, target_value: values.target_value },
		callback(r) {
			const users = (r.message || {}).users || [];
			wrapper.empty();
			const box = $('<div class="text-muted small"></div>').appendTo(wrapper);
			if (!users.length) {
				// Said plainly rather than left blank. An empty group and an
				// unselected one look the same, and only one of them is a mistake.
				box.text(__("Nobody is in that group right now."));
				return;
			}
			box.text(__("{0} people: {1}", [users.length, users.join(", ")]));
		},
	});
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
