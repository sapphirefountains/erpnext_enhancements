/**
 * Project form script — Comments App + Procurement Tracker.
 *
 * Targets: the "Project" doctype form.
 * Loaded via: hooks.py `doctype_js["Project"]` (with vue.global.js + comments.js;
 *   one of several Project form scripts — see project.js).
 *
 * On saved Projects: mounts the custom Comments App into `custom_comments_field`
 * (see comments.js) and renders a Vue 3 "Procurement Tracker" into
 * `custom_material_request_feed`. The tracker fetches the full procurement chain
 * (erpnext_enhancements.project_enhancements.get_procurement_overview) and shows it two
 * ways: by document (a collapsible DocType -> document -> item tree with MR/RFQ/SQ/PO/PR/
 * PI/Stock-Entry status badges) and by item (one row per item for the job). Each line says
 * where its item is now, and both views filter by place, status, supplier and "needs
 * action"; the view and its filters live in the address. Also wires the `custom_btn_*`
 * buttons that create project-linked procurement docs. Tracker styling lives in
 * desk_enhancements.bundle.css (`.procurement-tracker`, `.sapphire-theme`).
 */
/**
 * The tracker's sorting, filters and address handling are not in this file: they are pure, and
 * live in public/js/procurement_tracker/logic.js (procurement_tracker.bundle.js,
 * window.sf_procurement_tracker), where scripts/test_procurement_tracker.mjs runs them under
 * node. What stays here needs Vue and frappe: the template, the clicks and the print dialog.
 */

/**
 * Printing from the tracker: a whole DocType group as one PDF (All, or Open where the
 * doctype has an "open"), or one document in Frappe's own print view.
 *
 * The group PDF is Frappe's multi-document print, the one behind the list view's
 * Actions -> Print, reached through erpnext_enhancements.procurement_print only so the file
 * is named for the job. Declared out here, outside the Vue app, to keep the Vue
 * options object about the tracker rather than about a dialog.
 */

// Frappe's list view forces background printing above this many documents, and for the
// same reason: a synchronous render of that many PDFs is a request long enough to meet
// the worker timeout. No project on production has more than 23 of any one doctype today.
const PROCUREMENT_BACKGROUND_PRINT_THRESHOLD = 25;

function procurementPrintSettings() {
	return frappe.model.get_doc(":Print Settings", "Print Settings") || {};
}

/**
 * Why Frappe will refuse to print this document, or null if it will print.
 *
 * Asked here, before the request, because the multi-document PDF never says. printview
 * throws on a draft or cancelled document the Print Settings forbid, and
 * download_multi_pdf catches that and moves on — so the document is simply missing from
 * the PDF, with no message, no Error Log, and a page count nobody checks. On production
 * today drafts may be printed and cancelled documents may not, and a cancelled Purchase
 * Receipt or Invoice can still reach the tracker through the chain join.
 */
function procurementPrintBlocker(doc) {
	const settings = procurementPrintSettings();
	if (doc.docstatus === 0 && !cint(settings.allow_print_for_draft)) {
		return __("draft, and Print Settings do not allow printing drafts");
	}
	if (doc.docstatus === 2 && !cint(settings.allow_print_for_cancelled)) {
		return __("cancelled, and Print Settings do not allow printing cancelled documents");
	}
	return null;
}

/**
 * The format the dialog starts on.
 *
 * The doctype's configured default when there is one — and since v1.519.0 all six
 * procurement doctypes have one, the "<Doctype> - Sapphire" format, set by a Property
 * Setter fixture. The rest is the fallback for a site where that has not synced: Frappe
 * would start on "Standard", but where the site has built exactly one format of its own
 * for the doctype, that is the one it prints. Two home-built formats is a choice for a
 * person, so that falls back to Standard too.
 */
function procurementDefaultPrintFormat(doctype, formats) {
	const configured = frappe.get_meta(doctype).default_print_format;
	if (configured && formats.includes(configured)) return configured;
	const own = formats.filter((name) => {
		const pf = locals[":Print Format"] && locals[":Print Format"][name];
		return pf && pf.standard === "No";
	});
	return own.length === 1 ? own[0] : "Standard";
}

/**
 * Frappe's print-view rule for which PDF backend renders a format (print.js,
 * get_pdf_generator): the format's own, else Print Settings'. It matters for "Standard",
 * which has no Print Format record to carry a backend — left alone it renders on
 * wkhtmltopdf while the same document's PDF button in the print view uses chrome.
 */
function procurementPdfGenerator(format) {
	const pf = locals[":Print Format"] && locals[":Print Format"][format];
	return (pf && pf.pdf_generator) || procurementPrintSettings().pdf_generator || "wkhtmltopdf";
}

function procurementPrintInBackground(doctype, names, format, letterhead) {
	// Frappe's own background route, glued exactly as its list view glues it. It keeps
	// Frappe's filename and cannot be handed a PDF backend (a queued job has no request to
	// read one from), which is the price of not reimplementing it for a size no job has
	// reached yet.
	frappe
		.call("frappe.utils.print_format.download_multi_pdf_async", {
			doctype: doctype,
			name: JSON.stringify(names),
			format: format,
			no_letterhead: letterhead ? "0" : "1",
			letterhead: letterhead || "",
		})
		.then((r) => {
			const task_id = r.message && r.message.task_id;
			if (!task_id) return;
			frappe.show_alert({ message: __("Building the PDF in the background..."), indicator: "blue" });
			frappe.realtime.task_subscribe(task_id);
			frappe.realtime.on(`task_complete:${task_id}`, (data) => {
				frappe.msgprint({
					title: __("PDF ready"),
					message: __("The PDF of {0} documents is ready to download.", [names.length]),
					primary_action: {
						label: __("Download PDF"),
						client_action: "window.open",
						args: data.file_url,
					},
				});
				frappe.realtime.task_unsubscribe(task_id);
				frappe.realtime.off(`task_complete:${task_id}`);
			});
		});
}

