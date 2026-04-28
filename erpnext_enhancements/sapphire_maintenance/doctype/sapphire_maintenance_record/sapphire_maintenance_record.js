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

		frm.set_query("asset", function () {
			return {
				filters: {
					asset_category: "SF Water Feature",
				},
			};
		});
	},

	refresh: function (frm) {
		frm.trigger("toggle_safety_gate");
		if (frm.doc.project && frm.doc.asset) {
			frm.trigger("render_dashboard");
		}
	},

	safety_acknowledged: function (frm) {
		frm.trigger("toggle_safety_gate");
	},

	toggle_safety_gate: function (frm) {
		const is_acknowledged = frm.doc.safety_acknowledged;
		frm.set_df_property("maintenance_results", "hidden", !is_acknowledged);
		frm.set_df_property("consumables", "hidden", !is_acknowledged);

		if (!is_acknowledged) {
			const overlay_html = `
				<div class="p-4 mb-4 text-orange-700 bg-orange-100 border-l-4 border-orange-500" role="alert">
					<p class="font-bold">Safety Compliance Required</p>
					<p>Please review safety instructions and check the <strong>Safety Procedures & PPE Acknowledged</strong> box to begin the checklist.</p>
				</div>
			`;
			frm.get_field("dashboard").$wrapper.prepend(overlay_html);
		} else {
			frm.get_field("dashboard").$wrapper.find(".bg-orange-100").remove();
		}
	},

	project: function (frm) {
		frm.trigger("populate_checklist");
		frm.trigger("render_dashboard");
	},

	asset: function (frm) {
		frm.trigger("populate_checklist");
		frm.trigger("render_dashboard");
	},

	populate_checklist: function (frm) {
		if (frm.doc.project) {
			frappe.call({
				method: "erpnext_enhancements.sapphire_maintenance.doctype.sapphire_maintenance_record.sapphire_maintenance_record.get_template_items",
				args: {
					project: frm.doc.project,
				},
				callback: function (r) {
					if (r.message && r.message.length > 0) {
						frm.clear_table("maintenance_results");
						r.message.forEach((item) => {
							let row = frm.add_child("maintenance_results");
							row.question = item.question_prompt;
						});
						frm.refresh_field("maintenance_results");
					}
				},
			});
		}
	},

	render_dashboard: function (frm) {
		if (!frm.doc.project || !frm.doc.asset) return;

		frappe.call({
			method: "erpnext_enhancements.sapphire_maintenance.doctype.sapphire_maintenance_record.sapphire_maintenance_record.get_dashboard_context",
			args: {
				project: frm.doc.project,
				asset: frm.doc.asset,
			},
			callback: function (r) {
				if (r.message) {
					const ctx = r.message;
					const safety = ctx.profile.safety_instructions || "No specific safety instructions provided.";
					const codes = ctx.profile.access_codes || "N/A";
					const site_instr = ctx.asset.custom_site_instructions || "No specific site instructions.";

					let visits_html = "";
					if (ctx.visits && ctx.visits.length > 0) {
						visits_html = ctx.visits
							.map(
								(v) => `
							<div class="flex justify-between py-1 border-b border-gray-100 last:border-0">
								<span class="text-xs font-medium text-gray-600">${frappe.datetime.global_date_format(v.creation)}</span>
								<span class="text-xs text-gray-500">${v.technician}</span>
							</div>
						`
							)
							.join("");
					} else {
						visits_html = '<p class="text-xs text-gray-400">No recent visits found.</p>';
					}

					const dashboard_html = `
						<div class="space-y-4">
							<!-- Critical Safety -->
							<div class="p-4 border-l-4 bg-red-50 border-red-400 rounded-r-md">
								<div class="flex">
									<div class="flex-shrink-0">
										<svg class="w-5 h-5 text-red-400" viewBox="0 0 20 20" fill="currentColor">
											<path fill-rule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clip-rule="evenodd" />
										</svg>
									</div>
									<div class="ml-3">
										<h3 class="text-sm font-bold text-red-800">Safety Instructions</h3>
										<p class="mt-1 text-sm text-red-700">${safety}</p>
									</div>
								</div>
							</div>

							<!-- Site & Asset Context -->
							<div class="grid grid-cols-1 gap-4 md:grid-cols-2">
								<div class="p-4 bg-blue-50 border-l-4 border-blue-400 rounded-r-md">
									<h3 class="flex items-center text-sm font-bold text-blue-800">
										<svg class="w-4 h-4 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z"></path><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 11a3 3 0 11-6 0 3 3 0 016 0z"></path></svg>
										Access & Site
									</h3>
									<p class="mt-2 text-sm text-blue-700">Code: <span class="font-mono font-bold">${codes}</span></p>
									<p class="mt-1 text-sm text-blue-700 italic">${site_instr}</p>
								</div>

								<!-- Historical Context -->
								<div class="p-4 bg-gray-50 border-l-4 border-gray-400 rounded-r-md">
									<h3 class="flex items-center text-sm font-bold text-gray-800">
										<svg class="w-4 h-4 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>
										Recent Visits
									</h3>
									<div class="mt-2">
										${visits_html}
									</div>
								</div>
							</div>
						</div>
					`;
					frm.get_field("dashboard").$wrapper.html(dashboard_html);
					frm.trigger("toggle_safety_gate");
				}
			},
		});
	},
});
