// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

/**
 * Desk form for Rental Booking.
 *
 * - Status moves through the buttons at the top. Which buttons show comes from the server
 *   (`__onload.next_statuses`, from rental_rules.TRANSITIONS), so there is one list of legal
 *   moves and it lives in Python.
 * - "Check Availability" lists every rentable fountain and accessory pool for the booking's
 *   dates; free fountains can be ticked and added in one go.
 * - "Apply Package" fills free fountains of the package's models, its accessories and fees.
 * - Picking a Project fills a blank customer and schedule from the Project's Events fields.
 *
 * The server is the authority on availability: every save re-checks with the fountains and
 * pools row-locked. These buttons are for finding what is free, not for enforcing it.
 */

const AVAILABILITY = "erpnext_enhancements.asset_management.rental_availability";

const STATUS_ACTIONS = {
	Confirmed: { label: __("Confirm"), primary: true },
	Out: { label: __("Mark Out"), primary: true },
	Returned: { label: __("Mark Returned"), primary: true },
	Closed: { label: __("Close"), primary: true },
	Tentative: { label: __("Renew Hold"), primary: true },
	Expired: { label: __("Release Hold") },
	Canceled: { label: __("Cancel Booking"), confirm: __("Cancel this booking and free its fountains?") },
};

const PROJECT_SCHEDULE = {
	delivery_datetime: "custom_delivery_date_time",
	setup_datetime: "custom_setup_date_time",
	event_start_datetime: "custom_event_date_time",
	takedown_datetime: "custom_take_down_date_time",
};

const FROZEN = ["Closed", "Expired", "Canceled"];

frappe.ui.form.on("Rental Booking", {
	setup(frm) {
		frm.set_query("asset", "fountains", () => ({ query: `${AVAILABILITY}.rentable_asset_query` }));
		frm.set_query("pool", "accessories", () => ({ filters: { disabled: 0 } }));
		frm.set_query("rental_package", () => ({ filters: { disabled: 0 } }));
		frm.set_query("contact_person", () => ({
			query: "frappe.contacts.doctype.contact.contact.contact_query",
			filters: { link_doctype: "Customer", link_name: frm.doc.customer },
		}));
		frm.set_query("project", () => (frm.doc.customer ? { filters: { customer: frm.doc.customer } } : {}));
	},

	refresh(frm) {
		const frozen = FROZEN.includes(frm.doc.status);
		["delivery_datetime", "setup_datetime", "event_start_datetime", "event_end_datetime",
			"takedown_datetime", "fountains", "accessories", "rental_package"].forEach((f) =>
			frm.set_df_property(f, "read_only", frozen ? 1 : 0)
		);

		if (frm.is_new()) {
			// Opened from a Project's Create menu: route options set `project` without firing
			// its change handler, so pull the schedule here.
			if (frm.doc.project && !frm.doc.delivery_datetime) {
				frm.trigger("project");
			}
			add_availability_button(frm);
			return;
		}

		const next = (frm.doc.__onload && frm.doc.__onload.next_statuses) || [];
		next.forEach((status) => add_status_button(frm, status));

		if (!frozen) {
			add_availability_button(frm);
		}
		add_sales_buttons(frm);
		add_deposit_buttons(frm);
		frm.add_custom_button(__("Rental Planner"), () => frappe.set_route("rental-planner"), __("View"));
		frm.add_custom_button(__("Fountain Calendar"), () => {
			frappe.route_options = { rental_booking: frm.doc.name };
			frappe.set_route("List", "Asset Booking", "Calendar");
		}, __("View"));

		if (frm.doc.status === "Tentative" && frm.doc.hold_expires_on) {
			frm.dashboard.set_headline_alert(
				__("Held until {0}. Confirm the booking to keep the dates.", [
					frappe.datetime.str_to_user(frm.doc.hold_expires_on),
				]),
				"orange"
			);
		}
	},

	project(frm) {
		if (!frm.doc.project) {
			return;
		}
		frappe.db
			.get_value("Project", frm.doc.project, ["customer", ...Object.values(PROJECT_SCHEDULE)])
			.then(({ message }) => {
				if (!message) {
					return;
				}
				if (!frm.doc.customer && message.customer) {
					frm.set_value("customer", message.customer);
				}
				Object.entries(PROJECT_SCHEDULE).forEach(([field, source]) => {
					if (!frm.doc[field] && message[source]) {
						frm.set_value(field, message[source]);
					}
				});
			});
	},

	rental_package(frm) {
		if (frm.doc.rental_package) {
			apply_package(frm);
		}
	},
});

