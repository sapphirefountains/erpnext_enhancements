// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// Rental Settings: the calendar feed links (v1.567.0). The link carries the key, so it is shown
// only here, to the roles that manage it, and Rotate replaces it for everyone.
const FEED = "erpnext_enhancements.asset_management.rental_calendar.feed_links";

function show_feed_links(frm, rotate) {
	frappe.call({ method: FEED, args: { rotate: rotate ? 1 : 0 }, freeze: true }).then(({ message }) => {
		if (!message) return;
		const esc = frappe.utils.escape_html;
		const rows = message.fountains
			.map((f) => `<tr><td>${esc(f.asset_name || f.asset)}</td><td><input class="form-control input-xs" readonly value="${esc(f.url)}" onclick="this.select()"></td></tr>`)
			.join("");
		const dialog = new frappe.ui.Dialog({
			title: __("Calendar Feeds"),
			size: "large",
			fields: [{ fieldtype: "HTML", fieldname: "links" }],
			primary_action_label: __("Rotate Key"),
			primary_action() {
				frappe.confirm(__("Make a new link? Every calendar subscribed with the old one stops updating."), () => {
					dialog.hide();
					show_feed_links(frm, true);
				});
			},
		});
		dialog.fields_dict.links.$wrapper.html(`
			<p>${__("In Google Calendar: Other calendars, then From URL, and paste a link. Anyone with a link can see the bookings and customer names, so share it only with staff.")}</p>
			<label>${__("Whole fleet")}</label>
			<input class="form-control mb-3" readonly value="${esc(message.fleet)}" onclick="this.select()">
			<table class="table table-sm"><tbody>${rows}</tbody></table>`);
		dialog.show();
	});
}

frappe.ui.form.on("Rental Settings", {
	refresh(frm) {
		if (frappe.user.has_role(["System Manager", "Operations Team"])) {
			frm.add_custom_button(__("Calendar Feeds"), () => show_feed_links(frm, false));
		}
	},
});
