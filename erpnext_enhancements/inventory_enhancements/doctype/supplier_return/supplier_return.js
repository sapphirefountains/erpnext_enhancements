// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

/**
 * Supplier Return form (v1.807.0).
 *
 * The status is never typed: each step is a button, and the buttons offered are the ones the
 * server will accept from this person (`api/supplier_returns.state`). Every write is a POST to
 * `api/supplier_returns.py`; the rules behind them are `supplier_return_rules.py`.
 *
 * Nothing here pushes a history entry or changes the route except to open another document the
 * person asked for, so Back and Forward behave as on any form. Dialogs are frappe's own.
 *
 * The email to the supplier goes only from the Send button of the Send Return Request dialog,
 * after the person has seen the recipient, the subject and the message.
 */

const SR_API = "erpnext_enhancements.api.supplier_returns.";

const SR_WAITING = {
	us: __("Waiting on us"),
	supplier: __("Waiting on the supplier"),
	money: __("Waiting on money"),
	replacement: __("Waiting on the replacement"),
};

const SR_METHODS = ["Carrier", "Dropped off", "Supplier pickup"];
const SR_FINAL = [
	"Denied",
	"Refund Received",
	"Credit Received",
	"Replacement Received",
	"Closed Without Credit",
	"Canceled",
];
const SR_LOCKED_WHEN_FINAL = [
	"requested_resolution",
	"tax_amount",
	"restocking_fee",
	"return_shipping_cost",
	"shipping_deducted",
	"return_method",
	"carrier",
	"tracking_number",
	"supplier_return_number",
	"supplier_response_notes",
	"contact_email",
	"request_notes",
];
const SR_WAYS = ["Phone", "In person", "Supplier's website"];

function sr_call(method, args, freeze_message) {
	return frappe.call({ method: SR_API + method, type: "POST", args, freeze: !!freeze_message, freeze_message });
}

function sr_step(frm, method, args, freeze_message) {
	return sr_call(method, Object.assign({ name: frm.doc.name }, args), freeze_message).then((r) => {
		frm.reload_doc();
		return r && r.message;
	});
}

function sr_prompt(frm, title, fields, primary, method, map) {
	const dialog = new frappe.ui.Dialog({
		title,
		fields,
		primary_action_label: primary,
		primary_action(values) {
			dialog.disable_primary_action();
			sr_step(frm, method, map ? map(values) : values)
				.then(() => dialog.hide())
				.catch(() => dialog.enable_primary_action());
		},
	});
	dialog.show();
	return dialog;
}

function sr_days(n) {
	if (n === null || n === undefined) return "";
	if (n === 0) return __("since today");
	return n === 1 ? __("for 1 day") : __("for {0} days", [n]);
}

function sr_fill_from_receipt(frm) {
	if (!frm.doc.purchase_receipt) {
		frappe.msgprint(__("Pick the Purchase Receipt the items came in on first."));
		return;
	}
	frappe
		.call({
			method: SR_API + "lines_for_receipt",
			args: { purchase_receipt: frm.doc.purchase_receipt, name: frm.is_new() ? null : frm.doc.name },
		})
		.then((r) => {
			const data = r.message || {};
			if (!(data.lines || []).length) {
				frappe.msgprint(__("Nothing on {0} can still be returned.", [frm.doc.purchase_receipt]));
				return;
			}
			frm.set_value("supplier", data.supplier);
			frm.set_value("company", data.company);
			if (!frm.doc.contact_email && data.contact_email) frm.set_value("contact_email", data.contact_email);
			frm.clear_table("items");
			for (const line of data.lines) {
				const row = frm.add_child("items");
				Object.assign(row, {
					purchase_receipt_item: line.purchase_receipt_item,
					item_code: line.item_code,
					item_name: line.item_name,
					qty: line.returnable,
					uom: line.uom,
					conversion_factor: line.conversion_factor,
					rate: line.rate,
					amount: flt(line.returnable) * flt(line.rate),
					project: line.project,
					warehouse: line.warehouse,
					purchase_order: line.purchase_order,
					purchase_order_item: line.purchase_order_item,
					line_kind: line.line_kind,
					received_qty: line.received_qty,
					already_returned_qty: line.already_returned_qty,
				});
			}
			frm.refresh_field("items");
			frappe.show_alert({
				message: __("Lines added at the most that can go back. Lower the quantities and delete what stays."),
				indicator: "blue",
			});
		});
}

