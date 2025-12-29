frappe.ui.form.on("Project", {
	refresh: function (frm) {
		if (!frm.doc.__islocal) {
			frm.trigger("render_procurement_tracker");
		}
	},

	render_procurement_tracker: function (frm) {
		try {
			if (typeof window.Vue === 'undefined') {
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
							const app = window.Vue.createApp({
								data() {
									return {
										groupedItems: r.message,
										globalSearchTerm: '',
										sortKey: '',
										sortOrder: 'asc',
										collapsedGroups: Object.keys(r.message).reduce((acc, key) => {
											acc[key] = true; // Collapsed by default
											return acc;
										}, {}),
									};
								},
								watch: {
									globalSearchTerm(newVal) {
										const term = newVal.toLowerCase();
										if (!term) return;

                                        const tokens = term.split(/\s+/).filter(t => t);

										// Auto-expand groups with matches
										this.getGroupKeys().forEach(doctype => {
											const hasMatch = this.groupedItems[doctype].some(item => {
                                                const itemStr = Object.values(item).join(' ').toLowerCase();
                                                return tokens.every(t => itemStr.includes(t));
                                            });
											if (hasMatch) {
                                                this.collapsedGroups[doctype] = false;
                                            }
										});
									}
								},
								computed: {
									filteredGroups() {
										const result = {};
										const globalTerm = this.globalSearchTerm.toLowerCase();
                                        const tokens = globalTerm.split(/\s+/).filter(t => t);

										for (const doctype in this.groupedItems) {
											// Filter
											let items = this.groupedItems[doctype].filter(item => {
                                                if (!globalTerm) return true;
                                                const itemStr = Object.values(item).join(' ').toLowerCase();
                                                return tokens.every(t => itemStr.includes(t));
											});

											// Sort
											if (this.sortKey) {
												items.sort((a, b) => {
													let valA = this.getSortValue(a, this.sortKey);
													let valB = this.getSortValue(b, this.sortKey);

													if (valA < valB) return this.sortOrder === 'asc' ? -1 : 1;
													if (valA > valB) return this.sortOrder === 'asc' ? 1 : -1;
													return 0;
												});
											}

											result[doctype] = items;
										}
										return result;
									},
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
									getSortValue(item, key) {
										if (key === 'qty') return item.ordered_qty || 0;
										if (key === 'status') return item.completion_percentage || 0;
										// For doc chain, sort by the ID of the main document of that group if possible, or MR
										if (key === 'doc_chain') return item.mr || item.po || '';
										return (item[key] || '').toString().toLowerCase();
									},
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
										if (!term || !text) return text;
                                        const tokens = term.split(/\s+/).filter(t => t);
                                        if (tokens.length === 0) return text;

										const escapedTokens = tokens.map(t => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
										const regex = new RegExp(`(${escapedTokens.join('|')})`, 'gi');
										return String(text).replace(regex, '<mark>$1</mark>');
									},
                                    getStatusColorClass(status) {
                                        if (!status) return '';
                                        const s = status.toLowerCase();
                                        if (s.includes('draft')) return 'status-draft';
                                        if (s.includes('received') || s.includes('bill') || s === 'completed') return 'status-completed';
                                        if (s.includes('cancel') || s.includes('closed')) return 'status-cancelled';
                                        if (s.includes('submit') || s.includes('ordered')) return 'status-submitted';
                                        return 'status-pending';
                                    }
								},
								template: `
									<div class="procurement-tracker sapphire-theme">
										<div class="sticky-search-bar">
											<input type="text" v-model="globalSearchTerm" placeholder="Search all documents..." class="glass-input">
										</div>

										<div v-if="getGroupKeys().length === 0" class="text-center text-muted" style="padding: 20px;">
											No procurement records found.
										</div>

										<div v-for="doctype in getGroupKeys()" :key="doctype">
											<div class="group-header" @click="toggleGroup(doctype)">
												<div class="group-title">
													<!-- Chevron Icon (SVG) -->
													<svg class="chevron" :class="{ 'expanded': !collapsedGroups[doctype] }" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
														<polyline points="9 18 15 12 9 6"></polyline>
													</svg>
													<span>{{ doctype }}</span>
												</div>
												<span class="badge-count">{{ groupedItems[doctype].length }}</span>
											</div>

											<div v-if="!collapsedGroups[doctype]" class="group-content">
												<div class="table-responsive">
													<table class="glass-table">
														<thead>
															<tr>
																<th @click="sortBy('item_code')" style="cursor: pointer;">
																	Item Details
																	<span v-if="sortKey === 'item_code'">{{ sortOrder === 'asc' ? '▲' : '▼' }}</span>
																</th>
																<th @click="sortBy('warehouse')" style="cursor: pointer;">
																	Warehouse
																	<span v-if="sortKey === 'warehouse'">{{ sortOrder === 'asc' ? '▲' : '▼' }}</span>
																</th>
																<th @click="sortBy('qty')" style="cursor: pointer;">
																	Qty (Ord / Rec)
																	<span v-if="sortKey === 'qty'">{{ sortOrder === 'asc' ? '▲' : '▼' }}</span>
																</th>
																<th @click="sortBy('status')" style="cursor: pointer;">
																	Status
																	<span v-if="sortKey === 'status'">{{ sortOrder === 'asc' ? '▲' : '▼' }}</span>
																</th>
																<th @click="sortBy('doc_chain')" style="cursor: pointer;">
																	Doc Chain
																	<span v-if="sortKey === 'doc_chain'">{{ sortOrder === 'asc' ? '▲' : '▼' }}</span>
																</th>
															</tr>
														</thead>
														<tbody>
															<tr v-if="(filteredGroups[doctype] || []).length === 0">
																<td colspan="5" class="text-center text-muted">No matching records found.</td>
															</tr>
															<tr v-for="row in filteredGroups[doctype]" :key="row.item_code + (row.mr || row.po)">
																<td v-html="highlight(row.item_code + '<br><small class=\\\'text-muted\\\'>' + row.item_name + '</small>', globalSearchTerm)"></td>
																<td v-html="highlight(row.warehouse || '-', globalSearchTerm)"></td>
																<td>{{ row.ordered_qty }} / {{ row.received_qty }}</td>
																<td :class="row.completion_percentage >= 100 ? 'status-complete' : 'status-pending'">
																	{{ row.completion_percentage }}% Received
																</td>
																<td>
																	<!-- Doc Chain rendering -->
                                                                    <div class="doc-chain-container">
                                                                        <div v-if="row.mr" class="doc-chain-step">
                                                                            <span class="text-muted" style="margin-right:4px;">MR:</span>
                                                                            <span class="doc-link" @click="openDoc('Material Request', row.mr)" v-html="highlight(row.mr, globalSearchTerm)"></span>
                                                                            <span class="status-badge" :class="getStatusColorClass(row.mr_status)">{{ row.mr_status }}</span>
                                                                        </div>
                                                                        <div v-if="row.rfq" class="doc-chain-step">
                                                                            <span class="text-muted" style="margin-right:4px;">RFQ:</span>
                                                                            <span class="doc-link" @click="openDoc('Request for Quotation', row.rfq)" v-html="highlight(row.rfq, globalSearchTerm)"></span>
                                                                            <span class="status-badge" :class="getStatusColorClass(row.rfq_status)">{{ row.rfq_status }}</span>
                                                                        </div>
                                                                        <div v-if="row.sq" class="doc-chain-step">
                                                                            <span class="text-muted" style="margin-right:4px;">SQ:</span>
                                                                            <span class="doc-link" @click="openDoc('Supplier Quotation', row.sq)" v-html="highlight(row.sq, globalSearchTerm)"></span>
                                                                            <span class="status-badge" :class="getStatusColorClass(row.sq_status)">{{ row.sq_status }}</span>
                                                                        </div>
                                                                        <div v-if="row.po" class="doc-chain-step">
                                                                            <span class="text-muted" style="margin-right:4px;">PO:</span>
                                                                            <span class="doc-link" @click="openDoc('Purchase Order', row.po)" v-html="highlight(row.po, globalSearchTerm)"></span>
                                                                            <span class="status-badge" :class="getStatusColorClass(row.po_status)">{{ row.po_status }}</span>
                                                                        </div>
                                                                        <div v-if="row.pr" class="doc-chain-step">
                                                                            <span class="text-muted" style="margin-right:4px;">PR:</span>
                                                                            <span class="doc-link" @click="openDoc('Purchase Receipt', row.pr)" v-html="highlight(row.pr, globalSearchTerm)"></span>
                                                                            <span class="status-badge" :class="getStatusColorClass(row.pr_status)">{{ row.pr_status }}</span>
                                                                        </div>
                                                                        <div v-if="row.pi" class="doc-chain-step">
                                                                            <span class="text-muted" style="margin-right:4px;">PI:</span>
                                                                            <span class="doc-link" @click="openDoc('Purchase Invoice', row.pi)" v-html="highlight(row.pi, globalSearchTerm)"></span>
                                                                            <span class="status-badge" :class="getStatusColorClass(row.pi_status)">{{ row.pi_status }}</span>
                                                                        </div>
                                                                        <div v-if="row.stock_entry" class="doc-chain-step">
                                                                            <span class="text-muted" style="margin-right:4px;">SE:</span>
                                                                            <span class="doc-link" @click="openDoc('Stock Entry', row.stock_entry)" v-html="highlight(row.stock_entry, globalSearchTerm)"></span>
                                                                            <span class="status-badge" :class="getStatusColorClass(row.stock_entry_status)">{{ row.stock_entry_status }}</span>
                                                                        </div>
                                                                    </div>
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
