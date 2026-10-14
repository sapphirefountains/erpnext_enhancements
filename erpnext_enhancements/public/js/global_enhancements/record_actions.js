/*
 * Record actions the Desk form's buttons and the guided page's Actions menu share (v1.686.0).
 *
 * Until v1.686.0 each lived inside the form script that drew its button, a doctype_js that only a
 * Desk form loads, so the guided page (the default way a record opens) had none of them. They moved
 * here unchanged and the form scripts call them:
 *
 *   open_drive_folder(folder_id, missing)   drive_folder_button.js's "Open Drive Folder" / "Drive
 *                                           Folder Missing"
 *   open_reminder({doctype, name, title})   project_enhancements/doctype/opportunity/opportunity.js's
 *                                           "Set Reminder" (a ToDo for the person, dated)
 *   contract_types(doctype)                 contracts.js's "Generate Contract": the agreements a
 *   can_generate_contract(doctype)          record of this DocType generates, and whether the suite
 *   open_generate_contract(doctype, name)   is on; then its dialog
 *
 * erpnext_enhancements.bundle.js installs them as erpnext_enhancements.record_actions for the form
 * scripts (which cannot import); the guided page's bundle imports them. Each function returns the
 * frappe Dialog it opened (or null), so the guided page can close it on Back (party.js track).
 * Nothing here runs at import time.
 */

const CONTRACT_METHODS = "erpnext_enhancements.project_enhancements.doctype.project_contract.project_contract";

// ------------------------------------------------------------------ Drive folder

export function drive_folder_url(folder_id) {
	return `https://drive.google.com/drive/folders/${encodeURIComponent(folder_id)}`;
}

/**
 * A folder ID outlives the folder. Delete or move one in Drive and the ID points at nothing, so the
 * link would hand the person a bare Google 404 that reads like ERPNext is broken. The daily reconcile
 * (google_drive.drive_sync) sets custom_drive_folder_missing; then this explains instead of opening.
 */
export function open_drive_folder(folder_id, missing) {
	if (!folder_id) return null;
	if (missing) {
		return frappe.msgprint({
			title: __("Drive Folder Missing"),
			indicator: "orange",
			message: __(
				"The Google Drive folder linked to this record no longer exists, or is no longer shared with the Drive service account. Opening it would show a Google 404.<br><br>Folder ID: <code>{0}</code><br><br>Either re-link this record in the <a href='/desk/drive-link-manager'>Drive Link Manager</a>, or restore the folder from the Shared Drive trash and press <b>Check Drive Links</b> on Project Folder Google Drive Settings.",
				[frappe.utils.escape_html(folder_id)]
			),
		});
	}
	window.open(drive_folder_url(folder_id), "_blank");
	return null;
}

// ------------------------------------------------------------------ Set Reminder

/** A ToDo for the person, on the record, dated when they asked to be reminded. */
export function open_reminder(opts) {
	const o = opts || {};
	const d = new frappe.ui.Dialog({
		title: __("Create a Reminder"),
		fields: [
			{
				label: "Remind Me In",
				fieldname: "remind_in",
				fieldtype: "Select",
				options: [
					{ label: "30 Minutes", value: 30 },
					{ label: "1 Hour", value: 60 },
					{ label: "2 Hours", value: 120 },
					{ label: "4 Hours", value: 240 },
					{ label: "Tomorrow Morning", value: "tomorrow" },
				],
				onchange: function () {
					var choice = this.get_value();
					if (!choice) return;

					var new_time;
					if (choice === "tomorrow") {
						new_time = moment()
							.add(1, "days")
							.set({ hour: 9, minute: 0, second: 0 })
							.format("YYYY-MM-DD HH:mm:ss");
					} else {
						new_time = moment().add(choice, "minutes").format("YYYY-MM-DD HH:mm:ss");
					}
					d.set_value("remind_at", new_time);
				},
			},
			{
				fieldtype: "Column Break",
			},
			{
				label: "Remind At",
				fieldname: "remind_at",
				fieldtype: "Datetime",
				reqd: 1,
				default: frappe.datetime.now_datetime(),
			},
			{
				fieldtype: "Section Break",
			},
			{
				label: "Description",
				fieldname: "description",
				fieldtype: "Small Text",
				reqd: 1,
				default: `Reminder for ${o.doctype}: ${o.title || o.name}`,
			},
		],
		primary_action_label: __("Create"),
		primary_action: function (values) {
			frappe.db
				.insert({
					doctype: "ToDo",
					reference_type: o.doctype,
					reference_name: o.name,
					description: values.description,
					date: values.remind_at,
					allocated_to: frappe.session.user,
					status: "Open",
				})
				.then((doc) => {
					d.hide();
					frappe.show_alert({
						message: __("Reminder created successfully"),
						indicator: "green",
					});
					if (typeof o.after_insert === "function") o.after_insert(doc);
				});
		},
	});

	d.show();
	return d;
}

