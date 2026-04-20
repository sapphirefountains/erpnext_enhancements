frappe.ui.form.on("Sapphire Maintenance Record", {
	setup: function (frm) {
		// Filter items in consumables to only Stock Items and Consumables group
		frm.set_query("item", "consumables", function () {
			return {
				filters: [
					["is_stock_item", "=", 1],
					["item_group", "=", "Consumables"],
				],
			};
		});
	},

	project: function (frm) {
		if (frm.doc.project) {
			// 1. Dynamic Checklist Population
			frappe.call({
				method: "erpnext_enhancements.enhancements_core.doctype.sapphire_maintenance_record.sapphire_maintenance_record.get_template_items",
				args: {
					project: frm.doc.project,
				},
				callback: function (r) {
					if (r.message) {
						frm.clear_table("maintenance_results");
						r.message.forEach((item) => {
							let row = frm.add_child("maintenance_results");
							row.question = item.question_prompt;
							// If the template item has specific options, we might want to handle it here
						});
						frm.refresh_field("maintenance_results");
					}
				},
			});

			// 2. Contextual Dashboard via Tailwind CSS
			frappe.db.get_value("Sapphire Maintenance Profile", {"project": frm.doc.project}, ["safety_instructions", "access_codes"], (r) => {
				if (r) {
					const safety = r.safety_instructions || "No specific instructions provided.";
					const codes = r.access_codes || "N/A";

					const dashboard_html = `
						<div class="p-4 mb-4 border-l-4 bg-red-50 border-red-400">
							<div class="flex">
								<div class="flex-shrink-0">
									<svg class="w-5 h-5 text-red-400" viewBox="0 0 20 20" fill="currentColor">
										<path fill-rule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clip-rule="evenodd" />
									</svg>
								</div>
								<div class="ml-3">
									<h3 class="text-sm font-medium text-red-800">Critical Safety Instructions</h3>
									<div class="mt-2 text-sm text-red-700">
										<p>${safety}</p>
									</div>
								</div>
							</div>
						</div>
						<div class="p-4 bg-blue-50 border-l-4 border-blue-400">
							<div class="flex">
								<div class="flex-shrink-0">
									<svg class="w-5 h-5 text-blue-400" viewBox="0 0 20 20" fill="currentColor">
										<path d="M10 2a5 5 0 00-5 5v2a2 2 0 00-2 2v5a2 2 0 002 2h10a2 2 0 002-2v-5a2 2 0 00-2-2V7a5 5 0 00-5-5zM7 7a3 3 0 016 0v2H7V7z" />
									</svg>
								</div>
								<div class="ml-3">
									<h3 class="text-sm font-medium text-blue-800">Site Access Info</h3>
									<div class="mt-2 text-sm text-blue-700">
										<p>Gate/Access Code: <span class="font-bold">${codes}</span></p>
									</div>
								</div>
							</div>
						</div>
					`;
					frm.get_field("dashboard").$wrapper.html(dashboard_html);
				}
			});
		}
	},
});