function sr_send_dialog(frm) {
	frappe.call({ method: SR_API + "request_draft", args: { name: frm.doc.name } }).then((r) => {
		const draft = r.message || {};
		const options = (draft.recipients || []).map((c) => ({ value: c.email, label: c.label }));
		const other = "__other__";
		options.push({ value: other, label: __("Another address...") });
		const dialog = new frappe.ui.Dialog({
			title: __("Send Return Request"),
			size: "large",
			fields: [
				{
					fieldname: "recipient_choice",
					fieldtype: "Select",
					label: __("To"),
					options,
					default: options[0].value,
					reqd: 1,
				},
				{
					fieldname: "recipient_other",
					fieldtype: "Data",
					options: "Email",
					label: __("Email address"),
					depends_on: `eval:doc.recipient_choice=='${other}'`,
					mandatory_depends_on: `eval:doc.recipient_choice=='${other}'`,
				},
				{ fieldname: "cc", fieldtype: "Data", label: __("Cc (optional, one address)"), options: "Email" },
				{ fieldname: "subject", fieldtype: "Data", label: __("Subject"), reqd: 1, default: draft.subject },
				{
					fieldname: "body",
					fieldtype: "Text Editor",
					label: __("Message"),
					reqd: 1,
					default: draft.body,
					description:
						(draft.template_found
							? __("Worded by the Email Template {0}. Change it here for this one email.", [draft.template])
							: __("The Email Template {0} is missing, so the built-in wording is shown.", [draft.template])) +
						" " +
						__("The line {0} becomes the table of items. Preview shows it.", [draft.lines_marker]),
				},
				{
					fieldname: "attach_photos",
					fieldtype: "Check",
					label: __("Attach the {0} photo(s) on this return", [draft.photos || 0]),
					default: draft.photos ? 1 : 0,
					hidden: draft.photos ? 0 : 1,
				},
				{
					fieldname: "note",
					fieldtype: "HTML",
					options: `<p class="text-muted small">${__(
						"The PDF of this return is attached. Sending marks the return Requested."
					)}</p>`,
				},
			],
			primary_action_label: __("Send"),
			primary_action(values) {
				const to = values.recipient_choice === other ? values.recipient_other : values.recipient_choice;
				dialog.disable_primary_action();
				sr_step(
					frm,
					"send_request",
					{
						recipient: to,
						subject: values.subject,
						body: values.body,
						cc: values.cc ? [values.cc] : [],
						attach_photos: values.attach_photos ? 1 : 0,
					},
					__("Sending...")
				)
					.then(() => {
						dialog.hide();
						frappe.show_alert({ message: __("Return request sent to {0}.", [to]), indicator: "green" });
					})
					.catch(() => dialog.enable_primary_action());
			},
			secondary_action_label: __("Preview"),
			secondary_action() {
				const values = dialog.get_values(true) || {};
				sr_call("request_preview", { name: frm.doc.name, subject: values.subject, body: values.body }).then((p) => {
					const preview = new frappe.ui.Dialog({ title: __("As the supplier will see it"), size: "large" });
					const frame = document.createElement("iframe");
					frame.setAttribute("sandbox", "");
					frame.style.cssText = "width:100%;height:70vh;border:0;";
					frame.srcdoc = (p.message && p.message.html) || "";
					preview.$body.empty().append(frame);
					preview.show();
				});
			},
		});
		dialog.show();
	});
}

function sr_sent_back_dialog(frm) {
	const dialog = new frappe.ui.Dialog({
		title: __("Mark Sent Back"),
		fields: [
			{
				fieldname: "return_method",
				fieldtype: "Select",
				label: __("How it went back"),
				options: SR_METHODS,
				reqd: 1,
				default: frm.doc.return_method || "",
			},
			{
				fieldname: "carrier",
				fieldtype: "Data",
				label: __("Carrier"),
				default: frm.doc.carrier,
				depends_on: "eval:doc.return_method=='Carrier'",
				mandatory_depends_on: "eval:doc.return_method=='Carrier'",
			},
			{
				fieldname: "tracking_number",
				fieldtype: "Data",
				label: __("Tracking number"),
				default: frm.doc.tracking_number,
				depends_on: "eval:doc.return_method=='Carrier'",
				mandatory_depends_on: "eval:doc.return_method=='Carrier'",
			},
			{
				fieldname: "sent_back_on",
				fieldtype: "Date",
				label: __("Date it went back"),
				reqd: 1,
				default: frappe.datetime.get_today(),
			},
		],
		primary_action_label: __("Mark Sent Back"),
		primary_action(values) {
			dialog.disable_primary_action();
			sr_step(frm, "mark_sent_back", values)
				.then((answer) => {
					dialog.hide();
					if (answer && answer.needs_purchase_return) {
						frappe.confirm(
							answer.takes_stock
								? __("The items have left. Create the Purchase Return now? It takes them out of stock once you submit it.")
								: __("The items have left. Create the Purchase Return now? Nothing is in stock for these lines, but it lowers the received quantity on the order."),
							() => sr_make_purchase_return(frm)
						);
					} else if (answer && answer.note) {
						frappe.msgprint({ title: __("No stock document"), message: frappe.utils.escape_html(answer.note), indicator: "blue" });
					}
				})
				.catch(() => dialog.enable_primary_action());
		},
	});
	dialog.show();
}

