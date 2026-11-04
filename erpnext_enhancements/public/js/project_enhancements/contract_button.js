/**
 * @file The Contract button — the one way into a Project's or a Customer's agreements.
 * @description
 * Nikolas, 2026-11-03: "Add Contract button to Scope to view contract. If no contract is listed,
 * then it asks and opens the Contract Builder. Remove the contract tab in Project DocType and just
 * make that button the new primary way to view and create contracts." (v1.783.0; the Contracts
 * tab of v1.181.0–v1.745.0 is retired with its two Custom Fields on each form.)
 *
 * Project: a small bar at the top of the **Scope** tab, above the Tasks Tree. Customer: a button
 * in the page head (the form has no Scope tab).
 *
 * What a press does, decided from `get_contracts` at that moment rather than on every refresh:
 *
 *  - **one contract** — opens the Contract Builder on that contract's **review** step
 *    (`/desk/contract-builder/<contract>/review`): the whole agreement, its signature, sign or
 *    send. A Sapphire Maintenance Contract has no legal text of its own, so it opens its form.
 *  - **several** — a picker first: type, status, party, date, one row each; a maintenance
 *    contract row says "schedule" and opens read-only.
 *  - **none** — "No contract on this project yet. Create one?" and, on yes, the Builder's start
 *    screen with this project (or this customer) already filled into the start dialog
 *    (`frappe.route_options`, which the start screen consumes). Someone who cannot create a
 *    Project Contract, or a site with the suite switched off, gets the plain statement instead.
 *
 * Loaded via hooks.py `doctype_js` for Project and Customer.
 */