function add_status_button(frm, status) {
	const action = STATUS_ACTIONS[status];
	if (!action) {
		return;
	}
	const run = () =>
		frm.call("set_status", { status }).then(() => frm.reload_doc());
	const handler = () => {
		if (frm.is_dirty()) {
			frappe.msgprint(__("Save your changes first."));
			return;
		}
		if (action.confirm) {
			frappe.confirm(action.confirm, run);
		} else {
			run();
		}
	};
	if (action.primary) {
		// The next step in the lifecycle stands alone and is highlighted; the rest sit
		// under Status. Not page.set_primary_action: the form re-takes that slot for Save
		// whenever the document turns dirty.
		frm.add_custom_button(action.label, handler).addClass("btn-primary");
	} else {
		frm.add_custom_button(action.label, handler, __("Status"));
	}
}

function schedule_or_warn(frm) {
	if (!frm.doc.delivery_datetime || !frm.doc.takedown_datetime) {
		frappe.msgprint(__("Set the delivery and take-down times first."));
		return false;
	}
	return true;
}

function add_availability_button(frm) {
	frm.add_custom_button(__("Check Availability"), () => {
		if (!schedule_or_warn(frm)) {
			return;
		}
		frappe
			.call({
				method: `${AVAILABILITY}.get_availability`,
				args: {
					delivery_datetime: frm.doc.delivery_datetime,
					takedown_datetime: frm.doc.takedown_datetime,
					exclude_booking: frm.is_new() ? null : frm.doc.name,
				},
				freeze: true,
				freeze_message: __("Checking the fleet..."),
			})
			.then(({ message }) => show_availability(frm, message));
	});
	if (frm.doc.rental_package) {
		frm.add_custom_button(__("Apply Package"), () => apply_package(frm));
	}
}

function show_availability(frm, data) {
	const esc = frappe.utils.escape_html;
	const on_booking = new Set((frm.doc.fountains || []).map((r) => r.asset));
	const fountain_rows = (data.fountains || [])
		.map((f) => {
			const here = on_booking.has(f.asset);
			const why = f.conflicts.map((c) => esc(c.label)).join("<br>");
			const pick =
				f.available && !here
					? `<input type="checkbox" class="rental-pick" data-asset="${esc(f.asset)}">`
					: "";
			const state = here
				? `<span class="indicator-pill blue">${__("On this booking")}</span>`
				: f.available
					? `<span class="indicator-pill green">${__("Free")}</span>`
					: `<span class="indicator-pill red">${__("Taken")}</span>`;
			return `<tr><td>${pick}</td><td>${esc(f.asset_name || f.asset)}<div class="text-muted small">${esc(
				f.item_code || ""
			)}</div></td><td>${state}</td><td class="small">${why}</td></tr>`;
		})
		.join("");
	const pool_rows = (data.pools || [])
		.map(
			(p) =>
				`<tr><td>${esc(p.label)}</td><td>${p.available}</td><td class="text-muted">${__(
					"{0} owned, {1} booked",
					[p.capacity, p.booked]
				)}</td></tr>`
		)
		.join("");

	const dialog = new frappe.ui.Dialog({
		title: __("Availability"),
		size: "large",
		fields: [{ fieldtype: "HTML", fieldname: "table" }],
		primary_action_label: __("Add Selected Fountains"),
		primary_action() {
			dialog.$wrapper.find(".rental-pick:checked").each(function () {
				const row = frm.add_child("fountains");
				row.asset = $(this).data("asset");
			});
			frm.refresh_field("fountains");
			dialog.hide();
			frappe.show_alert({ message: __("Added. Save to hold them."), indicator: "blue" });
		},
	});
	dialog.fields_dict.table.$wrapper.html(`
		<p class="text-muted">${__("Each fountain is blocked from delivery to take-down, plus its own prep and turnaround time.")}</p>
		<table class="table table-bordered table-sm">
			<thead><tr><th></th><th>${__("Fountain")}</th><th>${__("Status")}</th><th>${__("In the way")}</th></tr></thead>
			<tbody>${fountain_rows || `<tr><td colspan="4">${__("No fountains are marked Available for Event Rental.")}</td></tr>`}</tbody>
		</table>
		<h5>${__("Accessories")}</h5>
		<table class="table table-bordered table-sm">
			<thead><tr><th>${__("Accessory")}</th><th>${__("Free")}</th><th></th></tr></thead>
			<tbody>${pool_rows || `<tr><td colspan="3">${__("No accessory pools yet.")}</td></tr>`}</tbody>
		</table>`);
	dialog.show();
}