function sr_make_purchase_return(frm) {
	sr_call("make_purchase_return", { name: frm.doc.name }, __("Building the Purchase Return...")).then((r) => {
		const made = r.message || {};
		if (!made.name) return;
		frappe.show_alert({
			message: made.existing
				? __("Purchase Return {0} already exists. Check it and submit it.", [made.name])
				: __("Draft Purchase Return {0} created. Check the warehouse on each line, then submit it.", [made.name]),
			indicator: "blue",
		});
		sr_say_notice(made.notice);
		frappe.set_route("Form", "Purchase Receipt", made.name);
	});
}

// A toast, not a dialog: the router closes an open dialog on the route change that follows.
// The same words are in the draft's remarks and on its timeline.
function sr_say_notice(notice) {
	if (notice) frappe.show_alert({ message: frappe.utils.escape_html(notice), indicator: "orange" }, 20);
}

function sr_money_dialog(frm, state) {
	sr_prompt(
		frm,
		__("Record Money Received"),
		[
			{
				fieldname: "warning",
				fieldtype: "HTML",
				options: `<div class="alert alert-warning">${frappe.utils.escape_html(state.not_backed)} ${__(
					"Use this while bills are entered outside ERPNext. Once a Debit Note or Payment Entry is linked, the amount comes from it."
				)}</div>`,
			},
			{
				fieldname: "kind",
				fieldtype: "Select",
				label: __("What came back"),
				reqd: 1,
				options: [
					{ value: "refund", label: __("A refund (money back)") },
					{ value: "credit", label: __("A credit on our account") },
				],
				default: frm.doc.requested_resolution === "Credit on Account" ? "credit" : "refund",
			},
			{ fieldname: "amount", fieldtype: "Currency", label: __("Amount"), reqd: 1, default: frm.doc.expected_amount },
			{ fieldname: "received_on", fieldtype: "Date", label: __("Date"), reqd: 1, default: frappe.datetime.get_today() },
			{
				fieldname: "reference",
				fieldtype: "Data",
				label: __("Reference"),
				reqd: 1,
				description: __("The check number, the credit memo number, or where the refund shows."),
			},
			{
				fieldname: "more_expected",
				fieldtype: "Check",
				label: __("More is still expected"),
				description: __(
					"Tick it when this is one part of the refund. The return keeps waiting for the rest, and you record that when it arrives."
				),
			},
			{ fieldname: "notes", fieldtype: "Small Text", label: __("Notes") },
		],
		__("Record"),
		"record_money",
		(values) => Object.assign({}, values, { more_expected: values.more_expected ? 1 : 0 })
	);
}

function sr_replacement_dialog(frm) {
	sr_prompt(
		frm,
		__("Record Replacement Received"),
		[
			{
				fieldname: "replacement_receipt",
				fieldtype: "Link",
				options: "Purchase Receipt",
				label: __("Purchase Receipt the replacement came in on"),
				description: __(
					"Receive the replacement against the same purchase order line (Purchase Order > Create > Purchase Receipt), then pick that receipt here."
				),
				get_query: () => ({
					filters: {
						supplier: frm.doc.supplier,
						docstatus: 1,
						is_return: 0,
						name: ["!=", frm.doc.purchase_receipt],
					},
				}),
			},
			{
				fieldname: "received_on",
				fieldtype: "Date",
				label: __("Date (when there is no receipt)"),
				depends_on: "eval:!doc.replacement_receipt",
			},
			{ fieldname: "notes", fieldtype: "Small Text", label: __("Notes") },
		],
		__("Record"),
		"record_replacement"
	);
}

