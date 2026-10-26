/**
 * Sapphire Maintenance Contract form script.
 *
 * Built for fast fill-out:
 * - Service Plan stamp: picking a plan copies its defaults (frequency,
 *   template, visit shape, invoicing, seasonal startup/winterization) onto
 *   the contract in one dropdown selection. Clearing the plan changes
 *   nothing — it's a stamp, not a live link.
 * - "Add Water Features" grid button: batch-adds the project's water
 *   features (all pre-checked) with one shared frequency and first-visit
 *   date, instead of row-by-row grid entry.
 * - New feature rows inherit the contract's Visit Frequency and anchor
 *   their next visit to the Start Date.
 * - Serial No picks are filtered to the configured water-feature Item and,
 *   when a Project is set, to that project's serials; warehouses to
 *   non-group leaves.
 * - "View Agreement" reads the signed Maintenance Services Agreement this
 *   contract was mapped from, in full (public/js/project_enhancements/
 *   contract_viewer.js) — the legal language lives there, not here.
 * - "Signed copy" beside it, when that agreement is Signed: the signed PDF,
 *   else its executed copy (v1.744.0). The server sends the answer with the
 *   form (__onload.signed_copy), and only to a reader of the agreement.
 */
frappe.ui.form.on("Sapphire Maintenance Contract", {
	setup(frm) {
		frm.set_query("serial_no", "covered_features", function () {
			const filters = {};
			if (frm._ee_water_feature_item) {
				filters.item_code = frm._ee_water_feature_item;
			}
			if (frm.doc.project) {
				filters.custom_project = frm.doc.project;
			}
			return { filters: filters };
		});
		frm.set_query("default_warehouse", "covered_features", function () {
			return { filters: { is_group: 0, disabled: 0 } };
		});
		frm.set_query("service_plan", function () {
			return { filters: { disabled: 0 } };
		});
		// From the boot payload, not frappe.db.get_single_value: that needs read on
		// ERPNext Enhancements Settings, and everyone but a System Manager got "No permission
		// for ERPNext Enhancements Settings" each time this form opened (boot.py).
		frm._ee_water_feature_item = (frappe.boot.ee_item_pickers || {}).water_feature_item;
	},

	refresh(frm) {
		if (frm.doc.status === "Active") {
			frm.page.set_indicator(__("Active"), "green");
		} else if (frm.doc.status === "Expired" || frm.doc.status === "Canceled") {
			frm.page.set_indicator(__(frm.doc.status), "red");
		}

		// This is the operational contract — the schedule, not the language.
		// When it came from a signed Maintenance Services Agreement, offer that
		// agreement's full text without a trip to another form.
		if (frm.doc.project_contract) {
			frm.add_custom_button(__("View Agreement"), () =>
				erpnext_enhancements.contract_viewer.open({
					name: frm.doc.project_contract,
					template_key: "maintenance",
					print_name: frm.doc.project_contract,
				})
			);
			const signed_copy = frm.doc.__onload && frm.doc.__onload.signed_copy;
			if (signed_copy) {
				frm.add_custom_button(__("Signed copy"), () =>
					erpnext_enhancements.contract_viewer.open_signed_copy(signed_copy, {
						template_key: "maintenance",
						on_filed: () => frm.reload_doc(),
					})
				);
			}
		}

		// Which visit form each kind of visit opens with (v1.620.0): the regular visit, a water
		// feature's own form, each seasonal visit, and where a blank link falls back to. Resolved
		// on the server by the function a visit's first open uses (get_visit_forms).
		if (!frm.is_new()) {
			frm.add_custom_button(__("Visit Forms"), () => ee_show_visit_forms(frm));
		}

		if (frm.doc.docstatus === 0 && !frm._ee_batch_button) {
			frm._ee_batch_button = frm.fields_dict.covered_features.grid.add_custom_button(
				__("Add Water Features"),
				() => ee_batch_add_features(frm)
			);
		}
	},

	// Stamp the plan's defaults onto the contract. One pick fills frequency,
	// template, visit shape, invoicing and the seasonal pair; row frequencies
	// backfill via the default_frequency trigger below.
	service_plan(frm) {
		if (!frm.doc.service_plan) return;
		frappe.db.get_doc("Sapphire Service Plan", frm.doc.service_plan).then((plan) => {
			if (plan.default_frequency) frm.set_value("default_frequency", plan.default_frequency);
			if (plan.default_template) frm.set_value("default_template", plan.default_template);
			if (plan.visit_shape) frm.set_value("visit_shape", plan.visit_shape);
			if (plan.invoicing_frequency) frm.set_value("invoicing_frequency", plan.invoicing_frequency);

			frm.set_value("seasonal_startup", plan.seasonal_startup ? 1 : 0);
			if (plan.seasonal_startup) {
				frm.set_value("startup_month", plan.startup_month || "April");
				frm.set_value("startup_template", plan.startup_template);
			}
			frm.set_value("winterization", plan.winterization ? 1 : 0);
			if (plan.winterization) {
				frm.set_value("winterization_month", plan.winterization_month || "October");
				frm.set_value("winterization_template", plan.winterization_template);
			}

			frappe.show_alert({
				message: __("Applied the {0} plan defaults.", [frappe.bold(frm.doc.service_plan)]),
				indicator: "green",
			});
		});
	},

	// Blank rows inherit silently; rows that already chose a different
	// cadence are only overwritten after an explicit confirm.
	default_frequency(frm) {
		const frequency = frm.doc.default_frequency;
		if (!frequency) return;
		const rows = frm.doc.covered_features || [];
		rows.filter((row) => !row.frequency).forEach((row) => {
			frappe.model.set_value(row.doctype, row.name, "frequency", frequency);
		});
		const differing = rows.filter((row) => row.frequency && row.frequency !== frequency);
		if (differing.length) {
			frappe.confirm(
				__("Apply {0} to all {1} feature rows?", [frappe.bold(frequency), rows.length]),
				() => {
					differing.forEach((row) => {
						frappe.model.set_value(row.doctype, row.name, "frequency", frequency);
					});
				}
			);
		}
	},
});