// ------------------------------------------------------------------ Generate Contract

// Customer agreements (Owner / Rental / Maintenance) generate from Opportunity or Project;
// subcontractor agreements (MSA / SOW / NDA) generate from Supplier. An SOW can also be generated from
// a Project/Opportunity by picking the subcontractor in the dialog, which prefills the scope of work
// from the source's request/deliverable tables.
const CONTRACT_SOURCES = { Opportunity: "customer", Project: "customer", Supplier: "supplier" };

export function contract_types(doctype) {
	const kind = CONTRACT_SOURCES[doctype];
	if (kind === "customer") {
		return [
			{ value: "owner", label: __("Owner Contract (Design / Build / Maintain)") },
			{ value: "rental", label: __("Rental Agreement") },
			{ value: "maintenance", label: __("Maintenance Services Agreement") },
			{ value: "sow", label: __("Statement of Work (subcontractor — requires signed MSA)") },
		];
	}
	if (kind === "supplier") {
		return [
			{ value: "msa", label: __("Master Subcontractor Agreement") },
			{ value: "sow", label: __("Statement of Work (requires signed MSA)") },
			{ value: "nda", label: __("Mutual Non-Disclosure Agreement") },
		];
	}
	return [];
}

// Master switch: the suite is dormant until enabled in ERPNext Enhancements Settings (a boot flag;
// the server's guard is the authority).
export function can_generate_contract(doctype) {
	return !!(frappe.boot && frappe.boot.ee_process_automation) && contract_types(doctype).length > 0;
}

// The new draft opens in the Contract Builder (v1.611.0): a step-by-step walk through its fields with
// the agreement previewed beside them, ending in sign-here or send. The full form is one click away.
export function create_contract(template, source_doctype, source_name, extra) {
	return frappe
		.call(`${CONTRACT_METHODS}.create_contract`, {
			template: template,
			source_doctype: source_doctype,
			source_name: source_name,
			...(extra || {}),
		})
		.then((r) => frappe.set_route("contract-builder", r.message));
}

// Sequencing rule from the Jun 9 follow-up: a Statement of Work requires a Signed Master
// Subcontractor Agreement. Proceed when one exists, otherwise offer to create the MSA instead.
export function create_sow_with_gate(supplier, source_doctype, source_name) {
	return frappe.call(`${CONTRACT_METHODS}.get_signed_msa`, { supplier: supplier }).then((r) => {
		if (r.message) {
			create_contract("sow", source_doctype, source_name, { party: supplier });
		} else {
			frappe.confirm(
				__(
					"No signed Master Subcontractor Agreement exists for {0} yet — an SOW can only be issued under a signed MSA. Create the MSA now?",
					[frappe.utils.escape_html(supplier).bold()]
				),
				() => create_contract("msa", "Supplier", supplier)
			);
		}
	});
}

export function open_generate_contract(source_doctype, source_name) {
	const types = contract_types(source_doctype);
	if (!types.length || !source_name) return null;
	const d = new frappe.ui.Dialog({
		title: __("Generate Contract"),
		fields: [
			{
				fieldname: "template",
				fieldtype: "Select",
				label: __("Contract Type"),
				reqd: 1,
				options: types.map((t) => ({ value: t.value, label: t.label })),
			},
			{
				fieldname: "supplier",
				fieldtype: "Link",
				label: __("Subcontractor (Supplier)"),
				options: "Supplier",
				depends_on: 'eval:doc.template=="sow"',
				mandatory_depends_on: 'eval:doc.template=="sow"',
				description: __(
					"The SOW's scope of work prefils from this {0}'s Customer Requests and Deliverables tables.",
					[__(source_doctype)]
				),
			},
		],
		primary_action_label: __("Create Draft"),
		primary_action(values) {
			d.hide();
			if (values.template === "sow") {
				const supplier = source_doctype === "Supplier" ? source_name : values.supplier;
				create_sow_with_gate(supplier, source_doctype, source_name);
			} else {
				create_contract(values.template, source_doctype, source_name);
			}
		},
	});
	if (source_doctype === "Supplier") {
		d.set_df_property("supplier", "hidden", 1);
		d.set_df_property("supplier", "mandatory_depends_on", "");
	}
	d.show();
	return d;
}