function sr_link_dialog(frm, title, doctype, method, argname, filters) {
	sr_prompt(
		frm,
		title,
		[{ fieldname: "docname", fieldtype: "Link", options: doctype, label: __(doctype), reqd: 1, get_query: () => ({ filters }) }],
		__("Link"),
		method,
		(values) => ({ [argname]: values.docname })
	);
}

function sr_debit_note(frm, state) {
	const bills = state.bills || [];
	if (!bills.length) return;
	const make = (bill) =>
		sr_call("make_debit_note", { name: frm.doc.name, purchase_invoice: bill }, __("Building the Debit Note...")).then((r) => {
			const made = r.message || {};
			if (!made.name) return;
			sr_say_notice(made.notice);
			frappe.set_route("Form", "Purchase Invoice", made.name);
		});
	if (bills.length === 1) {
		frappe.confirm(__("Create a Debit Note against bill {0} for the items on this return?", [bills[0].name]), () => make(bills[0].name));
		return;
	}
	const dialog = new frappe.ui.Dialog({
		title: __("Which bill?"),
		fields: [
			{
				fieldname: "bill",
				fieldtype: "Select",
				label: __("Bill"),
				reqd: 1,
				options: bills.map((b) => ({ value: b.name, label: `${b.name} · ${b.bill_no || ""} · ${b.posting_date}` })),
			},
		],
		primary_action_label: __("Create Debit Note"),
		primary_action(values) {
			dialog.hide();
			make(values.bill);
		},
	});
	dialog.show();
}

function sr_refund_payment(frm) {
	frappe.call({ method: SR_API + "refund_source", args: { name: frm.doc.name } }).then((r) => {
		const source = r.message;
		if (!source) return;
		// ERPNext's own builder: a Receive payment from the supplier against the invoice. It
		// opens unsaved; submitting it links itself to this return (it names the Debit Note).
		frappe
			.call({
				method: "erpnext.accounts.doctype.payment_entry.payment_entry.get_payment_entry",
				args: { dt: source.doctype, dn: source.name },
			})
			.then((p) => {
				if (!p.message) return;
				const doc = frappe.model.sync(p.message)[0];
				frappe.set_route("Form", doc.doctype, doc.name);
			});
	});
}

function sr_headline(frm, state) {
	const parts = [];
	const waiting = SR_WAITING[state.waiting_on];
	if (waiting) parts.push(`<b>${waiting}</b> ${sr_days(state.days_waiting)}`);
	else if (state.days_waiting !== null && state.days_waiting !== undefined) {
		parts.push(`${frappe.utils.escape_html(frm.doc.status)} ${sr_days(state.days_waiting)}`);
	}
	if (frm.doc.supplier_return_number) {
		parts.push(__("Return number {0}", [frappe.utils.escape_html(frm.doc.supplier_return_number)]));
	}
	if (frm.doc.tracking_number) {
		parts.push(
			`${frappe.utils.escape_html(frm.doc.carrier || "")} ${frappe.utils.escape_html(frm.doc.tracking_number)}`
		);
	}
	frm.dashboard.clear_headline();
	if (parts.length) frm.dashboard.set_headline(parts.join(" &middot; "));

	if (state.needs_purchase_return) {
		frm.set_intro(
			state.purchase_return_is_draft
				? __("Purchase Return {0} is still a draft. Stock does not change until it is submitted.", [frm.doc.purchase_return])
				: state.takes_stock
					? __("The items have gone back but they are still in stock in ERPNext. Create the Purchase Return.")
					: __("Create the Purchase Return so the order shows these items as returned."),
			"orange"
		);
	}
	// set_intro takes HTML, and these carry item codes and document names: escaped here.
	const esc = frappe.utils.escape_html;
	if (state.receipt_canceled) frm.set_intro(esc(state.receipt_gone), "red");
	if (state.record_only_note) frm.set_intro(esc(state.record_only_note), "blue");
	if (state.typed_by_hand) frm.set_intro(esc(state.not_backed), "yellow");
	if (state.money_note && state.money_backed && state.waiting_on === "money") {
		frm.set_intro(esc(state.money_note), "blue");
	}
}

