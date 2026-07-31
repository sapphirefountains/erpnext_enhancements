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
/**
 * Sortable columns for the Procurement Tracker's item table.
 *
 * Declared once, outside the Vue options object, so the template and the comparator
 * cannot drift apart — a header rendered against a key the sorter does not know is a
 * click that silently does nothing.
 *
 * Doc Chain is deliberately absent. It is a multi-row cell holding up to seven chain
 * nodes; there is no single value to order by, and giving it a click handler would mean
 * inventing an ordering that the column does not show.
 */
const PROCUREMENT_RECEIVE_RANK = ["Not Received", "Partially Received", "Received", "Over Received"];

const PROCUREMENT_SORT_COLUMNS = {
	item: {
		label: "Item Details",
		type: "text",
		value: (r) => `${r.item_code || ""} ${r.item_name || ""}`.trim(),
	},
	warehouse: { label: "Warehouse", type: "text", value: (r) => r.warehouse },
	requested: { label: "Requested", cls: "qty-col", type: "number", value: (r) => r.requested_qty },
	ordered: { label: "Ordered", cls: "qty-col", type: "number", value: (r) => r.ordered_qty },
	received: { label: "Received", cls: "qty-col", type: "number", value: (r) => r.received_qty },
	// Workflow order, not A-Z. Alphabetical gives Not Received / Partially Received /
	// Received, which interleaves "done" between two "not done" states. Worst-first
	// ascending means one click surfaces exactly the lines somebody has to chase.
	status: {
		label: "Status",
		type: "rank",
		value: (r) => r.receive_status,
		ranks: PROCUREMENT_RECEIVE_RANK,
	},
};

/**
 * Blanks sort last in BOTH directions, so the return value is never multiplied by the
 * sort direction. A missing warehouse is not "before A" — it is absent information, and
 * absent information belongs at the bottom whichever way you sorted.
 *
 * Zero is not blank. An ordered quantity of 0 is a real, meaningful value and sorts at
 * the numeric bottom with the numbers, which is why these are strict comparisons and
 * not a falsy check.
 *
 * Returns null when both values are present, meaning "no opinion, go and compare them".
 */
function procurementBlankOrder(a, b) {
	const aBlank = a === null || a === undefined || a === "";
	const bBlank = b === null || b === undefined || b === "";
	if (!aBlank && !bBlank) return null;
	if (aBlank && bBlank) return 0;
	return aBlank ? 1 : -1;
}