(function () {
	const M = "erpnext_enhancements.project_enhancements.doctype.project_contract.project_contract";
	const MAINTENANCE = "Sapphire Maintenance Contract";

	// Status → indicator colour, the same vocabulary both forms use.
	const STATUS_COLOR = {
		Draft: "gray",
		"Out for Signature": "orange",
		Signed: "green",
		Void: "red",
		Active: "green",
		Expired: "orange",
		Canceled: "red",
	};

	const esc = (value) => frappe.utils.escape_html(String(value == null ? "" : value));

	function noun(frm) {
		return frm.doctype === "Customer" ? __("customer") : __("project");
	}

	function open_row(row) {
		if (row.doctype_name === MAINTENANCE) {
			frappe.set_route("Form", MAINTENANCE, row.name);
			return;
		}
		frappe.set_route("contract-builder", row.name, "review");
	}

	function row_meta(row) {
		const bits = [];
		if (row.party_display) bits.push(esc(row.party_display));
		bits.push(esc(row.name));
		if (row.doctype_name === MAINTENANCE) {
			bits.push(__("schedule"));
			if (row.start_date) bits.push(frappe.datetime.str_to_user(row.start_date));
		} else if (row.signed_on) {
			bits.push(__("signed {0}", [frappe.datetime.str_to_user(row.signed_on)]));
		} else if (row.contract_date) {
			bits.push(__("dated {0}", [frappe.datetime.str_to_user(row.contract_date)]));
		}
		return bits.join(" &middot; ");
	}

	function pick(frm, rows) {
		const d = new frappe.ui.Dialog({
			title: __("Which contract?"),
			fields: [{ fieldname: "list", fieldtype: "HTML" }],
		});
		const $list = $('<div class="ee-contract-pick"></div>');
		rows.forEach((row, i) => {
			const color = STATUS_COLOR[row.status] || "gray";
			$(`
				<div class="ee-contract-pick-row" role="button" tabindex="0" data-index="${i}">
					<div class="ee-contract-pick-main">
						<div class="ee-contract-pick-title">${esc(row.type_label || row.doctype_name)}</div>
						<div class="ee-contract-pick-meta">${row_meta(row)}</div>
					</div>
					<span class="indicator-pill ${color}"><span>${esc(row.status || __("Draft"))}</span></span>
				</div>
			`)
				.on("click keydown", (event) => {
					if (event.type === "keydown" && event.key !== "Enter") return;
					d.hide();
					open_row(rows[i]);
				})
				.appendTo($list);
		});
		d.fields_dict.list.$wrapper.html(`
			<style>
				.ee-contract-pick-row {
					display: flex; align-items: center; gap: 10px; padding: 10px 12px; cursor: pointer;
					border: 1px solid var(--border-color); border-radius: 8px; margin-bottom: 8px;
					background: var(--fg-color);
				}
				.ee-contract-pick-row:hover, .ee-contract-pick-row:focus { background: var(--bg-color); outline: none; }
				.ee-contract-pick-main { flex: 1 1 auto; min-width: 0; }
				.ee-contract-pick-title { font-weight: 600; color: var(--heading-color); }
				.ee-contract-pick-meta { font-size: 12px; color: var(--text-muted); }
			</style>
		`);
		d.fields_dict.list.$wrapper.append($list);
		d.show();
	}

	function offer_new(frm) {
		const can_create =
			!!(frappe.boot && frappe.boot.ee_process_automation) && frappe.model.can_create("Project Contract");
		if (!can_create) {
			frappe.msgprint(__("No contract is listed on this {0}.", [noun(frm)]));
			return;
		}
		frappe.confirm(__("No contract on this {0} yet. Create one?", [noun(frm)]), () => {
			// The start screen consumes these: the start dialog of whichever type is picked opens
			// with this record filled in (contract_builder.js show_start / start_dialog).
			frappe.route_options =
				frm.doctype === "Customer" ? { customer: frm.doc.name } : { project: frm.doc.name };
			frappe.set_route("contract-builder");
		});
	}

	function on_press(frm) {
		if (frm.is_new()) {
			frappe.msgprint(__("Save this {0} first.", [noun(frm)]));
			return;
		}
		frappe
			.call({
				method: `${M}.get_contracts`,
				args: { source_doctype: frm.doctype, source_name: frm.doc.name },
				freeze: true,
				freeze_message: __("Looking up the contract…"),
			})
			.then((r) => {
				const rows = r.message || [];
				if (!rows.length) return offer_new(frm);
				if (rows.length === 1) return open_row(rows[0]);
				pick(frm, rows);
			});
	}

	function scope_tab(frm) {
		const tabs = (frm.layout && frm.layout.tabs) || [];
		return tabs.find((tab) => tab.df && tab.df.fieldname === "custom_scope") || null;
	}

	// The bar sits at the top of the Scope tab pane, above the Tasks Tree. Rebuilt on every
	// refresh (the pane survives a reload of the same form), never duplicated.
	function render_scope_bar(frm) {
		const tab = scope_tab(frm);
		if (!tab || !tab.wrapper) return;
		tab.wrapper.find(".ee-contract-bar").remove();
		if (frm.is_new()) return;
		if (!document.getElementById("ee-contract-bar-style")) {
			$("<style id='ee-contract-bar-style'></style>")
				.text(
					".ee-contract-bar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap;" +
						" padding: 8px 0 10px; margin: 0 0 4px; border-bottom: 1px solid var(--border-color); }" +
						".ee-contract-bar .ee-contract-btn .icon { margin-right: 4px; }" +
						".ee-contract-bar .ee-contract-hint { font-size: 12px; color: var(--text-muted); }"
				)
				.appendTo(document.head);
		}
		const $bar = $(`
			<div class="ee-contract-bar">
				<button type="button" class="btn btn-default btn-sm ee-contract-btn">
					${frappe.utils.icon("file", "sm")} <span>${__("Contract")}</span>
				</button>
				<span class="ee-contract-hint">${__("View this project's agreement, or create one.")}</span>
			</div>
		`);
		$bar.find(".ee-contract-btn").on("click", () => on_press(frm));
		tab.wrapper.prepend($bar);
	}

	frappe.ui.form.on("Project", {
		refresh(frm) {
			render_scope_bar(frm);
		},
	});

	frappe.ui.form.on("Customer", {
		refresh(frm) {
			if (frm.is_new()) return;
			frm.add_custom_button(__("Contract"), () => on_press(frm));
		},
	});
})();