function sr_buttons(frm, state) {
	const has = (action) => (state.actions || []).includes(action);
	const steps = __("Next Step");
	const documents = __("ERPNext Documents");

	if (has("request")) {
		frm.add_custom_button(__("Send Return Request"), () => sr_send_dialog(frm)).addClass("btn-primary");
		frm.add_custom_button(
			__("Requested Another Way"),
			() =>
				sr_prompt(
					frm,
					__("Requested Another Way"),
					[
						{ fieldname: "how", fieldtype: "Select", label: __("How the supplier was asked"), options: SR_WAYS, reqd: 1 },
						{ fieldname: "notes", fieldtype: "Small Text", label: __("Who you spoke to, and what they said") },
					],
					__("Mark Requested"),
					"mark_requested"
				),
			steps
		);
	}
	if (has("approve")) {
		frm.add_custom_button(
			__("Supplier Approved"),
			() =>
				sr_prompt(
					frm,
					__("Supplier Approved"),
					[
						{
							fieldname: "supplier_return_number",
							fieldtype: "Data",
							label: __("Supplier's return number"),
							default: frm.doc.supplier_return_number,
							description: __("The RMA number. Leave blank if they gave none."),
						},
						{ fieldname: "notes", fieldtype: "Small Text", label: __("What the supplier said") },
					],
					__("Record Approval"),
					"record_approval"
				),
			steps
		);
	}
	if (has("deny")) {
		frm.add_custom_button(
			__("Supplier Denied"),
			() =>
				sr_prompt(
					frm,
					__("Supplier Denied"),
					[{ fieldname: "notes", fieldtype: "Small Text", label: __("What the supplier said"), reqd: 1 }],
					__("Record Denial"),
					"record_denial"
				),
			steps
		);
	}
	if (has("send_back")) frm.add_custom_button(__("Mark Sent Back"), () => sr_sent_back_dialog(frm), steps);
	if (has("refund") || has("credit")) {
		frm.add_custom_button(__("Record Money Received"), () => sr_money_dialog(frm, state), steps);
	}
	if (has("replacement")) {
		frm.add_custom_button(__("Record Replacement Received"), () => sr_replacement_dialog(frm), steps);
	}
	if (has("settle")) {
		frm.add_custom_button(
			__("No More Is Coming"),
			() =>
				sr_prompt(
					frm,
					__("No More Is Coming"),
					[
						{
							fieldname: "info",
							fieldtype: "HTML",
							options: `<p>${__(
								"This finishes the return at what has come back so far. The report shows the difference from what was expected."
							)}</p>`,
						},
						{ fieldname: "reason", fieldtype: "Small Text", label: __("Why the rest is not coming") },
					],
					__("Finish"),
					"settle"
				),
			steps
		);
	}
	if (has("close")) {
		frm.add_custom_button(
			__("Close Without Credit"),
			() =>
				sr_prompt(
					frm,
					__("Close Without Credit"),
					[{ fieldname: "reason", fieldtype: "Small Text", label: __("Why nothing is coming back"), reqd: 1 }],
					__("Close"),
					"close_without_credit"
				),
			steps
		);
	}
	if (has("cancel")) {
		frm.add_custom_button(
			__("Cancel Return"),
			() =>
				sr_prompt(
					frm,
					__("Cancel Return"),
					[{ fieldname: "reason", fieldtype: "Small Text", label: __("Why"), reqd: 1 }],
					__("Cancel Return"),
					"cancel_return"
				),
			steps
		);
	}
	if (has("reopen")) {
		frm.add_custom_button(
			__("Reopen"),
			() =>
				sr_prompt(
					frm,
					__("Reopen"),
					[{ fieldname: "reason", fieldtype: "Small Text", label: __("Why it is reopened"), reqd: 1 }],
					__("Reopen"),
					"reopen"
				),
			steps
		);
	}

	const gone = ["Sent Back", "Refund Received", "Credit Received", "Replacement Received", "Closed Without Credit"].includes(
		frm.doc.status
	);
	if (gone && state.may_create) {
		if (state.needs_purchase_return) {
			frm.add_custom_button(
				state.purchase_return_is_draft ? __("Open Purchase Return") : __("Create Purchase Return"),
				() => sr_make_purchase_return(frm),
				documents
			);
		}
		if (!frm.doc.purchase_return) {
			frm.add_custom_button(
				__("Link Existing Purchase Return"),
				() =>
					sr_link_dialog(frm, __("Link Existing Purchase Return"), "Purchase Receipt", "link_purchase_return", "purchase_return", {
						is_return: 1,
						return_against: frm.doc.purchase_receipt,
						docstatus: ["!=", 2],
					}),
				documents
			);
		}
	}
	if (gone && state.may_money) {
		if (!frm.doc.debit_note && (state.bills || []).length) {
			frm.add_custom_button(__("Create Debit Note"), () => sr_debit_note(frm, state), documents);
		}
		if (!frm.doc.debit_note) {
			frm.add_custom_button(
				__("Link Existing Debit Note"),
				() =>
					sr_link_dialog(frm, __("Link Existing Debit Note"), "Purchase Invoice", "link_debit_note", "debit_note", {
						is_return: 1,
						supplier: frm.doc.supplier,
						docstatus: ["!=", 2],
					}),
				documents
			);
		}
		if (frm.doc.debit_note && flt(state.owed) > 0) {
			frm.add_custom_button(__("Create Refund Payment"), () => sr_refund_payment(frm), documents);
		}
		// A return can hold several payments (a refund that arrives in parts), so this stays.
		frm.add_custom_button(
			__("Link a Refund Payment"),
			() =>
				sr_prompt(
					frm,
					__("Link a Refund Payment"),
					[
						{
							fieldname: "payment_entry",
							fieldtype: "Link",
							options: "Payment Entry",
							label: __("Payment Entry"),
							reqd: 1,
							get_query: () => ({
								filters: {
									payment_type: "Receive",
									party_type: "Supplier",
									party: frm.doc.supplier,
									docstatus: 1,
								},
							}),
						},
						{
							fieldname: "amount",
							fieldtype: "Currency",
							label: __("How much of it is for this return"),
							description: __(
								"Leave blank to take whatever other returns have not already counted. A payment is never counted twice."
							),
						},
					],
					__("Link"),
					"link_payment_entry"
				),
			documents
		);
		if (frm.doc.debit_note || (frm.doc.payments || []).length || frm.doc.purchase_return) {
			frm.add_custom_button(__("Check the Documents"), () => sr_step(frm, "refresh", {}), documents);
		}
	}
}

