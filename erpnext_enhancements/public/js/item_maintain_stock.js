// "Turn on Maintain Stock" on the Item form (v1.754.0).
//
// ERPNext's Item form refuses to change Maintain Stock once any submitted document uses the item
// ("Linked with submitted documents", Item.cant_change). This action shows the result of each
// safety check (api/maintain_stock.check) and, only when they all pass, a confirm switches it on
// the server (api/maintain_stock.turn_on), which runs every check again under a row lock. Why that
// is safe, and what it refuses: inventory_enhancements/maintain_stock_rules.py.
//
// Two ways in:
//   * Actions > Turn on Maintain Stock, on a saved Item that keeps no stock.
//   * Save, after ticking Maintain Stock on such an Item. When ERPNext's own form would refuse (the
//     check names the document it would name), the save stops before ERPNext's message and the
//     dialog opens instead; after the switch, the save the person asked for finishes. When ERPNext
//     would not refuse, or the check can't be reached, the save goes on untouched.
//
// SWITCH_ROLES copies maintain_stock_rules.SWITCH_ROLES, and tests/test_maintain_stock.py fails the
// build when they differ. It only decides whether to show the button: the server is the gate.

(() => {
	// doctype_js can load a file more than once; registered twice, the button would appear twice.
	if (window.__sf_maintain_stock_registered) return;
	window.__sf_maintain_stock_registered = true;

	const SWITCH_ROLES = ["Stock Manager", "Item Manager"];
	const CHECK = "erpnext_enhancements.api.maintain_stock.check";
	const TURN_ON = "erpnext_enhancements.api.maintain_stock.turn_on";

	const esc = (value) => frappe.utils.escape_html(value == null ? "" : String(value));

	function may_switch() {
		return frappe.user.has_role(SWITCH_ROLES);
	}

	// Settles on frappe.call's own promise: v16 never calls `error` on a timeout or a deadlock, so a
	// dialog waiting on it could hang. A refusal resolves null; frappe.call has shown its message.
	function call(method, args, opts) {
		return new Promise((resolve) => {
			let pending;
			try {
				pending = frappe.call(Object.assign({ method, args }, opts || {}));
			} catch (e) {
				resolve(null);
				return;
			}
			if (!pending || typeof pending.then !== "function") {
				resolve(null);
				return;
			}
			pending.then(
				(data) => {
					let body = data;
					if (typeof body === "string") {
						try {
							body = JSON.parse(body);
						} catch (e) {
							body = null;
						}
					}
					resolve(body && body.message !== undefined ? body.message : null);
				},
				() => resolve(null)
			);
		});
	}

	// The Save path asks without a word to anyone: a check that fails or times out must leave the
	// save exactly as it was, so no popup of frappe.call's may appear (a plain GET, no freeze).
	function quiet_check(item_code) {
		const url = `/api/method/${CHECK}?item_code=${encodeURIComponent(item_code)}`;
		return fetch(url, { headers: { Accept: "application/json" }, credentials: "same-origin" })
			.then((response) => (response.ok ? response.json() : null))
			.then((body) => (body && body.message) || null)
			.catch(() => null);
	}

	function doc_link(example) {
		const url = frappe.utils.get_form_link(example.doctype, example.name);
		return `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(example.doctype)} ${esc(example.name)}</a>`;
	}

	function render(res) {
		const parts = [];
		const intro = res.locked_by
			? __("ERPNext's form won't change Maintain Stock here, because {0} uses this item.", [
					doc_link(res.locked_by),
			  ])
			: __("ERPNext's form won't change Maintain Stock once a submitted document uses an item.");
		parts.push(
			`<p>${intro} ${esc(
				__(
					"These checks look for every case where switching it would leave stock or accounts wrong. It switches only when none applies."
				)
			)}</p>`
		);
		parts.push('<div class="sf-maintain-stock-checks">');
		(res.checks || []).forEach((check) => {
			const examples = (check.examples || []).map(doc_link).join(", ");
			parts.push(
				`<div style="display:flex;gap:10px;align-items:flex-start;padding:8px 0;border-bottom:1px solid var(--border-color)">` +
					`<span class="indicator-pill ${check.ok ? "green" : "red"}" style="flex:none">${esc(
						check.ok ? __("Passes") : __("Fails")
					)}</span>` +
					`<div><div>${esc(check.label)}</div>` +
					(check.detail ? `<div class="text-muted small">${esc(check.detail)}</div>` : "") +
					(examples ? `<div class="small">${examples}</div>` : "") +
					`</div></div>`
			);
		});
		parts.push("</div>");
		const receipts = (res.receipts && res.receipts.count) || 0;
		if (res.can_switch) {
			if (receipts) {
				parts.push(
					`<p style="margin-top:12px">${esc(
						__(
							"Its {0} earlier receipt(s) stay as they are, as non-stock. This app refuses canceling, returning or billing them in ERPNext.",
							[receipts]
						)
					)}</p>`
				);
			}
			parts.push(
				`<p style="margin-top:12px"><strong>${esc(
					__("There's no button to turn it back off.")
				)}</strong></p>`
			);
		} else {
			parts.push(
				`<p style="margin-top:12px">${esc(
					__("Maintain Stock stays off. Fix what fails above, then run this again.")
				)}</p>`
			);
		}
		return parts.join("");
	}

	function open_reconciliation(out) {
		// frappe.new_doc's route options fill the header; the row needs the form, so it goes in once
		// the new form has loaded. Item and warehouse are set through set_value so ERPNext's own
		// triggers fetch the current quantity and rate (both 0) for the person to overwrite.
		frappe.route_hooks.after_load = (frm) => {
			if (frm.doctype !== "Stock Reconciliation" || !frm.is_new()) return;
			if (out.account && !frm.doc.expense_account) frm.set_value("expense_account", out.account);
			const row = (frm.doc.items || []).find((r) => !r.item_code) || frm.add_child("items");
			const values = { item_code: out.item_code };
			if (out.warehouse) values.warehouse = out.warehouse;
			frappe.model.set_value(row.doctype, row.name, values);
			frm.refresh_field("items");
		};
		const opts = { purpose: "Stock Reconciliation" };
		if (out.company) opts.company = out.company;
		if (out.account) opts.expense_account = out.account;
		frappe.new_doc("Stock Reconciliation", opts);
	}

	function show_next_steps(out) {
		const can_reconcile = frappe.model.can_create("Stock Reconciliation");
		const dialog = frappe.msgprint({
			title: __("Maintain Stock is on"),
			indicator: "green",
			message: esc(out.next_steps),
			primary_action: can_reconcile
				? {
						label: __("Open a Stock Reconciliation"),
						action: () => {
							const shown = dialog || frappe.msg_dialog;
							if (shown) shown.hide();
							open_reconciliation(out);
						},
				  }
				: undefined,
		});
	}

	function after_switch(frm, out, resume_save) {
		frm.__sf_saved_stock = 1;
		if (frm.is_dirty()) {
			// Other edits are still on the form. The switch moved `modified`, and a stale form is
			// refused on save, so take the server's flag and timestamp; then the edits can be saved.
			frm.doc.is_stock_item = 1;
			frm.doc.modified = out.modified;
			frm.refresh_field("is_stock_item");
			if (resume_save) {
				Promise.resolve(frm.save()).then(() => show_next_steps(out));
				return;
			}
			show_next_steps(out);
			frappe.show_alert({
				message: __("Your other changes to this item aren't saved yet."),
				indicator: "orange",
			});
			return;
		}
		Promise.resolve(frm.reload_doc()).then(() => show_next_steps(out));
	}

	function open_dialog(frm, res, resume_save) {
		const options = {
			title: __("Turn on Maintain Stock for {0}", [res.item_code]),
			size: "large",
			fields: [{ fieldtype: "HTML", fieldname: "checks" }],
			primary_action_label: res.can_switch ? __("Turn on Maintain Stock") : __("Close"),
			primary_action() {
				if (!res.can_switch) {
					dialog.hide();
					return;
				}
				dialog.get_primary_btn().prop("disabled", true);
				call(
					TURN_ON,
					{ item_code: res.item_code },
					{ freeze: true, freeze_message: __("Turning on Maintain Stock...") }
				).then((out) => {
					dialog.hide();
					// null: refused (every check runs again on the server); frappe.call said why.
					if (out) after_switch(frm, out, resume_save);
				});
			},
		};
		if (res.can_switch) {
			options.secondary_action_label = __("Cancel");
			options.secondary_action = () => dialog.hide();
		}
		const dialog = new frappe.ui.Dialog(options);
		dialog.fields_dict.checks.$wrapper.html(render(res));
		dialog.show();
	}

	function start(frm) {
		call(
			CHECK,
			{ item_code: frm.doc.name },
			{ type: "GET", freeze: true, freeze_message: __("Checking...") }
		).then((res) => {
			if (res) open_dialog(frm, res, false);
		});
	}

	frappe.ui.form.on("Item", {
		refresh(frm) {
			// What the database holds, so Save can tell a box ticked just now from one already on.
			frm.__sf_saved_stock = frm.is_new() ? null : cint(frm.doc.is_stock_item);
			if (frm.is_new() || cint(frm.doc.is_stock_item) || cint(frm.doc.is_fixed_asset)) return;
			if (!may_switch()) return;
			frm.add_custom_button(__("Turn on Maintain Stock"), () => start(frm), __("Actions"));
		},

		validate(frm) {
			if (frm.is_new() || !cint(frm.doc.is_stock_item) || frm.__sf_saved_stock !== 0) return;
			if (!may_switch()) return;
			return quiet_check(frm.doc.name).then((res) => {
				// Not locked (or not known): ERPNext's own save can make this change, so let it.
				if (!res || !res.locked_by) return;
				frappe.validated = false;
				open_dialog(frm, res, true);
			});
		},
	});
})();