function procurementCompare(a, b, column) {
	if (column.type === "number") return Number(a) - Number(b);
	if (column.type === "rank") {
		// An unrecognised status sorts after every known one rather than at -1, which
		// would silently put it first.
		const ai = column.ranks.indexOf(a);
		const bi = column.ranks.indexOf(b);
		return (ai < 0 ? column.ranks.length : ai) - (bi < 0 ? column.ranks.length : bi);
	}
	// `numeric` matters here: this site's item codes are 417-080, 417-100, 2622-010,
	// which a plain string compare orders wrong.
	return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: "base" });
}

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
										// Sort state per document, same key shape. Per-document
										// rather than global: expansion already works that way, two
										// documents in one group can want different sorts, and a
										// global sort would silently reorder collapsed tables
										// nobody asked about.
										sortByDoc: {},
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
									// Headers are rendered from the same registry the comparator
									// reads, so a column cannot exist in one and not the other.
									sortableColumns() {
										return Object.keys(PROCUREMENT_SORT_COLUMNS).map((key) => ({
											key: key,
											label: PROCUREMENT_SORT_COLUMNS[key].label,
											cls: PROCUREMENT_SORT_COLUMNS[key].cls || '',
										}));
									},
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
									sortFor(doctype, name) {
										return this.sortByDoc[this.docKey(doctype, name)] || null;
									},
									toggleSort(doctype, name, key) {
										if (!PROCUREMENT_SORT_COLUMNS[key]) return;
										const dk = this.docKey(doctype, name);
										const current = this.sortByDoc[dk];
										if (!current || current.key !== key) {
											this.sortByDoc[dk] = { key: key, dir: 'asc' };
										} else if (current.dir === 'asc') {
											this.sortByDoc[dk] = { key: key, dir: 'desc' };
										} else {
											// Third click clears it. Without a way back, line order is
											// lost until the form is reloaded — and line order is
											// meaningful on a request, it is how the job was grouped.
											this.sortByDoc[dk] = null;
										}
									},
									isSorted(doctype, name, key) {
										const s = this.sortFor(doctype, name);
										return !!(s && s.key === key);
									},
									sortIndicator(doctype, name, key) {
										const s = this.sortFor(doctype, name);
										if (!s || s.key !== key) return '⇅';
										return s.dir === 'asc' ? '↑' : '↓';
									},
									sortAria(doctype, name, key) {
										const s = this.sortFor(doctype, name);
										if (!s || s.key !== key) return 'none';
										return s.dir === 'asc' ? 'ascending' : 'descending';
									},
									// Search filters first, then sort sorts — the order a user expects,
									// and it leaves filteredGroups, the auto-expand watcher and
									// highlight() completely untouched.
									displayItems(doctype, doc) {
										const items = doc.items || [];
										const sort = this.sortFor(doctype, doc.name);
										const column = sort && PROCUREMENT_SORT_COLUMNS[sort.key];
										if (!column) return items;

										const dir = sort.dir === 'desc' ? -1 : 1;
										// slice() before sort(): Array.prototype.sort mutates in place,
										// and mutating doc.items during a render is an infinite
										// reactivity loop. This method runs on every render.
										return items.slice().sort((a, b) => {
											const av = column.value(a);
											const bv = column.value(b);
											const blanks = procurementBlankOrder(av, bv);
											// Not multiplied by dir — see procurementBlankOrder.
											if (blanks !== null) return blanks;
											return dir * procurementCompare(av, bv, column);
										});
									},
									// Quantities arrive as floats. "4" reads as a quantity; "4.0"
									// reads as a rounding artefact, and a column of them is noise.
									fmtQty(value) {
										if (value === null || value === undefined || value === '') return '-';
										const n = Number(value);
										if (!isFinite(n)) return '-';
										return Number.isInteger(n) ? String(n) : String(parseFloat(n.toFixed(3)));
									},
									// The figures are in the stock UOM; the line may have been written
									// in another. Say so rather than leaving someone to wonder why a
									// 120 FT line reads 120 against a stock UOM of Unit.
									uomTitle(row) {
										if (!row.stock_uom) return '';
										if (row.uom && row.uom !== row.stock_uom) {
											return `Quantities in ${row.stock_uom}. Line written in ${row.uom}.`;
										}
										return `Quantities in ${row.stock_uom}.`;
									},
									lineReceiveTitle(row) {
										const parts = [`This line: ${this.fmtQty(row.received_qty)} of ${this.fmtQty(row.ordered_qty)} received`];
										if (row.over_received_qty) {
											parts.push(`${this.fmtQty(row.over_received_qty)} more than was ordered`);
										}
										return parts.join(' · ');
									},
									// Hidden when there is nothing to total, so the pre-order doctypes
									// (RFQ, Supplier Quotation) do not sprout a row of zeroes that
									// reads as a failure rather than an absence.
									hasRollup(doc) {
										const r = doc.rollup;
										return !!(r && (r.requested_qty || r.ordered_qty || r.received_qty));
									},
									rollupText(doc) {
										const r = doc.rollup || {};
										const parts = [];
										if (r.requested_qty) parts.push(`${this.fmtQty(r.requested_qty)} req`);
										if (r.ordered_qty) parts.push(`${this.fmtQty(r.ordered_qty)} ord`);
										parts.push(`${this.fmtQty(r.received_qty || 0)} rec`);
										return parts.join(' · ');
									},
									rollupTitle(doc) {
										const r = doc.rollup || {};
										// No unit: a document's lines can span UOMs, so a single total
										// carries no one unit. Per-line units are on the cells.
										return [
											`${r.item_count || 0} line(s)`,
											`ordered: ${r.order_status}`,
											`received: ${r.receive_status}`,
										].join(' · ');
									},
									// Spells out the arithmetic behind an item row's badge, and names
									// the parent's own status alongside it — the two legitimately
									// differ, and seeing them together is what makes it obvious that
									// the row is no longer just echoing its parent.
									lineOrderTitle(row) {
										const parts = [];
										if (row.requested_qty !== null && row.requested_qty !== undefined) {
											parts.push(`This line: ${this.fmtQty(row.ordered_qty)} of ${this.fmtQty(row.requested_qty)} ordered`);
										} else {
											parts.push(`This line: ${this.fmtQty(row.ordered_qty)} ordered (no request behind it)`);
										}
										if (row.draft_ordered_qty) {
											parts.push(`${this.fmtQty(row.draft_ordered_qty)} on draft orders (not counted)`);
										}
										if (row.mr_status) {
											parts.push(`Request ${row.mr} is ${row.mr_status}`);
										}
										return parts.join(' · ');
									},
									getStatusColorClass(status) {
										if (!status) return '';

										// Our own vocabulary first, by exact match. The substring
										// heuristic below cannot separate "Partially Ordered" from
										// "Ordered" (both contain "ordered") or "Partially Received"
										// from "Received", so it painted a half-done line and a
										// finished one identically — the second half of the
										// item-status bug, which a correct status string alone would
										// not have fixed.
										const known = {
											'Not Ordered': 'status-pending',
											'Partially Ordered': 'status-partial',
											'Ordered': 'status-submitted',
											'Over Ordered': 'status-warning',
											'Not Received': 'status-pending',
											'Partially Received': 'status-partial',
											'Received': 'status-completed',
											'Over Received': 'status-warning',
										};
										if (known[status]) return known[status];

										// Fallback for ERPNext's own status strings, which still
										// reach the RFQ/SQ/PO/PR/PI/Stock Entry badges. `partial`
										// is tested before the generic terms for the same reason.
										const s = status.toLowerCase();
										if (s.includes('draft')) return 'status-draft';
										if (s.includes('partial')) return 'status-partial';
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
														<!-- One span rather than three: .doc-supplier is the only element
														     in this flex row that grows, so three nowrap spans would
														     squeeze a supplier name to nothing on a narrow screen. Hidden
														     entirely when there is nothing to total, so an RFQ header does
														     not gain a "0 req · 0 ord · 0 rec" that reads as an error. -->
														<span v-if="hasRollup(doc)" class="doc-meta doc-qty-rollup" :title="rollupTitle(doc)">{{ rollupText(doc) }}</span>
														<span class="doc-meta doc-itemcount">{{ doc.items.length }} item(s)</span>
													</div>

													<!-- Level 3: items inside the document -->
													<div v-if="!isDocCollapsed(group.doctype, doc.name)" class="doc-items">
														<div class="table-responsive">
															<table class="glass-table">
																<thead>
																	<tr>
																		<th v-for="col in sortableColumns" :key="col.key"
																			class="sortable"
																			:class="[col.cls, { sorted: isSorted(group.doctype, doc.name, col.key) }]"
																			role="button" tabindex="0"
																			:aria-sort="sortAria(group.doctype, doc.name, col.key)"
																			:title="'Sort by ' + col.label"
																			@click="toggleSort(group.doctype, doc.name, col.key)"
																			@keydown.enter.prevent="toggleSort(group.doctype, doc.name, col.key)"
																			@keydown.space.prevent="toggleSort(group.doctype, doc.name, col.key)">{{ col.label }}<span class="sort-indicator">{{ sortIndicator(group.doctype, doc.name, col.key) }}</span></th>
																		<!-- Not sortable: seven chain nodes in one cell, no single
																		     value to order by. No click handler, no cursor. -->
																		<th>Doc Chain</th>
																	</tr>
																</thead>
																<tbody>
																	<tr v-if="(doc.items || []).length === 0">
																		<td colspan="7" class="text-center text-muted">No items.</td>
																	</tr>
																	<tr v-for="row in displayItems(group.doctype, doc)" :key="row.row_id" class="procurement-item-row">
																		<td @click="row.source_doc_type && row.source_doc_name && openDoc(row.source_doc_type, row.source_doc_name)"
																			:class="{ 'doc-link': row.source_doc_type && row.source_doc_name }"
																			v-html="highlight(row.item_code + '<br><small class=\\\'text-muted\\\'>' + (row.item_name || '') + '</small>', globalSearchTerm)">
																		</td>
																		<td v-html="highlight(row.warehouse || '-', globalSearchTerm)"></td>
																		<!-- "-", not 0, when nothing was requested: a direct Purchase
																		     Order line has no request behind it, and "nobody asked" is
																		     a different fact from "asked for none". -->
																		<td class="qty-col" :title="uomTitle(row)">{{ fmtQty(row.requested_qty) }}</td>
																		<td class="qty-col" :title="lineOrderTitle(row)">
																			{{ fmtQty(row.ordered_qty) }}<span v-if="row.draft_ordered_qty" class="qty-draft"> +{{ fmtQty(row.draft_ordered_qty) }} draft</span>
																		</td>
																		<td class="qty-col" :title="lineReceiveTitle(row)">{{ fmtQty(row.received_qty) }}</td>
																		<td>
																			<span class="status-badge" :class="getStatusColorClass(row.receive_status)" :title="lineReceiveTitle(row)">{{ row.receive_status }}</span>
																		</td>
																		<td>
																			<div class="doc-chain-container">
																				<div v-if="row.mr" class="doc-chain-step">
																					<span class="text-muted" style="margin-right:4px;">MR:</span>
																					<span class="doc-link" @click="openDoc('Material Request', row.mr)" v-html="highlight(row.mr, globalSearchTerm)"></span>
																					<!-- THIS line's ordering status, not the request header's.
																					     The header status was painted here on every child row, so
																					     a fully ordered line inside a partially ordered request
																					     read "Partially Ordered". The request's own status is on
																					     the document header above, where it belongs. -->
																					<span class="status-badge" :class="getStatusColorClass(row.order_status)" :title="lineOrderTitle(row)">{{ row.order_status }}</span>
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
		// WI-066: Purchase Order create belongs to the "PO Creator" role. frappe.new_doc
		// performs no permission check, so without this the form opens fully editable and
		// only fails at save — losing however many line items were typed. Refuse up front.
		if (!frappe.model.can_create("Purchase Order")) {
			frappe.msgprint({
				title: __("PO Creator role required"),
				indicator: "orange",
				message: __(
					"Creating a Purchase Order needs the PO Creator role. Raise a Material Request against this project instead, and a PO Creator will convert it."
				),
			});
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