function openProcurementPrintDialog(project, group) {
	const doctype = group.doctype;
	// Loads the doctype's meta and, with it, its enabled print formats into locals. The
	// Project form has neither for a Purchase Order until something asks.
	frappe.model.with_doctype(doctype, () => {
		const formats = frappe.meta.get_print_formats(doctype);
		const documents = group.documents || [];
		const open = documents.filter((d) => d.is_open === true);

		const scopes = [{ value: "all", label: __("All ({0})", [documents.length]) }];
		if (group.open_rule) {
			scopes.push({ value: "open", label: __("Open ({0})", [open.length]) });
		}

		const pick = (scope) => {
			const printable = [];
			const skipped = [];
			(scope === "open" ? open : documents).forEach((d) => {
				const why = procurementPrintBlocker(d);
				if (why) skipped.push({ name: d.name, why: why });
				else printable.push(d.name);
			});
			return { printable: printable, skipped: skipped };
		};

		// Declared before it is built: a field's onchange can fire while its default is
		// being set, inside the constructor, and a `const` read there is a ReferenceError.
		let dialog = null;
		dialog = new frappe.ui.Dialog({
			title: __("Print {0}", [__(doctype)]),
			fields: [
				{
					fieldtype: "Select",
					fieldname: "scope",
					label: __("Documents"),
					options: scopes,
					default: "all",
					onchange: () => render_summary(),
				},
				{ fieldtype: "HTML", fieldname: "summary" },
				{
					fieldtype: "Select",
					fieldname: "print_format",
					label: __("Print Format"),
					options: formats,
					default: procurementDefaultPrintFormat(doctype, formats),
				},
				{
					fieldtype: "Link",
					fieldname: "letterhead",
					label: __("Letter Head"),
					options: "Letter Head",
					description: __("Leave blank to print without one."),
				},
			],
			primary_action_label: __("Print"),
			primary_action(values) {
				const scope = values.scope || "all";
				const printable = pick(scope).printable;
				if (!printable.length) {
					frappe.msgprint(__("Nothing to print."));
					return;
				}
				const format = values.print_format || "Standard";
				const letterhead = values.letterhead || "";

				if (printable.length > PROCUREMENT_BACKGROUND_PRINT_THRESHOLD) {
					procurementPrintInBackground(doctype, printable, format, letterhead);
				} else {
					const params = new URLSearchParams({
						project: project,
						doctype: doctype,
						name: JSON.stringify(printable),
						scope: scope,
						format: format,
						no_letterhead: letterhead ? "0" : "1",
						pdf_generator: procurementPdfGenerator(format),
					});
					if (letterhead) params.set("letterhead", letterhead);
					const w = window.open(
						"/api/method/erpnext_enhancements.procurement_print.download_procurement_pdf?" +
							params.toString()
					);
					if (!w) frappe.msgprint(__("Please enable pop-ups"));
				}
				dialog.hide();
			},
		});

		// Says exactly what will be on the paper before anyone presses Print: which
		// documents, in the tracker's newest-first order, and which were left out and why.
		// The PDF itself cannot say that, so this is the only place it can be said.
		function render_summary() {
			if (!dialog) return;
			const scope = dialog.get_value("scope") || "all";
			const { printable, skipped } = pick(scope);
			const esc = frappe.utils.escape_html;
			const parts = [];
			if (scope === "open") {
				parts.push(`<p class="text-muted small">${esc(__("Open means: {0}", [group.open_rule]))}</p>`);
			}
			if (printable.length) {
				parts.push(
					`<p class="small">${esc(
						__("{0} to print, newest first: {1}", [printable.length, printable.join(", ")])
					)}</p>`
				);
			} else {
				parts.push(`<p class="small">${esc(__("Nothing to print."))}</p>`);
			}
			if (skipped.length) {
				const list = skipped.map((s) => `<li>${esc(s.name)}: ${esc(s.why)}</li>`).join("");
				parts.push(`<p class="small text-warning">${esc(__("Left out:"))}</p><ul class="small">${list}</ul>`);
			}
			if (printable.length > PROCUREMENT_BACKGROUND_PRINT_THRESHOLD) {
				parts.push(
					`<p class="text-muted small">${esc(
						__("More than {0} documents are printed in the background; a download link appears when the PDF is ready.", [
							PROCUREMENT_BACKGROUND_PRINT_THRESHOLD,
						])
					)}</p>`
				);
			}
			dialog.fields_dict.summary.$wrapper.html(parts.join(""));
		}

		dialog.show();
		render_summary();

		// Same default as the print view's: the enabled default Letter Head.
		frappe.db
			.get_value("Letter Head", { disabled: 0, is_default: 1 }, "name")
			.then(({ message }) => {
				if (message && message.name && !dialog.get_value("letterhead")) {
					dialog.set_value("letterhead", message.name);
				}
			});
	});
}

