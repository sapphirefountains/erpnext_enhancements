// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt
//
// "Is anybody in a hole right now?"
//
// The list view answers it on open, because that is the only question this list
// gets asked urgently and scrolling for it is how you find out ten minutes later.
// An OPEN permit older than its expiry is the one that matters: a permit nobody
// closed reads exactly like a person still down there, and the two are
// indistinguishable from the outside — which is precisely why somebody should go
// and look.

frappe.listview_settings["Confined Space Entry Permit"] = {
	add_fields: ["status", "opened_on", "expires_on", "entrant_name"],

	get_indicator(doc) {
		if (doc.status === "Open") {
			const expired = doc.expires_on && frappe.datetime.now_datetime() > doc.expires_on;
			return [__(expired ? "Open — expired" : "Open"), expired ? "red" : "orange", "status,=,Open"];
		}
		const tone = { Closed: "green", Canceled: "gray", Draft: "blue" };
		return [__(doc.status), tone[doc.status] || "gray", `status,=,${doc.status}`];
	},

	onload(listview) {
		frappe
			.call({ method: "erpnext_enhancements.hr_enhancements.permits.open_permits" })
			.then((r) => {
				const rows = (r && r.message) || [];
				if (!rows.length) return;
				const expired = rows.filter((x) => x.expired);
				const who = rows.map((x) => frappe.utils.escape_html(x.entrant_name || "")).join(", ");
				listview.page.set_indicator(
					__("{0} permit(s) open", [rows.length]),
					expired.length ? "red" : "orange"
				);
				frappe.show_alert(
					{
						message: expired.length
							? __("{0} open permit(s) are past their expiry — {1}. Go and look.", [
									expired.length,
									who,
								])
							: __("{0} permit(s) open right now: {1}.", [rows.length, who]),
						indicator: expired.length ? "red" : "orange",
					},
					10
				);
			})
			.catch(() => {
				// Staff-only on the server. A customer contact reaching this list would
				// get a modal for a banner, which is worse than no banner.
			});
	},
};