frappe.ui.form.on("Supplier Return", {
	setup(frm) {
		frm.set_query("purchase_receipt", () => {
			const filters = { docstatus: 1, is_return: 0 };
			if (frm.doc.supplier) filters.supplier = frm.doc.supplier;
			return { filters };
		});
	},

	refresh(frm) {
		const draft = frm.doc.status === "Draft" || frm.is_new();
		// What is going back is fixed once the supplier has been asked about it.
		frm.set_df_property("items", "cannot_add_rows", true);
		frm.set_df_property("items", "cannot_delete_rows", !draft);
		for (const field of ["supplier", "purchase_receipt", "posting_date"]) frm.set_df_property(field, "read_only", !draft);
		// A finished return's fees, shipping and request are part of its outcome
		// (`supplier_return_rules.LOCKED_WHEN_FINAL`; the server refuses the save too).
		const finished = SR_FINAL.includes(frm.doc.status);
		for (const field of SR_LOCKED_WHEN_FINAL) frm.set_df_property(field, "read_only", finished);
		frm.fields_dict.items.grid.update_docfield_property("qty", "read_only", !draft);

		if (draft) {
			frm.add_custom_button(__("Get Items From Receipt"), () => sr_fill_from_receipt(frm));
		}
		if (frm.is_new()) {
			if (frm.doc.purchase_receipt && !(frm.doc.items || []).length) sr_fill_from_receipt(frm);
			return;
		}
		frappe.call({ method: SR_API + "state", args: { name: frm.doc.name } }).then((r) => {
			const state = r.message;
			if (!state || frm.doc.name !== cur_frm.doc.name) return;
			sr_headline(frm, state);
			sr_buttons(frm, state);
		});
	},

	purchase_receipt(frm) {
		if (frm.doc.purchase_receipt && (frm.is_new() || frm.doc.status === "Draft") && !(frm.doc.items || []).length) {
			sr_fill_from_receipt(frm);
		}
	},
});

frappe.ui.form.on("Supplier Return Item", {
	qty(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		frappe.model.set_value(cdt, cdn, "amount", flt(row.qty) * flt(row.rate));
		const most = flt(row.received_qty) - flt(row.already_returned_qty);
		if (row.received_qty && flt(row.qty) > most + 1e-9) {
			frappe.show_alert({
				message: __("Only {0} of {1} can still go back on this receipt line.", [most, row.item_code]),
				indicator: "orange",
			});
		}
	},
});