function apply_package(frm) {
	if (!schedule_or_warn(frm)) {
		return;
	}
	frappe
		.call({
			method: `${AVAILABILITY}.plan_package`,
			args: {
				package: frm.doc.rental_package,
				delivery_datetime: frm.doc.delivery_datetime,
				takedown_datetime: frm.doc.takedown_datetime,
				current_assets: (frm.doc.fountains || []).map((r) => r.asset),
				exclude_booking: frm.is_new() ? null : frm.doc.name,
			},
			freeze: true,
		})
		.then(({ message }) => {
			if (!message) {
				return;
			}
			message.fountains.forEach((f) => {
				const row = frm.add_child("fountains");
				row.asset = f.asset;
				row.asset_name = f.asset_name;
				row.rate = f.rate;
			});
			const by_pool = {};
			(frm.doc.accessories || []).forEach((r) => (by_pool[r.pool] = r));
			message.accessories.forEach((a) => {
				const row = by_pool[a.pool] || frm.add_child("accessories");
				row.pool = a.pool;
				row.qty = a.qty;
				row.rate = a.rate;
			});
			Object.entries(message.fees).forEach(([field, value]) => {
				if (value && !frm.doc[field]) {
					frm.doc[field] = value;
				}
			});
			frm.refresh_fields();
			frm.dirty();
			if (message.shortfalls.length) {
				frappe.msgprint({
					title: __("Package partly applied"),
					message: message.shortfalls.map(frappe.utils.escape_html).join("<br>"),
					indicator: "orange",
				});
			} else {
				frappe.show_alert({ message: __("Package applied. Save to hold it."), indicator: "green" });
			}
		});
}

const SALES = "erpnext_enhancements.asset_management.rental_sales";

// The agreement and the two invoices (v1.564.0). Invoices are drafts until someone presses
// Submit & Send, which posts the invoice and emails the customer its pay link (Nik, 2026-09-29).
function add_sales_buttons(frm) {
	const live = ["Tentative", "Confirmed", "Out", "Returned"].includes(frm.doc.status);
	if (frm.doc.rental_agreement) {
		frm.add_custom_button(__("Rental Agreement"), () =>
			frappe.set_route("Form", "Project Contract", frm.doc.rental_agreement), __("View"));
	} else if (["Tentative", "Confirmed"].includes(frm.doc.status)) {
		frm.add_custom_button(__("Rental Agreement"), () => {
			if (frm.is_dirty()) {
				frappe.msgprint(__("Save your changes first."));
				return;
			}
			frappe
				.call({ method: `${SALES}.make_rental_agreement`, args: { booking: frm.doc.name }, freeze: true })
				.then(({ message }) => message && frappe.set_route("Form", "Project Contract", message));
		}, __("Create"));
	}
	if (live) {
		// v1.565.0: a portal account for one of the customer's contacts, and an email saying
		// where their rental lives. The signer gets one automatically when the agreement is signed.
		frm.add_custom_button(__("Invite to Portal"), () => {
			const dialog = new frappe.ui.Dialog({
				title: __("Invite to the customer portal"),
				fields: [{
					fieldname: "contact",
					fieldtype: "Link",
					options: "Contact",
					label: __("Contact"),
					reqd: 1,
					default: frm.doc.contact_person,
					get_query: () => ({
						query: "frappe.contacts.doctype.contact.contact.contact_query",
						filters: { link_doctype: "Customer", link_name: frm.doc.customer },
					}),
					description: __("They sign in with an emailed link. Staff accounts cannot be invited."),
				}],
				primary_action_label: __("Send Invitation"),
				primary_action({ contact }) {
					frappe
						.call({
							method: "erpnext_enhancements.asset_management.rental_portal.invite_to_portal",
							args: { booking: frm.doc.name, contact },
							freeze: true,
						})
						.then(({ message }) => {
							dialog.hide();
							if (message) frappe.show_alert({ message: __("Invitation sent to {0}", [message]), indicator: "green" });
						});
				},
			});
			dialog.show();
		}, __("Create"));
	}
	if (!live || frm.doc.status === "Tentative") {
		return;
	}
	[
		["Deposit", "deposit_invoice", __("Deposit Invoice")],
		["Balance", "balance_invoice", __("Balance Invoice")],
	].forEach(([kind, field, label]) => {
		const name = frm.doc[field];
		if (!name) {
			frm.add_custom_button(__("Draft {0}", [label]), () =>
				frappe
					.call({ method: `${SALES}.draft_rental_invoice`, args: { booking: frm.doc.name, kind }, freeze: true })
					.then(() => frm.reload_doc()), __("Invoices"));
			return;
		}
		frm.add_custom_button(label, () => frappe.set_route("Form", "Sales Invoice", name), __("Invoices"));
		frappe.db.get_value("Sales Invoice", name, "docstatus").then(({ message }) => {
			if (!message || message.docstatus !== 0) return;
			frm.add_custom_button(__("Submit & Send {0}", [label]), () =>
				frappe.confirm(
					__("Post {0} to the books and email the customer its pay link?", [name]),
					() =>
						frappe
							.call({ method: `${SALES}.submit_and_send`, args: { sales_invoice: name }, freeze: true })
							.then(({ message: r }) => {
								if (!r) return;
								frappe.msgprint(
									r.emailed
										? __("{0} posted and the pay link emailed to {1}.", [r.submitted, r.emailed])
										: r.autopay
											? __("{0} posted; the saved card will be charged.", [r.submitted])
											: __("{0} posted. {1}", [r.submitted, r.reason || ""])
								);
								frm.reload_doc();
							})
				), __("Invoices"));
		});
	});
}

