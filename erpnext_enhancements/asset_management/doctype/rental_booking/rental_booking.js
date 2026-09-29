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