frappe.ui.form.on("Sapphire Contract Feature", {
	covered_features_add(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.frequency && frm.doc.default_frequency) {
			frappe.model.set_value(cdt, cdn, "frequency", frm.doc.default_frequency);
		}
		if (!row.next_visit_date) {
			frappe.model.set_value(
				cdt,
				cdn,
				"next_visit_date",
				frm.doc.start_date || frappe.datetime.get_today()
			);
		}
	},
});

// "Visit Forms": one table of which form each kind of visit opens with, each a link to the
// template. Saved values only (the server reads the contract), so unsaved edits are flagged.
function ee_show_visit_forms(frm) {
	frappe
		.call({
			method: "erpnext_enhancements.sapphire_maintenance.doctype.sapphire_maintenance_contract.sapphire_maintenance_contract.get_visit_forms",
			args: { contract: frm.doc.name },
		})
		.then((r) => {
			const esc = (value) => frappe.utils.escape_html(String(value == null ? "" : value));
			const rows = (r.message || [])
				.map((row) => {
					const form = row.template
						? `<a href="${esc(frappe.utils.get_form_link("Sapphire Maintenance Template", row.template))}">${esc(
								row.template
						  )}</a>`
						: `<span class="text-muted">${__("None")}</span>`;
					return `<tr><td>${esc(row.visit)}</td><td>${form}</td><td class="text-muted">${esc(row.source)}</td></tr>`;
				})
				.join("");
			const unsaved = frm.is_dirty()
				? `<p class="text-warning">${__("This shows the saved contract. Save to see your changes here.")}</p>`
				: "";
			frappe.msgprint({
				title: __("Visit Forms"),
				wide: true,
				message: `${unsaved}<table class="table table-bordered table-sm"><thead><tr><th>${__("Visit")}</th><th>${__(
					"Form"
				)}</th><th>${__("Where it comes from")}</th></tr></thead><tbody>${rows}</tbody></table>`,
			});
		});
}

// "Add Water Features" — multi-select the project's water features (all
// pre-checked, native Select All) with one shared frequency and first-visit
// date. A 12-fountain contract becomes three clicks instead of twelve rows.
function ee_batch_add_features(frm) {
	if (!frm.doc.project) {
		frappe.msgprint(__("Set a Project first — the feature list comes from the project's water features."));
		return;
	}

	frappe.call({
		method:
			"erpnext_enhancements.sapphire_maintenance.doctype.sapphire_maintenance_contract.sapphire_maintenance_contract.get_project_water_features",
		args: { project: frm.doc.project },
		callback(r) {
			const covered = new Set((frm.doc.covered_features || []).map((row) => row.serial_no));
			const available = (r.message || []).filter((feature) => !covered.has(feature.value));
			if (!available.length) {
				frappe.msgprint(
					__("All of this project's water features are already covered (or the project has none).")
				);
				return;
			}

			const dialog = new frappe.ui.Dialog({
				title: __("Add Water Features"),
				fields: [
					{
						fieldname: "features",
						fieldtype: "MultiCheck",
						label: __("Water Features"),
						select_all: 1,
						columns: 1,
						options: available.map((feature) => ({
							label: feature.description
								? `${feature.value} — ${feature.description}`
								: feature.value,
							value: feature.value,
							checked: 1,
						})),
					},
					{ fieldtype: "Section Break" },
					{
						fieldname: "frequency",
						fieldtype: "Select",
						label: __("Frequency"),
						options: "\nDaily\nWeekly\nBi-Weekly\nMonthly\nQuarterly\nYearly",
						default: frm.doc.default_frequency,
					},
					{ fieldtype: "Column Break" },
					{
						fieldname: "first_visit_date",
						fieldtype: "Date",
						label: __("First Visit"),
						default: frm.doc.start_date || frappe.datetime.get_today(),
					},
				],
				primary_action_label: __("Add"),
				primary_action(values) {
					const selected = dialog.fields_dict.features.get_checked_options();
					if (!selected.length) {
						frappe.msgprint(__("Pick at least one water feature."));
						return;
					}
					selected.forEach((serial_no) => {
						const row = frm.add_child("covered_features");
						row.serial_no = serial_no;
						row.frequency = values.frequency || frm.doc.default_frequency;
						row.next_visit_date = values.first_visit_date;
					});
					frm.refresh_field("covered_features");
					dialog.hide();
					frappe.show_alert({
						message: __("Added {0} water features.", [selected.length]),
						indicator: "green",
					});
				},
			});
			dialog.show();
		},
	});
}
