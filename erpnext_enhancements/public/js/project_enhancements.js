/**
 * Project form script — Comments App + Procurement Tracker.
 *
 * Targets: the "Project" doctype form.
 * Loaded via: hooks.py `doctype_js["Project"]` (with vue.global.js + comments.js;
 *   one of several Project form scripts — see project.js).
 *
 * On saved Projects: mounts the custom Comments App into `custom_comments_field`
 * (see comments.js) and renders a self-contained Vue 3 "Procurement Tracker" into
 * `custom_material_request_feed`. The tracker fetches the full procurement chain
 * (erpnext_enhancements.project_enhancements.get_procurement_documents) and shows
 * a collapsible, searchable DocType -> document -> item tree with MR/RFQ/SQ/PO/PR/
 * PI/Stock-Entry status badges. Also wires the `custom_btn_*` buttons that create
 * project-linked procurement docs. Tracker styling lives in
 * desk_enhancements.bundle.css (`.procurement-tracker`, `.sapphire-theme`).
 */
frappe.ui.form.on("Project", {
	refresh: function (frm) {
		if (!frm.doc.__islocal) {
			frm.trigger("render_procurement_tracker");
			frm.trigger("render_comments_section");
		}
	},

	render_comments_section: function (frm) {
		if (erpnext_enhancements && erpnext_enhancements.render_comments_app) {
			erpnext_enhancements.render_comments_app(frm, "custom_comments_field");
		} else {
			console.error("erpnext_enhancements.render_comments_app is not defined.");
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
				method: "erpnext_enhancements.project_enhancements.get_procurement_documents",
				args: {
					project_name: frm.doc.name,
				},
				callback: function (r) {
					try {
						if (r.message && r.message.length) {
							const app = window.Vue.createApp({
								data() {
									const groups = r.message; // [{ doctype, documents: [{ name, date, supplier, status, items: [...] }] }]
									return {
										groups: groups,
										globalSearchTerm: '',
										// DocType groups collapsed by default.
										collapsedGroups: groups.reduce((acc, g) => {
											acc[g.doctype] = true;
											return acc;
										}, {}),
										// Individual documents collapsed by default (keyed "DocType::name").
										collapsedDocs: {},
									};
								},
								watch: {
									globalSearchTerm(newVal) {
										const tokens = this.tokenize(newVal);
										if (!tokens.length) return;

										// Auto-expand groups + documents that contain a match.
										this.groups.forEach(group => {
											let groupHasMatch = false;
											group.documents.forEach(doc => {
												if (this.docMatches(doc, tokens)) {
													groupHasMatch = true;
													this.collapsedDocs[this.docKey(group.doctype, doc.name)] = false;
												}
											});
											if (groupHasMatch) {
												this.collapsedGroups[group.doctype] = false;
											}
										});
									}
								},
								computed: {
									filteredGroups() {
										const tokens = this.tokenize(this.globalSearchTerm);
										if (!tokens.length) return this.groups;

										const out = [];
										this.groups.forEach(group => {
											const docs = [];
											group.documents.forEach(doc => {
												const matchedItems = this.filteredItems(doc, tokens);
												const docLevel = this.docLevelMatches(doc, tokens);
												if (matchedItems.length || docLevel) {
													// If only the header matched, keep all items; otherwise show matches.
													docs.push(Object.assign({}, doc, {
														items: matchedItems.length ? matchedItems : doc.items,
													}));
												}
											});
											if (docs.length) {
												out.push(Object.assign({}, group, { documents: docs }));
											}
										});
										return out;
									}
								},
								methods: {
									tokenize(term) {
										return (term || '').toLowerCase().split(/\s+/).filter(t => t);
									},
									docKey(doctype, name) {
										return doctype + '::' + name;
									},
									toggleGroup(doctype) {
										this.collapsedGroups[doctype] = !this.collapsedGroups[doctype];
									},
									toggleDoc(doctype, name) {
										const key = this.docKey(doctype, name);
										// Default (key absent) is collapsed; store false to expand, true to collapse.
										this.collapsedDocs[key] = (this.collapsedDocs[key] === false);
									},
									isDocCollapsed(doctype, name) {
										// Default (key absent) is collapsed.
										return this.collapsedDocs[this.docKey(doctype, name)] !== false;
									},
									openDoc(doctype, name) {
										if (doctype && name) frappe.set_route('Form', doctype, name);
									},
									itemMatches(item, tokens) {
										const str = Object.values(item).join(' ').toLowerCase();
										return tokens.every(t => str.includes(t));
									},
									filteredItems(doc, tokens) {
										if (!tokens.length) return doc.items;
										return (doc.items || []).filter(item => this.itemMatches(item, tokens));
									},
									docLevelMatches(doc, tokens) {
										const str = [doc.name, doc.supplier, doc.status]
											.filter(Boolean).join(' ').toLowerCase();
										return tokens.every(t => str.includes(t));
									},
									docMatches(doc, tokens) {
										return this.docLevelMatches(doc, tokens) || this.filteredItems(doc, tokens).length > 0;
									},
									formatDate(d) {
										return d ? frappe.datetime.str_to_user(d) : '-';
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

										<div v-if="filteredGroups.length === 0" class="text-center text-muted" style="padding: 20px;">
											No procurement records found.
										</div>

										<div v-for="group in filteredGroups" :key="group.doctype">
											<!-- Level 1: DocType group -->
											<div class="group-header" @click="toggleGroup(group.doctype)">
												<div class="group-title">
													<svg class="chevron" :class="{ 'expanded': !collapsedGroups[group.doctype] }" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
														<polyline points="9 18 15 12 9 6"></polyline>
													</svg>
													<span>{{ group.doctype }} ({{ group.documents.length }})</span>
												</div>
											</div>

											<div v-if="!collapsedGroups[group.doctype]" class="group-content">
												<!-- Level 2: documents of this DocType -->
												<div v-for="doc in group.documents" :key="doc.name" class="procurement-doc">
													<div class="doc-header" @click="toggleDoc(group.doctype, doc.name)">
														<svg class="chevron doc-chevron" :class="{ 'expanded': !isDocCollapsed(group.doctype, doc.name) }" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
															<polyline points="9 18 15 12 9 6"></polyline>
														</svg>
														<span class="doc-id doc-link" @click.stop="openDoc(group.doctype, doc.name)" v-html="highlight(doc.name, globalSearchTerm)"></span>
														<span class="doc-meta doc-date">{{ formatDate(doc.date) }}</span>
														<span class="doc-meta doc-supplier" v-html="highlight(doc.supplier || '-', globalSearchTerm)"></span>
														<span class="status-badge" :class="getStatusColorClass(doc.status)">{{ doc.status }}</span>
														<span class="doc-meta doc-itemcount">{{ doc.items.length }} item(s)</span>
													</div>

													<!-- Level 3: items inside the document -->
													<div v-if="!isDocCollapsed(group.doctype, doc.name)" class="doc-items">
														<div class="table-responsive">
															<table class="glass-table">
																<thead>
																	<tr>
																		<th>Item Details</th>
																		<th>Warehouse</th>
																		<th>Qty (Ord / Rec)</th>
																		<th>Status</th>
																		<th>Doc Chain</th>
																	</tr>
																</thead>
																<tbody>
																	<tr v-if="(doc.items || []).length === 0">
																		<td colspan="5" class="text-center text-muted">No items.</td>
																	</tr>
																	<tr v-for="(row, idx) in doc.items" :key="idx" class="procurement-item-row">
																		<td @click="row.source_doc_type && row.source_doc_name && openDoc(row.source_doc_type, row.source_doc_name)"
																			:class="{ 'doc-link': row.source_doc_type && row.source_doc_name }"
																			v-html="highlight(row.item_code + '<br><small class=\\\'text-muted\\\'>' + (row.item_name || '') + '</small>', globalSearchTerm)">
																		</td>
																		<td v-html="highlight(row.warehouse || '-', globalSearchTerm)"></td>
																		<td>{{ row.ordered_qty }} / {{ row.received_qty }}</td>
																		<td :class="row.completion_percentage >= 100 ? 'status-complete' : 'status-pending'">
																			{{ row.completion_percentage }}% Received
																		</td>
																		<td>
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
										</div>
									</div>
								`
							});
							app.mount('#procurement-tracker-app');
						} else {
							$(wrapper).html('<div class="text-center text-muted">No procurement records found.</div>');
						}
					} catch (e) {
						console.error(e);
						frappe.msgprint("Error inside procurement tracker callback: " + e.message);
						$(wrapper).html('<div class="text-danger">Error: ' + e.message + '</div>');
					}
				},
				error: function(r) {
					frappe.msgprint("Server Error in get_procurement_documents");
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