/**
 * The Procurement Tracker's Vue app (v1.806.0): two groupings of one job's purchasing, with
 * filters that live in the address.
 *
 * `pt` is window.sf_procurement_tracker (public/js/procurement_tracker/logic.js): every decision
 * that can be tested without a browser is there, and scripts/test_procurement_tracker.mjs tests
 * it. This function is the part that needs Vue and frappe: the template, the clicks, and the
 * address.
 *
 * THE ADDRESS. The view and the filters are written into the address's query
 * (?pt_view=items&pt_where=project_shelf) with history.replaceState, so they survive a reload,
 * come back with Back and Forward, and travel in a shared link, without a filter ever becoming
 * a history entry of its own. frappe's router is not involved: it reads the query into
 * frappe.route_options on every route change, and the widget takes its own keys back out so the
 * next list view is not asked to filter on them.
 *
 * `keep` holds what is expanded and how each table is sorted, on the form object, so a refresh
 * of the form (a save, a reload of the document) does not fold the tree shut again.
 */
function mountProcurementTracker(frm, el, pt, data, keep) {
	const project = frm.doc.name;
	const groups = data.groups || [];
	if (!keep.collapsedGroups) {
		// DocType groups collapsed by default.
		keep.collapsedGroups = groups.reduce((acc, g) => {
			acc[g.doctype] = true;
			return acc;
		}, {});
	}
	const columns = (registry) =>
		Object.keys(registry).map((key) => ({ key: key, label: registry[key].label, cls: registry[key].cls || "" }));
	const esc = frappe.utils.escape_html;
	let address_timer = null;

	// The where-now cell, shared by both tables. Plain interpolation, so every value is escaped.
	const WHERE_CELL = `
		<td data-label="Where it is now" class="pt-where">
			<template v-if="(row.where_now || []).length">
				<span v-for="p in row.where_now" :key="p.kind + '|' + (p.warehouse || '')"
					class="pt-place" :class="'pt-place--' + placeTone(p.kind)" :title="placeTitle(p)">{{ p.text }}</span>
			</template>
			<span v-else class="pt-where-none" :class="{ 'pt-where-untracked': row.location_tracked === false }">{{ row.where_now_text || '-' }}</span>
			<small v-for="r in (row.supplier_returns || [])" :key="r.supplier_return" class="pt-where-note">
				<span class="doc-link" @click.stop="openDoc('Supplier Return', r.supplier_return)">{{ r.supplier_return }}</span>: {{ fmtQty(r.qty) }} {{ returnWords(r) }}
			</small>
			<small v-if="sharedNote(row)" class="pt-where-note">{{ sharedNote(row) }}</small>
			<small v-if="receivedAt(row)" class="pt-where-note">Received at {{ receivedAt(row) }}</small>
		</td>`;

	const app = window.Vue.createApp({
		data() {
			return {
				groups: groups, // [{ doctype, documents: [{ name, date, supplier, status, items: [...] }] }]
				items: data.items || [], // one row per item for the project
				options: data.filters || {},
				located: data.located !== false,
				// { view, location, status, supplier, action, q }: what the address says.
				view: pt.read_query(window.location.search),
				collapsedGroups: keep.collapsedGroups,
				// Individual documents collapsed by default (keyed "DocType::name").
				collapsedDocs: keep.collapsedDocs,
				// Sort state per document, same key shape. Per-document rather than global:
				// expansion already works that way, two documents in one group can want
				// different sorts, and a global sort would silently reorder collapsed tables
				// nobody asked about.
				sortByDoc: keep.sortByDoc,
				itemSort: keep.itemSort,
			};
		},
		created() {
			// frappe copied the address's query into route_options; ours come back out.
			frappe.route_options = pt.without_own_keys(frappe.route_options);
			if (pt.is_narrowed(this.view)) this.expandMatches();
		},
		watch: {
			view: {
				deep: true,
				handler() {
					this.writeAddress();
					if (pt.is_narrowed(this.view)) this.expandMatches();
				},
			},
		},
		computed: {
			// Headers are rendered from the same registry the comparator reads, so a column
			// cannot exist in one and not the other.
			lineColumns() {
				return columns(pt.LINE_COLUMNS);
			},
			itemColumns() {
				return columns(pt.ITEM_COLUMNS);
			},
			filteredGroups() {
				return pt.filter_groups(this.groups, this.view);
			},
			// Filters first, then the sort: the order a person expects.
			filteredItems() {
				return pt.sort_rows(pt.filter_items(this.items, this.view), pt.ITEM_COLUMNS, this.itemSort);
			},
			narrowed() {
				return pt.is_narrowed(this.view);
			},
			byItem() {
				return this.view.view === pt.VIEW_ITEMS;
			},
			summaryText() {
				if (this.byItem) {
					return this.narrowed
						? `${this.filteredItems.length} of ${this.items.length} items`
						: `${this.items.length} items`;
				}
				const total = pt.count_lines(this.groups);
				return this.narrowed ? `${pt.count_lines(this.filteredGroups)} of ${total} lines` : `${total} lines`;
			},
			// A filter the address names stays in its list even when this job has no line for it,
			// so the box shows what is filtering instead of going blank.
			locationOptions() {
				return this.withCurrent(this.options.locations, this.view.location);
			},
			statusOptions() {
				return this.withCurrent(this.options.states, this.view.status);
			},
			supplierOptions() {
				const list = (this.options.suppliers || []).slice();
				if (this.view.supplier && !list.includes(this.view.supplier)) list.push(this.view.supplier);
				return list;
			},
		},
		methods: {
			withCurrent(list, value) {
				const out = (list || []).slice();
				if (value && !out.some((o) => o.value === value)) out.push({ value: value, label: value });
				return out;
			},
			setView(name) {
				this.view.view = name;
			},
			clearFilters() {
				this.view = pt.cleared(this.view);
			},
			// The address follows the view, a moment later: typing in the search box must not
			// write the address on every key (Safari refuses more than 100 history writes in
			// 30 seconds). Only while this project's form is the page on screen.
			writeAddress() {
				clearTimeout(address_timer);
				address_timer = setTimeout(() => {
					const route = frappe.get_route() || [];
					if (route[0] !== "Form" || route[1] !== "Project" || route[2] !== project) return;
					const next = pt.write_query(window.location.search, this.view);
					if (next === window.location.search) return;
					try {
						window.history.replaceState(
							window.history.state,
							"",
							window.location.pathname + next + window.location.hash
						);
					} catch (e) {
						// The view still works; only the address is a step behind.
					}
				}, 250);
			},
			// Open every group and document that holds a match, so a filter never answers with a
			// row of closed folders.
			expandMatches() {
				this.filteredGroups.forEach((group) => {
					this.collapsedGroups[group.doctype] = false;
					group.documents.forEach((doc) => {
						this.collapsedDocs[this.docKey(group.doctype, doc.name)] = false;
					});
				});
			},
			docKey(doctype, name) {
				return doctype + "::" + name;
			},
			toggleGroup(doctype) {
				this.collapsedGroups[doctype] = !this.collapsedGroups[doctype];
			},
			toggleDoc(doctype, name) {
				const key = this.docKey(doctype, name);
				// Default (key absent) is collapsed; store false to expand, true to collapse.
				this.collapsedDocs[key] = this.collapsedDocs[key] === false;
			},
			isDocCollapsed(doctype, name) {
				// Default (key absent) is collapsed.
				return this.collapsedDocs[this.docKey(doctype, name)] !== false;
			},
			openDoc(doctype, name) {
				if (doctype && name) frappe.set_route("Form", doctype, name);
			},
			formatDate(d) {
				return d ? frappe.datetime.str_to_user(d) : "-";
			},
			// The text with the search words marked, as HTML. Every piece is escaped: item
			// names, suppliers and document names are data, never markup.
			hl(value) {
				return pt.highlight_html(value, this.view.q, esc);
			},
			itemCountText(doc) {
				return pt.item_count_text(doc);
			},
			stateTheme(state) {
				return pt.state_theme(state);
			},
			stateLabel(state) {
				return pt.STATE_LABELS[state] || state;
			},
			statusTheme(status) {
				return pt.status_theme(status);
			},
			placeTone(kind) {
				return pt.place_tone(kind);
			},
			placeTitle(place) {
				if (place.kind === "unrecorded") {
					return "Received for this job, and no record says where it is: it was received before this item kept stock, or it was moved or counted with no job on the entry.";
				}
				if (place.kind === "taken") return "Taken out of stock with this job on the entry.";
				if (place.kind === "returned") return "Sent back on a purchase return.";
				return place.warehouse || "";
			},
			sharedNote(row) {
				return pt.shared_where_note(row);
			},
			returnWords(r) {
				return pt.return_words(r);
			},
			receivedAt(row) {
				const at = row.received_at;
				return Array.isArray(at) ? at.join(", ") : at || "";
			},
			dueText(row) {
				if (!row.expected_date || !row.needs_action) return "";
				return "Due " + frappe.datetime.str_to_user(row.expected_date);
			},
			// Only where receiving actually makes sense. The rule is the same one
			// api/pickup_routing.py settled on: the number, not the label. "Closed" can hide an
			// order whose goods never turned up, and "To Bill" means they are already here.
			//
			// Hidden rather than disabled for a user without create permission: frappe.new_doc
			// and the mapper both perform no permission check, so a visible button would open a
			// form and only fail at save, losing whatever was typed. Same reasoning as the PO
			// Creator gate on the + Purchase Order button.
			canReceive(doctype, doc) {
				if (doctype !== "Purchase Order") return false;
				if (doc.docstatus !== 1) return false;
				if (["Closed", "Delivered"].includes(doc.status)) return false;
				if (Number(doc.per_received || 0) >= 100) return false;
				return frappe.model.can_create("Purchase Receipt");
			},
			receiveAgainst(poName) {
				// ERPNext's own mapper. It carries supplier, items, warehouse and only the
				// outstanding quantity, and it lands on an UNSAVED draft. Nothing here submits:
				// a Purchase Receipt is a stock transaction, so submitting writes Stock Ledger
				// and GL entries, and cancelling one later is an accounting event rather than
				// an undo.
				//
				// `frm` is deliberately not passed. open_mapped_doc falls back to frm.doc.name
				// for the source, and the source here is a Purchase Order while the open form
				// is the Project.
				frappe.model.open_mapped_doc({
					method: "erpnext.buying.doctype.purchase_order.purchase_order.make_purchase_receipt",
					source_name: poName,
					freeze_message: __("Building the receipt..."),
				});
			},
			// Hidden, not disabled, without print permission on the doctype: the same test
			// Frappe's own form uses for its print icon.
			canPrintDoctype(doctype) {
				return frappe.model.can_print(doctype);
			},
			canPrintDoc(doctype, doc) {
				return frappe.model.can_print(doctype) && !procurementPrintBlocker(doc);
			},
			printGroup(doctype) {
				// From `groups`, not `filteredGroups`: the header being clicked may be showing
				// a narrowed subset, and "All" on paper has to mean all of them.
				const group = this.groups.find((g) => g.doctype === doctype);
				if (group) openProcurementPrintDialog(frm.doc.name, group);
			},
			printDoc(doctype, name) {
				// Frappe's print view, in a new tab: format, letter head, PDF and Print are all
				// there, and the Project form keeps whatever the user had expanded.
				const w = window.open(frappe.router.make_url(["print", doctype, name]));
				if (!w) frappe.msgprint(__("Please enable pop-ups"));
			},
			// The by-item list on paper: what is on screen, filtered and sorted as it is, with
			// where each item is now. A plain page of its own, so it prints the same anywhere.
			printList() {
				const html = pt.print_html(
					this.filteredItems,
					{
						title: __("{0} materials", [project]),
						subtitle: frm.doc.project_name || "",
						filters: pt.describe_filters(this.view, this.options),
						printed: frappe.datetime.str_to_user(frappe.datetime.get_today()),
					},
					esc
				);
				const w = window.open("", "_blank");
				if (!w) {
					frappe.msgprint(__("Please enable pop-ups"));
					return;
				}
				w.document.open();
				w.document.write(html);
				w.document.close();
				w.focus();
				setTimeout(() => w.print(), 250);
			},
			sortFor(doctype, name) {
				return this.sortByDoc[this.docKey(doctype, name)] || null;
			},
			toggleSort(doctype, name, key) {
				const dk = this.docKey(doctype, name);
				this.sortByDoc[dk] = pt.next_sort(this.sortByDoc[dk], key, pt.LINE_COLUMNS);
			},
			toggleItemSort(key) {
				this.itemSort = keep.itemSort = pt.next_sort(this.itemSort, key, pt.ITEM_COLUMNS);
			},
			sortIndicator(sort, key) {
				if (!sort || sort.key !== key) return "⇅";
				return sort.dir === "asc" ? "↑" : "↓";
			},
			sortAria(sort, key) {
				if (!sort || sort.key !== key) return "none";
				return sort.dir === "asc" ? "ascending" : "descending";
			},
			// Search and filters narrow first, then sort sorts.
			displayItems(doctype, doc) {
				return pt.sort_rows(doc.items || [], pt.LINE_COLUMNS, this.sortFor(doctype, doc.name));
			},
			fmtQty(value) {
				return pt.fmt_qty(value);
			},
			// The figures are in the stock UOM; the line may have been written in another. Say
			// so rather than leaving someone to wonder why a 120 FT line reads 120 against a
			// stock UOM of Unit.
			uomTitle(row) {
				if (!row.stock_uom) return "";
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
				if (row.expected_date) parts.push(`due ${frappe.datetime.str_to_user(row.expected_date)}`);
				return parts.join(" · ");
			},
			// Hidden when there is nothing to total, so the pre-order doctypes (RFQ, Supplier
			// Quotation) do not sprout a row of zeroes that reads as a failure rather than an
			// absence.
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
				return parts.join(" · ");
			},
			rollupTitle(doc) {
				const r = doc.rollup || {};
				// No unit: a document's lines can span UOMs, so a single total carries no one
				// unit. Per-line units are on the cells.
				return [`${r.item_count || 0} line(s)`, `ordered: ${r.order_status}`, `received: ${r.receive_status}`].join(" · ");
			},
			// Spells out the arithmetic behind an item row's badge, and names the parent's own
			// status alongside it: the two legitimately differ, and seeing them together is
			// what makes it obvious that the row is not just echoing its parent.
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
				return parts.join(" · ");
			},
			itemTitle(row) {
				const parts = [`${row.line_count || 0} line(s) on this job`];
				if (row.stock_uom) parts.push(`quantities in ${row.stock_uom}`);
				return parts.join(" · ");
			},
			chainLabel(doctype) {
				return (
					{
						"Material Request": "MR",
						"Request for Quotation": "RFQ",
						"Supplier Quotation": "SQ",
						"Purchase Order": "PO",
						"Purchase Receipt": "PR",
						"Purchase Invoice": "PI",
					}[doctype] || doctype
				);
			},
		},
		template: `
			<div class="procurement-tracker sapphire-theme">
				<div class="sticky-search-bar pt-toolbar">
					<input type="search" v-model="view.q" placeholder="Search items, documents and suppliers..." class="glass-input pt-search" aria-label="Search the procurement tracker">
					<div class="pt-controls">
						<div class="btn-group pt-views" role="group" aria-label="Group by">
							<button type="button" class="btn btn-default btn-sm" :class="{ active: !byItem }" :aria-pressed="String(!byItem)" @click="setView('documents')">By document</button>
							<button type="button" class="btn btn-default btn-sm" :class="{ active: byItem }" :aria-pressed="String(byItem)" @click="setView('items')">By item</button>
						</div>
						<label class="pt-filter"><span>Where</span>
							<select class="form-control input-xs" v-model="view.location">
								<option value="">Anywhere</option>
								<option v-for="o in locationOptions" :key="o.value" :value="o.value">{{ o.label }}</option>
							</select>
						</label>
						<label class="pt-filter"><span>Status</span>
							<select class="form-control input-xs" v-model="view.status">
								<option value="">Any status</option>
								<option v-for="o in statusOptions" :key="o.value" :value="o.value">{{ o.label }}</option>
							</select>
						</label>
						<label class="pt-filter"><span>Supplier</span>
							<select class="form-control input-xs" v-model="view.supplier">
								<option value="">Any supplier</option>
								<option v-for="s in supplierOptions" :key="s" :value="s">{{ s }}</option>
							</select>
						</label>
						<label class="pt-check"><input type="checkbox" v-model="view.action"> Needs action only</label>
						<button v-if="narrowed" type="button" class="btn btn-default btn-xs pt-clear" @click="clearFilters">Clear</button>
						<button v-if="byItem" type="button" class="btn-print pt-print-list" @click="printList"
								title="Print the items on screen, with where each one is now">Print list</button>
						<span class="pt-summary" aria-live="polite">{{ summaryText }}</span>
					</div>
				</div>

				<div v-if="!located" class="pt-warning" role="status">
					The stock locations could not be read just now, so "Where it is now" is blank. Quantities and statuses are right. Reload the page to try again.
				</div>

				<!-- BY ITEM: one row per item for the job -->
				<div v-if="byItem">
					<div v-if="filteredItems.length === 0" class="text-center text-muted pt-empty">
						{{ narrowed ? 'No item matches these filters.' : 'No procurement records found.' }}
					</div>
					<div v-else class="table-responsive">
						<table class="glass-table pt-table pt-items-table">
							<thead>
								<tr>
									<th v-for="col in itemColumns" :key="col.key"
										class="sortable" :class="[col.cls, { sorted: !!(itemSort && itemSort.key === col.key) }]"
										role="button" tabindex="0" :aria-sort="sortAria(itemSort, col.key)" :title="'Sort by ' + col.label"
										@click="toggleItemSort(col.key)"
										@keydown.enter.prevent="toggleItemSort(col.key)"
										@keydown.space.prevent="toggleItemSort(col.key)">{{ col.label }}<span class="sort-indicator">{{ sortIndicator(itemSort, col.key) }}</span></th>
									<th>Supplier</th>
									<th>Documents</th>
								</tr>
							</thead>
							<tbody>
								<tr v-for="row in filteredItems" :key="row.row_id" class="procurement-item-row">
									<td data-label="Item" class="pt-item" :title="itemTitle(row)">
										<span class="pt-item-code" v-html="hl(row.item_code)"></span>
										<span class="pt-item-name" v-html="hl(row.item_name)"></span>
									</td>
									<td data-label="Requested" class="qty-col">{{ fmtQty(row.requested_qty) }}</td>
									<td data-label="Ordered" class="qty-col">
										{{ fmtQty(row.ordered_qty) }}<span v-if="row.draft_ordered_qty" class="qty-draft"> +{{ fmtQty(row.draft_ordered_qty) }} draft</span>
									</td>
									<td data-label="Received" class="qty-col">{{ fmtQty(row.received_qty) }}</td>
									<td data-label="Status" class="pt-status">
										<span v-for="s in row.states" :key="s" class="es-badge pt-pill" :data-theme="stateTheme(s)">{{ stateLabel(s) }}</span>
									</td>
									${WHERE_CELL}
									<td data-label="Due" class="pt-due">{{ row.expected_date ? formatDate(row.expected_date) : '-' }}</td>
									<td data-label="Supplier" class="pt-supplier">
										<span v-for="s in row.suppliers" :key="s" class="pt-supplier-name" v-html="hl(s)"></span>
										<span v-if="!(row.suppliers || []).length">-</span>
									</td>
									<td data-label="Documents" class="pt-docs">
										<div v-for="(names, doctype) in row.documents" :key="doctype" class="doc-chain-step">
											<span class="text-muted">{{ chainLabel(doctype) }}:</span>
											<span v-for="name in names" :key="name" class="doc-link" @click="openDoc(doctype, name)" v-html="hl(name)"></span>
										</div>
									</td>
								</tr>
							</tbody>
						</table>
					</div>
				</div>

				<!-- BY DOCUMENT: DocType group, document, its lines -->
				<div v-else>
					<div v-if="filteredGroups.length === 0" class="text-center text-muted pt-empty">
						{{ narrowed ? 'No line matches these filters.' : 'No procurement records found.' }}
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
							<!-- @click.stop: the header's own click toggles the group. -->
							<button v-if="canPrintDoctype(group.doctype)" type="button" class="btn-print btn-print-group"
									@click.stop="printGroup(group.doctype)"
									:title="'Print these ' + group.doctype + ' documents as one PDF' + (group.open_rule ? ' — all, or only the open ones' : '')">Print</button>
						</div>

						<div v-if="!collapsedGroups[group.doctype]" class="group-content">
							<!-- Level 2: documents of this DocType -->
							<div v-for="doc in group.documents" :key="doc.name" class="procurement-doc">
								<div class="doc-header" @click="toggleDoc(group.doctype, doc.name)">
									<svg class="chevron doc-chevron" :class="{ 'expanded': !isDocCollapsed(group.doctype, doc.name) }" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
										<polyline points="9 18 15 12 9 6"></polyline>
									</svg>
									<span class="doc-id doc-link" @click.stop="openDoc(group.doctype, doc.name)" v-html="hl(doc.name)"></span>
									<span class="doc-meta doc-date">{{ formatDate(doc.date) }}</span>
									<span class="doc-meta doc-supplier" v-html="hl(doc.supplier || '-')"></span>
									<span class="es-badge pt-pill" :data-theme="statusTheme(doc.status)">{{ doc.status }}</span>
									<!-- One span rather than three: .doc-supplier is the only element
									     in this flex row that grows, so three nowrap spans would
									     squeeze a supplier name to nothing on a narrow screen. Hidden
									     entirely when there is nothing to total, so an RFQ header does
									     not gain a "0 req · 0 ord · 0 rec" that reads as an error. -->
									<span v-if="hasRollup(doc)" class="doc-meta doc-qty-rollup" :title="rollupTitle(doc)">{{ rollupText(doc) }}</span>
									<span class="doc-meta doc-itemcount">{{ itemCountText(doc) }}</span>
									<!-- @click.stop is load-bearing: .doc-header's own click toggles
									     expansion, so without it every Receive click also collapses
									     the row the user was reading. -->
									<button v-if="canReceive(group.doctype, doc)" type="button" class="btn-receive"
											@click.stop="receiveAgainst(doc.name)"
											:title="'Create a Purchase Receipt against ' + doc.name">Receive</button>
									<!-- Only where Frappe would actually print it; see procurementPrintBlocker. -->
									<button v-if="canPrintDoc(group.doctype, doc)" type="button" class="btn-print"
											@click.stop="printDoc(group.doctype, doc.name)"
											:title="'Open the print view for ' + doc.name + ' in a new tab'">Print</button>
								</div>

								<!-- Level 3: items inside the document -->
								<div v-if="!isDocCollapsed(group.doctype, doc.name)" class="doc-items">
									<div class="table-responsive">
										<table class="glass-table pt-table">
											<thead>
												<tr>
													<th v-for="col in lineColumns" :key="col.key"
														class="sortable"
														:class="[col.cls, { sorted: !!(sortFor(group.doctype, doc.name) && sortFor(group.doctype, doc.name).key === col.key) }]"
														role="button" tabindex="0"
														:aria-sort="sortAria(sortFor(group.doctype, doc.name), col.key)"
														:title="'Sort by ' + col.label"
														@click="toggleSort(group.doctype, doc.name, col.key)"
														@keydown.enter.prevent="toggleSort(group.doctype, doc.name, col.key)"
														@keydown.space.prevent="toggleSort(group.doctype, doc.name, col.key)">{{ col.label }}<span class="sort-indicator">{{ sortIndicator(sortFor(group.doctype, doc.name), col.key) }}</span></th>
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
													<td data-label="Item" class="pt-item"
														@click="row.source_doc_type && row.source_doc_name && openDoc(row.source_doc_type, row.source_doc_name)"
														:class="{ 'doc-link': row.source_doc_type && row.source_doc_name }">
														<span class="pt-item-code" v-html="hl(row.item_code)"></span>
														<span class="pt-item-name" v-html="hl(row.item_name)"></span>
													</td>
													${WHERE_CELL}
													<!-- "-", not 0, when nothing was requested: a direct Purchase
													     Order line has no request behind it, and "nobody asked" is
													     a different fact from "asked for none". -->
													<td data-label="Requested" class="qty-col" :title="uomTitle(row)">{{ fmtQty(row.requested_qty) }}</td>
													<td data-label="Ordered" class="qty-col" :title="lineOrderTitle(row)">
														{{ fmtQty(row.ordered_qty) }}<span v-if="row.draft_ordered_qty" class="qty-draft"> +{{ fmtQty(row.draft_ordered_qty) }} draft</span>
													</td>
													<td data-label="Received" class="qty-col" :title="lineReceiveTitle(row)">{{ fmtQty(row.received_qty) }}</td>
													<td data-label="Status" class="pt-status">
														<!-- THIS line's own state, from its own quantities; never the
														     document's label (the v1.194.0 defect). -->
														<span v-for="s in row.line_states" :key="s" class="es-badge pt-pill" :data-theme="stateTheme(s)" :title="lineReceiveTitle(row)">{{ stateLabel(s) }}</span>
														<small v-if="dueText(row)" class="pt-where-note">{{ dueText(row) }}</small>
													</td>
													<td data-label="Doc Chain">
														<div class="doc-chain-container">
															<div v-if="row.mr" class="doc-chain-step">
																<span class="text-muted">MR:</span>
																<span class="doc-link" @click="openDoc('Material Request', row.mr)" v-html="hl(row.mr)"></span>
																<!-- THIS line's ordering status, not the request header's.
																     The header status was painted here on every child row, so
																     a fully ordered line inside a partially ordered request
																     read "Partially Ordered". The request's own status is on
																     the document header above, where it belongs. -->
																<span class="es-badge pt-pill" :data-theme="statusTheme(row.order_status)" :title="lineOrderTitle(row)">{{ row.order_status }}</span>
															</div>
															<div v-if="row.rfq" class="doc-chain-step">
																<span class="text-muted">RFQ:</span>
																<span class="doc-link" @click="openDoc('Request for Quotation', row.rfq)" v-html="hl(row.rfq)"></span>
																<span class="es-badge pt-pill" :data-theme="statusTheme(row.rfq_status)">{{ row.rfq_status }}</span>
															</div>
															<div v-if="row.sq" class="doc-chain-step">
																<span class="text-muted">SQ:</span>
																<span class="doc-link" @click="openDoc('Supplier Quotation', row.sq)" v-html="hl(row.sq)"></span>
																<span class="es-badge pt-pill" :data-theme="statusTheme(row.sq_status)">{{ row.sq_status }}</span>
															</div>
															<div v-if="row.po" class="doc-chain-step">
																<span class="text-muted">PO:</span>
																<span class="doc-link" @click="openDoc('Purchase Order', row.po)" v-html="hl(row.po)"></span>
																<span class="es-badge pt-pill" :data-theme="statusTheme(row.po_status)">{{ row.po_status }}</span>
															</div>
															<div v-if="row.pr" class="doc-chain-step">
																<span class="text-muted">PR:</span>
																<span class="doc-link" @click="openDoc('Purchase Receipt', row.pr)" v-html="hl(row.pr)"></span>
																<span class="es-badge pt-pill" :data-theme="statusTheme(row.pr_status)">{{ row.pr_status }}</span>
															</div>
															<div v-if="row.pi" class="doc-chain-step">
																<span class="text-muted">PI:</span>
																<span class="doc-link" @click="openDoc('Purchase Invoice', row.pi)" v-html="hl(row.pi)"></span>
																<span class="es-badge pt-pill" :data-theme="statusTheme(row.pi_status)">{{ row.pi_status }}</span>
															</div>
															<div v-if="row.stock_entry" class="doc-chain-step">
																<span class="text-muted">SE:</span>
																<span class="doc-link" @click="openDoc('Stock Entry', row.stock_entry)" v-html="hl(row.stock_entry)"></span>
																<span class="es-badge pt-pill" :data-theme="statusTheme(row.stock_entry_status)">{{ row.stock_entry_status }}</span>
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
			</div>
		`,
	});
	app.mount(el);
	return app;
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
			if (typeof window.Vue === "undefined") {
				frappe.msgprint("Error: Vue is not defined. Please check if vue.global.js is loaded.");
				return;
			}

			if (!frm.fields_dict["custom_material_request_feed"]) {
				console.warn('Field "custom_material_request_feed" not found in Project DocType.');
				frappe.msgprint('Field "custom_material_request_feed" not found in Project DocType.');
				return;
			}

			const wrapper = frm.fields_dict["custom_material_request_feed"].wrapper;
			const project = frm.doc.name;

			// One tracker per form. `refresh` fires on every save and reload, and each used to
			// mount a new Vue app over the last one's node and leave the old one alive with its
			// watchers; now that a watcher writes the address, the old one is unmounted first.
			if (frm.__pt_app) {
				try {
					frm.__pt_app.unmount();
				} catch (e) {
					// Already gone with its node.
				}
				frm.__pt_app = null;
			}
			// A reply for an earlier refresh, or for another project the form has since moved
			// to, draws nothing.
			const seq = (frm.__pt_seq = (frm.__pt_seq || 0) + 1);
			// What is expanded and how each table is sorted, kept across refreshes of one project.
			if (!frm.__pt_keep || frm.__pt_keep.project !== project) {
				frm.__pt_keep = { project: project, collapsedGroups: null, collapsedDocs: {}, sortByDoc: {}, itemSort: null };
			}

			$(wrapper).html('<div class="procurement-tracker-mount">Loading Procurement Tracker...</div>');
			const mount = $(wrapper).find(".procurement-tracker-mount")[0];
			const failed = (message) => $(wrapper).html($('<div class="text-danger"></div>').text(message));

			// The widget's decisions (filters, sorting, the address) are a bundle of their own,
			// public/js/procurement_tracker, so node can test them. v16's frappe.require calls
			// back even when the file failed to load, so the global is checked below.
			const logic = new Promise((resolve) => frappe.require("procurement_tracker.bundle.js", resolve));
			const feed = new Promise((resolve, reject) =>
				frappe.call({
					method: "erpnext_enhancements.project_enhancements.get_procurement_overview",
					args: { project_name: project },
					callback: resolve,
					error: reject,
				})
			);

			Promise.all([logic, feed]).then(
				([, r]) => {
					if (frm.__pt_seq !== seq || frm.doc.name !== project) return;
					try {
						const pt = window.sf_procurement_tracker;
						if (!pt) {
							failed(__("The Procurement Tracker could not load its files. Reload the page to try again."));
							return;
						}
						const data = (r && r.message) || {};
						if (!(data.groups || []).length) {
							$(wrapper).html('<div class="text-center text-muted">No procurement records found.</div>');
							return;
						}
						frm.__pt_app = mountProcurementTracker(frm, mount, pt, data, frm.__pt_keep);
					} catch (e) {
						console.error(e);
						frappe.msgprint("Error inside procurement tracker callback: " + e.message);
						failed("Error: " + e.message);
					}
				},
				() => {
					if (frm.__pt_seq !== seq) return;
					frappe.msgprint("Server Error in get_procurement_overview");
					failed("Server Error");
				}
			);
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
		if (!frappe.model.can_create("Purchase Receipt")) {
			frappe.msgprint({
				title: __("Purchase Receipt permission required"),
				indicator: "orange",
				message: __("Creating a Purchase Receipt needs create permission on Purchase Receipt."),
			});
			return;
		}

		// This button used to open a blank Purchase Receipt carrying nothing but the
		// project — no supplier, no order, no lines — so the receiver retyped a delivery
		// ERPNext already knew about, and could not link it back to the order afterwards.
		// Ask which order arrived instead, then let ERPNext's own mapper fill it in.
		frappe.call({
			method: "erpnext_enhancements.procurement_project.get_receivable_purchase_orders",
			args: { project: frm.doc.name },
			freeze: true,
			callback: function (r) {
				const orders = (r && r.message) || [];

				if (!orders.length) {
					// Deliberately not falling back to the old blank form: a receipt with
					// no order behind it is the thing this replaced.
					frappe.msgprint({
						title: __("Nothing to receive"),
						indicator: "blue",
						message: __(
							"No submitted Purchase Order on this project still has goods outstanding. Raise or submit an order first."
						),
					});
					return;
				}

				const receive = (name) =>
					frappe.model.open_mapped_doc({
						method: "erpnext.buying.doctype.purchase_order.purchase_order.make_purchase_receipt",
						source_name: name,
						freeze_message: __("Building the receipt..."),
					});

				// One outstanding order is the common case on a job; asking which of one
				// is a click for nothing.
				if (orders.length === 1) {
					receive(orders[0].name);
					return;
				}

				const label = (po) => {
					const parts = [po.name];
					if (po.supplier) parts.push(po.supplier);
					const pct = Math.round(Number(po.per_received || 0));
					if (pct > 0) parts.push(__("{0}% received", [pct]));
					return parts.join(" — ");
				};

				frappe.prompt(
					[
						{
							fieldname: "purchase_order",
							label: __("Which order arrived?"),
							fieldtype: "Select",
							reqd: 1,
							options: orders.map(label),
						},
					],
					(values) => {
						const chosen = orders.find((po) => label(po) === values.purchase_order);
						if (chosen) receive(chosen.name);
					},
					__("Receive against a Purchase Order"),
					__("Continue")
				);
			},
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
