// Travel Settings form script.
//
// HRMS (Frappe HR) is an optional dependency of the Travel Management module.
// When it is not installed the Expense Claim Type doctype is absent, so the
// Expense Claim Types mapping here is meaningless — and a stored value would
// 404 this form on load (Frappe resolves link titles via getdoc). The server
// controller clears those fields on save and flags availability via __onload;
// here we hide the whole section and explain why, so the surviving settings
// (per-diem and mileage rates, automation toggles) stay usable.

frappe.ui.form.on('Travel Settings', {
	refresh(frm) {
		const available = !!(frm.doc.__onload && frm.doc.__onload.expense_claims_available);

		frm.toggle_display('expense_types_section', available);

		// Clear once up front so neither notice duplicates across refreshes.
		frm.dashboard.clear_comment();

		// The agenda map uses a Google Maps *browser* key — it is sent to every
		// permitted Trip viewer by design, so its only real protection is an
		// HTTP-referrer restriction in the Google Cloud console. Remind the
		// operator the moment a key is present.
		if (frm.doc.google_maps_api_key) {
			frm.dashboard.add_comment(
				__(
					'Restrict the Google Maps API key by HTTP referrer (to your ERPNext domain) in the Google Cloud console — a browser key is exposed to anyone who can open a Travel Trip.'
				),
				'blue',
				true
			);
		}

		if (!available) {
			frm.dashboard.add_comment(
				__(
					'The HR module (Frappe HR) is not installed, so Expense Claim Type mapping and travel expense-claim generation are unavailable. The per-diem and mileage rates below still apply. Install <code>hrms</code> to enable travel finance.'
				),
				'yellow',
				true
			);
		}
	},
});
