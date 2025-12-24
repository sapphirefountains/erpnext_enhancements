frappe.ui.form.on("Project", {
	refresh: function (frm) {
		if (!frm.doc.__islocal) {
			frm.trigger("render_procurement_tracker");
		}
	},

	render_procurement_tracker: function (frm) {
		try {
			if (typeof Vue === 'undefined') {
				frappe.msgprint("Error: Vue is not defined. Please check if vue.global.js is loaded.");
				return;
			}

			if (!frm.fields_dict["custom_material_request_feed"]) {
				console.warn('Field "custom_material_request_feed" not found in Project DocType.');
				frappe.msgprint('Field "custom_material_request_feed" not found in Project DocType.');
				return;
			}

			const wrapper = frm.fields_dict["custom_material_request_feed"].wrapper;
			$(wrapper).html('<div id="procurement-tracker-app">Loading Procurement Tracker...</div>');

			frappe.call({
				method: "erpnext_enhancements.project_enhancements.get_procurement_status",
				args: {
					project_name: frm.doc.name,
				},
				callback: function (r) {
					try {
						if (r.message) {
							const app = Vue.createApp({
								data() {
									return {
										groupedItems: r.message,
										globalSearchTerm: '',
										groupSearchTerms: {},
										collapsedGroups: Object.keys(r.message).reduce((acc, key) => {
											acc[key] = true;
											return acc;
										}, {}),
									};
								},
								watch: {
									globalSearchTerm(newVal) {
										const term = newVal.toLowerCase();
										if (!term) {
											// Collapse all when search is cleared
											this.getGroupKeys().forEach(doctype => this.collapsedGroups[doctype] = true);
											return;
										}
										this.getGroupKeys().forEach(doctype => {
											const hasMatch = this.groupedItems[doctype].some(item =>
												Object.values(item).some(val => String(val).toLowerCase().includes(term))
											);
											this.collapsedGroups[doctype] = !hasMatch;
										});
									}
								},
								computed: {
									filteredGroups() {
										const result = {};
										const globalTerm = this.globalSearchTerm.toLowerCase();

										for (const doctype in this.groupedItems) {
											const groupTerm = (this.groupSearchTerms[doctype] || '').toLowerCase();

											const items = this.groupedItems[doctype].filter(item => {
												const matchesGlobal = !globalTerm || Object.values(item).some(val =>
													String(val).toLowerCase().includes(globalTerm)
												);
												const matchesGroup = !groupTerm || Object.values(item).some(val =>
													String(val).toLowerCase().includes(groupTerm)
												);
												return matchesGlobal && matchesGroup;
											});

											result[doctype] = items;
										}
										return result;
									}
								},
								methods: {
									toggleGroup(doctype) {
										this.collapsedGroups[doctype] = !this.collapsedGroups[doctype];
									},
									openDoc(doctype, name) {
										frappe.set_route('Form', doctype, name);
									},
									getGroupKeys() {
										return Object.keys(this.groupedItems);
									},
									highlight(text, term) {
										if (!term) return text;
										const escapedTerm = term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
										const regex = new RegExp(`(${escapedTerm})`, 'gi');
										return String(text).replace(regex, '<b>$1</b>');
									}
								},
								template: `
									<div class="procurement-tracker">
										<style>
											.procurement-table { width: 100%; border-collapse: collapse; font-size: 12px; }
											.procurement-table th, .procurement-table td { border: 1px solid #d1d8dd; padding: 8px; text-align: left; }
											.procurement-table th { background-color: #f8f9fa; font-weight: bold; }
											.status-complete { color: #28a745; font-weight: bold; }
											.status-pending { color: #fd7e14; font-weight: bold; }
											.doc-link { color: #007bff; cursor: pointer; text-decoration: underline; }
											.doc-link:hover { color: #0056b3; }
											.group-header {
												background-color: #f8f9fa;
												padding: 12px 15px;
												cursor: pointer;
												border: 1px solid #d1d8dd;
												border-radius: 4px;
												margin-top: 10px;
												font-weight: 600; /* Bolder */
												display: flex;
												justify-content: space-between;
												align-items: center;
												transition: background-color 0.2s;
											}
											.group-header:hover { background-color: #e9ecef; }
											.search-bar { margin-bottom: 15px; padding: 8px; width: 100%; border: 1px solid #ccc; border-radius: 4px; }
											b { background-color: yellow; }
										</style>

										<input type="text" v-model="globalSearchTerm" placeholder="Search all documents..." class="search-bar">

										<div v-if="getGroupKeys().length === 0" class="text-center text-muted">
											No procurement records found.
										</div>

										<div v-for="doctype in getGroupKeys()" :key="doctype">
											<div class="group-header" @click="toggleGroup(doctype)">
												<span>{{ doctype }}</span>
												<span class="badge">{{ groupedItems[doctype].length }}</span>
											</div>
											<div v-if="!collapsedGroups[doctype]" class="group-content" style="padding: 10px; border: 1px solid #ddd; border-top: none;">
												<input type="text" v-model="groupSearchTerms[doctype]" :placeholder="'Search in ' + doctype + '...'" class="search-bar" style="margin-top: 10px;">
												<div class="table-responsive">
													<table class="table table-bordered procurement-table">
														<thead>
															<tr>
																<th>Item Details</th>
																<th>Doc Chain</th>
																<th>Warehouse</th>
																<th>Qty (Ord / Rec)</th>
																<th>Status</th>
															</tr>
														</thead>
														<tbody>
															<tr v-if="(filteredGroups[doctype] || []).length === 0">
																<td colspan="5" class="text-center text-muted">No matching records found.</td>
															</tr>
															<tr v-for="row in filteredGroups[doctype]" :key="row.item_code + row.mr">
																<td v-html="highlight(row.item_code + '<br><small class=\\\'text-muted\\\'>' + row.item_name + '</small>', globalSearchTerm || groupSearchTerms[doctype])"></td>
																<td>
																	<!-- Doc Chain rendering -->
																	<div v-if="row.mr"><span class="doc-link" @click="openDoc('Material Request', row.mr)" v-html="highlight(row.mr, globalSearchTerm || groupSearchTerms[doctype])"></span> <span class="text-muted">({{ row.mr_status }})</span></div>
																	<div v-if="row.rfq"><span class="text-muted">↓</span><br><span class="doc-link" @click="openDoc('Request for Quotation', row.rfq)" v-html="highlight(row.rfq, globalSearchTerm || groupSearchTerms[doctype])"></span> <span class="text-muted">({{ row.rfq_status }})</span></div>
																	<div v-if="row.sq"><span class="text-muted">↓</span><br><span class="doc-link" @click="openDoc('Supplier Quotation', row.sq)" v-html="highlight(row.sq, globalSearchTerm || groupSearchTerms[doctype])"></span> <span class="text-muted">({{ row.sq_status }})</span></div>
																	<div v-if="row.po"><span class="text-muted">↓</span><br><span class="doc-link" @click="openDoc('Purchase Order', row.po)" v-html="highlight(row.po, globalSearchTerm || groupSearchTerms[doctype])"></span> <span class="text-muted">({{ row.po_status }})</span></div>
																	<div v-if="row.pr"><span class="text-muted">↓</span><br><span class="doc-link" @click="openDoc('Purchase Receipt', row.pr)" v-html="highlight(row.pr, globalSearchTerm || groupSearchTerms[doctype])"></span> <span class="text-muted">({{ row.pr_status }})</span></div>
																	<div v-if="row.pi"><span class="text-muted">↓</span><br><span class="doc-link" @click="openDoc('Purchase Invoice', row.pi)" v-html="highlight(row.pi, globalSearchTerm || groupSearchTerms[doctype])"></span> <span class="text-muted">({{ row.pi_status }})</span></div>
																	<div v-if="row.stock_entry"><span class="text-muted">↓</span><br><span class="doc-link" @click="openDoc('Stock Entry', row.stock_entry)" v-html="highlight(row.stock_entry, globalSearchTerm || groupSearchTerms[doctype])"></span> <span class="text-muted">({{ row.stock_entry_status }})</span></div>
																</td>
																<td v-html="highlight(row.warehouse || '-', globalSearchTerm || groupSearchTerms[doctype])"></td>
																<td>{{ row.ordered_qty }} / {{ row.received_qty }}</td>
																<td :class="row.completion_percentage >= 100 ? 'status-complete' : 'status-pending'">
																	{{ row.completion_percentage }}% Received
																</td>
															</tr>
														</tbody>
													</table>
												</div>
											</div>
										</div>
									</div>
								`
							});
							app.mount('#procurement-tracker-app');
						} else {
							$(wrapper).html('<div class="text-center text-muted">No procurement data returned.</div>');
						}
					} catch (e) {
						console.error(e);
						frappe.msgprint("Error inside procurement tracker callback: " + e.message);
						$(wrapper).html('<div class="text-danger">Error: ' + e.message + '</div>');
					}
				},
				error: function(r) {
					frappe.msgprint("Server Error in get_procurement_status");
					 $(wrapper).html('<div class="text-danger">Server Error</div>');
				}
			});
		} catch (e) {
			console.error(e);
			frappe.msgprint("Error in render_procurement_tracker: " + e.message);
		}
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