const DEPOSIT = "erpnext_enhancements.asset_management.rental_deposit";

// Releasing the security deposit (v1.566.0): drafts first (a credit note, and a damage-charge
// invoice for any deduction), each posted by a person; then one click refunds the rest on Stripe.
function add_deposit_buttons(frm) {
	if (!frm.doc.security_deposit || !["Out", "Returned", "Closed"].includes(frm.doc.status)) {
		return;
	}
	const status = frm.doc.deposit_status || "";
	if (!status || status === "Held" || status === "Release Drafted") {
		frm.add_custom_button(__("Release Deposit…"), () => {
			const dialog = new frappe.ui.Dialog({
				title: __("Release the security deposit"),
				fields: [
					{
						fieldname: "deduction",
						fieldtype: "Currency",
						label: __("Deduction for damage"),
						default: frm.doc.deposit_deduction || 0,
						description: __("Deposit: {0}. Leave 0 to return all of it.", [
							format_currency(frm.doc.security_deposit),
						]),
					},
					{
						fieldname: "reason",
						fieldtype: "Small Text",
						label: __("What the deduction is for"),
						default: frm.doc.deposit_deduction_reason,
						depends_on: "eval:doc.deduction > 0",
						description: __("The customer sees this on the damage-charge invoice."),
					},
				],
				primary_action_label: __("Draft"),
				primary_action(values) {
					frappe
						.call({
							method: `${DEPOSIT}.prepare_release`,
							args: { booking: frm.doc.name, deduction: values.deduction || 0, reason: values.reason },
							freeze: true,
						})
						.then(() => {
							dialog.hide();
							frappe.show_alert({ message: __("Drafted. Submit the credit note (and any damage charge), then refund."), indicator: "blue" });
							frm.reload_doc();
						});
				},
			});
			dialog.show();
		}, __("Deposit"));
	}
	if (status === "Release Drafted" && frappe.user.has_role(["System Manager", "Accounts Manager"])) {
		frm.add_custom_button(__("Refund Deposit"), () =>
			frappe.confirm(__("Refund the deposit, less any deduction, to the customer's card?"), () =>
				frappe
					.call({ method: `${DEPOSIT}.refund_deposit`, args: { booking: frm.doc.name }, freeze: true })
					.then(({ message }) => {
						if (message) frappe.msgprint(__("Refunded {0}.", [format_currency(message.refunded)]));
						frm.reload_doc();
					})
			), __("Deposit"));
		frm.add_custom_button(__("Mark Refunded by Hand"), () =>
			frappe.prompt(
				{ fieldname: "reference", fieldtype: "Data", label: __("Check number or reference") },
				({ reference }) =>
					frappe
						.call({ method: `${DEPOSIT}.mark_refunded`, args: { booking: frm.doc.name, reference }, freeze: true })
						.then(() => frm.reload_doc()),
				__("Refunded outside Stripe")
			), __("Deposit"));
	}
}
