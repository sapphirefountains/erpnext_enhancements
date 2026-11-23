// Estimating milestone 1: start an Estimate from the deal it prices.
//
// Create > Estimate on an Opportunity opens a new Estimate with the deal, its account and its title
// filled in; pick a template there and the checklist opens. Only for people who may create an
// Estimate (Estimator, Executive Team, Finance Team, System Manager), so nobody else sees a button
// that would refuse them. Estimates > View lists the deal's estimates once there are any.

frappe.ui.form.on("Opportunity", {
	refresh(frm) {
		if (frm.is_new() || !frappe.model.can_create("Estimate")) return;
		frm.add_custom_button(
			__("Estimate"),
			() => {
				const customer = frm.doc.opportunity_from === "Customer" ? frm.doc.party_name : "";
				frappe.new_doc("Estimate", {
					opportunity: frm.doc.name,
					customer,
					title: frm.doc.title || frm.doc.customer_name || frm.doc.name,
					company: frm.doc.company,
				});
			},
			__("Create")
		);
		if (!frappe.model.can_read("Estimate")) return;
		frappe.db.count("Estimate", { filters: { opportunity: frm.doc.name } }).then((n) => {
			if (!n) return;
			frm.add_custom_button(
				__("Estimates ({0})", [n]),
				() => frappe.set_route("List", "Estimate", { opportunity: frm.doc.name }),
				__("View")
			);
		});
	},
});
