frappe.ui.form.on("Project", {
	refresh: function (frm) {
		if (!frm.doc.__islocal) {
			frm.trigger("render_procurement_tracker");
		}
	},

	render_procurement_tracker: function (frm) {
		// Check if the custom field exists
		if (!frm.fields_dict["custom_material_request_feed"]) {
			console.warn('Field "custom_material_request_feed" not found in Project DocType.');
			return;
		}

		// Container for Vue app
		const wrapper = frm.fields_dict["custom_material_request_feed"].wrapper;
		$(wrapper).html('<div id="procurement-tracker-app">Loading Procurement Tracker...</div>');

		frappe.call({
			method: "erpnext_enhancements.project_enhancements.get_procurement_status",
			args: {
				project_name: frm.doc.name,
			},
			callback: function (r) {
				if (r.message) {
					const data = r.message;

					// Mount Vue App
					const app = Vue.createApp({
						data() {
							return {
								items: data,
								sortKey: 'completion_percentage',
								sortOrder: 'asc'
							};
						},
						computed: {
							sortedItems() {
								return this.items.sort((a, b) => {
									let modifier = this.sortOrder === 'desc' ? -1 : 1;
									if (a[this.sortKey] < b[this.sortKey]) return -1 * modifier;
									if (a[this.sortKey] > b[this.sortKey]) return 1 * modifier;
									return 0;
								});
							}
						},
						methods: {
							sortBy(key) {
								if (this.sortKey === key) {
									this.sortOrder = this.sortOrder === 'asc' ? 'desc' : 'asc';
								} else {
									this.sortKey = key;
									this.sortOrder = 'asc';
								}
							},
							openDoc(doctype, name) {
								frappe.set_route('Form', doctype, name);
							}
						},
						template: `
							<div class="procurement-tracker">
								<style>
									.procurement-table { width: 100%; border-collapse: collapse; font-size: 12px; }
									.procurement-table th, .procurement-table td { border: 1px solid #d1d8dd; padding: 8px; text-align: left; }
									.procurement-table th { background-color: #f8f9fa; font-weight: bold; cursor: pointer; }
									.procurement-table th:hover { background-color: #e9ecef; }
									.status-complete { color: #28a745; font-weight: bold; }
									.status-pending { color: #fd7e14; font-weight: bold; }
									.doc-link { color: #007bff; cursor: pointer; text-decoration: underline; }
									.doc-link:hover { color: #0056b3; }
									.arrow { display: inline-block; vertical-align: middle; width: 0; height: 0; margin-left: 5px; opacity: 0.66; }
									.arrow.asc { border-left: 4px solid transparent; border-right: 4px solid transparent; border-bottom: 4px solid #000; }
									.arrow.desc { border-left: 4px solid transparent; border-right: 4px solid transparent; border-top: 4px solid #000; }
								</style>
								<div class="table-responsive">
									<table class="table table-bordered procurement-table">
										<thead>
											<tr>
												<th @click="sortBy('item_code')">
													Item Details
													<span v-if="sortKey == 'item_code'" :class="['arrow', sortOrder]"></span>
												</th>
												<th>Doc Chain</th>
												<th @click="sortBy('warehouse')">
													Warehouse
													<span v-if="sortKey == 'warehouse'" :class="['arrow', sortOrder]"></span>
												</th>
												<th>Qty (Ord / Rec)</th>
												<th @click="sortBy('completion_percentage')">
													Status
													<span v-if="sortKey == 'completion_percentage'" :class="['arrow', sortOrder]"></span>
												</th>
											</tr>
										</thead>
										<tbody>
											<tr v-if="items.length === 0">
												<td colspan="5" class="text-center text-muted">No procurement records found.</td>
											</tr>
											<tr v-for="row in sortedItems" :key="row.item_code + row.mr">
												<td>
													<strong class="doc-link" @click="openDoc('Item', row.item_code)">{{ row.item_code }}</strong><br>
													<span class="text-muted">{{ row.item_name }}</span>
												</td>
												<td>
													<div v-if="row.mr">
														<span class="doc-link" @click="openDoc('Material Request', row.mr)">{{ row.mr }}</span>
														<span class="text-muted">({{ row.mr_status }})</span>
													</div>
													<div v-if="row.rfq">
														<span class="text-muted">↓</span><br>
														<span class="doc-link" @click="openDoc('Request for Quotation', row.rfq)">{{ row.rfq }}</span>
														<span class="text-muted">({{ row.rfq_status }})</span>
													</div>
													<div v-if="row.sq">
														<span class="text-muted">↓</span><br>
														<span class="doc-link" @click="openDoc('Supplier Quotation', row.sq)">{{ row.sq }}</span>
														<span class="text-muted">({{ row.sq_status }})</span>
													</div>
													<div v-if="row.po">
														<span class="text-muted">↓</span><br>
														<span class="doc-link" @click="openDoc('Purchase Order', row.po)">{{ row.po }}</span>
														<span class="text-muted">({{ row.po_status }})</span>
													</div>
													<div v-if="row.pr">
														<span class="text-muted">↓</span><br>
														<span class="doc-link" @click="openDoc('Purchase Receipt', row.pr)">{{ row.pr }}</span>
														<span class="text-muted">({{ row.pr_status }})</span>
													</div>
													<div v-if="row.pi">
														<span class="text-muted">↓</span><br>
														<span class="doc-link" @click="openDoc('Purchase Invoice', row.pi)">{{ row.pi }}</span>
														<span class="text-muted">({{ row.pi_status }})</span>
													</div>
												</td>
												<td>{{ row.warehouse || "-" }}</td>
												<td>{{ row.ordered_qty }} / {{ row.received_qty }}</td>
												<td :class="row.completion_percentage >= 100 ? 'status-complete' : 'status-pending'">
													{{ row.completion_percentage }}% Received
												</td>
											</tr>
										</tbody>
									</table>
								</div>
							</div>
						`
					});

					app.mount('#procurement-tracker-app');
				}
			},
		});
	},

	custom_btn_material_request: function (frm) {
		if (frm.is_new()) {
			frappe.msgprint(__("Please save the Project before creating linked documents."));
			return;
		}
		frappe.new_doc("Material Request", {
			custom_project: frm.doc.name,
			project: frm.doc.name,
		});
	},

	custom_btn_request_quote: function (frm) {
		if (frm.is_new()) {
			frappe.msgprint(__("Please save the Project before creating linked documents."));
			return;
		}
		frappe.new_doc("Request for Quotation", {
			custom_project: frm.doc.name,
			project: frm.doc.name,
		});
	},

	custom_btn_supplier_quotation: function (frm) {
		if (frm.is_new()) {
			frappe.msgprint(__("Please save the Project before creating linked documents."));
			return;
		}
		frappe.new_doc("Supplier Quotation", {
			project: frm.doc.name,
		});
	},

	custom_btn_purchase_order: function (frm) {
		if (frm.is_new()) {
			frappe.msgprint(__("Please save the Project before creating linked documents."));
			return;
		}
		frappe.new_doc("Purchase Order", {
			project: frm.doc.name,
		});
	},

	custom_btn_purchase_receipt: function (frm) {
		if (frm.is_new()) {
			frappe.msgprint(__("Please save the Project before creating linked documents."));
			return;
		}
		frappe.new_doc("Purchase Receipt", {
			project: frm.doc.name,
		});
	},

	custom_btn_purchase_invoice: function (frm) {
		if (frm.is_new()) {
			frappe.msgprint(__("Please save the Project before creating linked documents."));
			return;
		}
		frappe.new_doc("Purchase Invoice", {
			project: frm.doc.name,
		});
	},
});
