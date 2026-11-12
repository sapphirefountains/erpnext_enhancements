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
 * The Procurement Tracker's Vue app (v1.811.0): two groupings of one job's purchasing, with
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
function mountProcurementTracker(frm, el, pt, data, keep, saved) {
	const project = frm.doc.name;
	const groups = data.groups || [];
	// DocType groups collapsed by default: every group the first time, and on a later refresh any
	// group that was not there before (the job's first Purchase Receipt, say), which would
	// otherwise open on its own.
	if (!keep.collapsedGroups) keep.collapsedGroups = {};
	groups.forEach((g) => {
		if (!(g.doctype in keep.collapsedGroups)) keep.collapsedGroups[g.doctype] = true;
	});
	// What the person may see of prices (v1.814.0). The server sends this key only to someone who may
	// read Purchase Orders, and puts no price on any row for anyone else; the columns follow it.
	const prices = data.prices || null;
	const esc = frappe.utils.escape_html;
	let address_timer = null;
	let memory_timer = null;
	// SAVED VIEWS (v1.816.0). `saved` is get_views' answer ({ catalog, mine, user }), or null when it could not
	// be fetched: the tracker then draws from the two standard views and offers no saving.
	const savedOk = !!(saved && saved.catalog && !saved.unavailable);
	const viewList = savedOk ? pt.merge_views(saved.catalog, saved.mine) : pt.fallback_views();
	// What this browser remembers of the last layouts, per person. Storage can be missing or refuse
	// (a private window, blocked site data): everything works without it, only the memory is lost.
	const memoryKey = pt.storage_key(frappe.session.user);
	let memory = null;
	try {
		memory = pt.parse_remembered(window.localStorage.getItem(memoryKey));
	} catch (e) {
		memory = null;
	}
	const start = pt.resolve_start({
		search: window.location.search,
		views: viewList,
		kept: keep.layout || null,
		memory: memory,
		prices: !!prices,
		// Without the saved views, a view that is not in the list is not known to be gone: its id is kept.
		views_ok: savedOk,
	});
	// The view, column and filter panels close on a click elsewhere and on Escape.
	let vm = null;
	const closeMenus = () => {
		if (vm && vm.menu) vm.menu = "";
	};
	const closeMenusOnEscape = (e) => {
		if (e.key === "Escape") closeMenus();
	};
	const flushMemory = () => {
		clearTimeout(memory_timer);
		memory_timer = null;
		try {
			window.localStorage.setItem(memoryKey, pt.remembered_text(memory));
		} catch (e) {
			// No storage: the layout just is not remembered.
		}
	};

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
				// { currency, spent, ... } for a person who may see prices, else null.
				prices: prices,
				// { view, location, status, supplier, action, noprice, q }: the grouping, the toolbar filters and the
				// search, from the address when it names them, else from the layout the browser remembered.
				view: start.view,
				// { id, columns, sort, rules }: the saved view in use and what has been changed on top of it.
				layout: start.layout,
				views: viewList,
				viewsOk: savedOk,
				notes: start.notes.slice(),
				menu: "",
				busy: false,
				collapsedGroups: keep.collapsedGroups,
				// Individual documents collapsed by default (keyed "DocType::name").
				collapsedDocs: keep.collapsedDocs,
				// Sort state per document, same key shape. Per-document rather than global:
				// expansion already works that way, two documents in one group can want
				// different sorts, and a global sort would silently reorder collapsed tables
				// nobody asked about.
				sortByDoc: keep.sortByDoc,
			};
		},
		// A write to the address that is still waiting belongs to this app. Once the app is gone
		// (the form refreshed and mounted a new one) it must not fire: it would put the OLD view
		// back into the address a quarter of a second after the new app read it. The browser's memory
		// is the opposite: it is what the person last did, so a write still waiting is made now.
		beforeUnmount() {
			clearTimeout(address_timer);
			address_timer = null;
			if (memory_timer) flushMemory();
			document.removeEventListener("click", closeMenus);
			document.removeEventListener("keydown", closeMenusOnEscape);
		},
		mounted() {
			vm = this;
			document.addEventListener("click", closeMenus);
			document.addEventListener("keydown", closeMenusOnEscape);
		},
		created() {
			// frappe copied the address's query into route_options; ours come back out.
			frappe.route_options = pt.without_own_keys(frappe.route_options);
			// A layout the browser remembered is not in the address yet: write it, so a copied link names it.
			this.writeAddress();
			if (this.narrowed) this.expandMatches();
		},
		watch: {
			view: {
				deep: true,
				handler() {
					this.writeAddress();
					this.persist();
					if (this.narrowed) this.expandMatches();
				},
			},
			layout: {
				deep: true,
				handler() {
					this.writeAddress();
					this.persist();
				},
			},
			// Only a change to the column filters opens the folders that hold a match: moving a column or changing
			// the sort must not reopen documents the person closed.
			"layout.rules": {
				deep: true,
				handler() {
					if (this.narrowed) this.expandMatches();
				},
			},
		},
		computed: {
			// Headers are rendered from the same registry the comparator reads, so a column
			// cannot exist in one and not the other.
			hasPrices() {
				return !!this.prices;
			},
			currency() {
				return (this.prices && this.prices.currency) || "";
			},
			mode() {
				return this.view.view;
			},
			// The saved view in use, whether it is the person's own or one of the built-in ones.
			currentView() {
				const known = this.views.find((v) => v.id === this.layout.id);
				if (known) return known;
				const standard = pt.standard_view(this.mode);
				// Without the saved views the id of one is kept (it is remembered and written to the address), and
				// shown under a name that says it is not loaded.
				return this.viewsOk ? standard : Object.assign(standard, { name: "Saved view (not loaded)" });
			},
			isOwn() {
				return this.viewsOk && !this.currentView.preset;
			},
			changed() {
				return pt.view_changed(this.view, this.layout, this.currentView.layout, this.mode, this.hasPrices);
			},
			listedViews() {
				return pt.views_for(this.views);
			},
			// The columns on screen, in the view's order, and the ones the person may not see.
			shown() {
				return pt.shown_columns(this.layout.columns, this.mode, this.hasPrices);
			},
			cols() {
				return this.shown.columns;
			},
			withheldText() {
				const labels = this.shown.withheld.concat(this.ruleSet.withheld.filter((l) => !this.shown.withheld.includes(l)));
				return [pt.withheld_text(labels), pt.noprice_note(this.currentView.layout.filters, this.hasPrices)].filter(Boolean).join(" ");
			},
			// The column filters that apply to this person.
			ruleSet() {
				return pt.usable_rules(this.layout.rules, this.mode, this.hasPrices);
			},
			filterCtx() {
				return { rules: this.ruleSet.active, mode: this.mode, prices: this.hasPrices };
			},
			showDocPrice() {
				return this.hasPrices && this.cols.some((c) => c.key === "rate" || c.key === "amount");
			},
			// The total under the by-item table, over the items it is showing: it follows every filter.
			// Only when the Spent column is on screen.
			spentSummary() {
				return this.hasPrices && this.spentIndex >= 0 ? pt.spent_summary(this.filteredItems, this.currency) : null;
			},
			// The Spent column is not the first money column: the total sits under it.
			spentIndex() {
				return this.cols.findIndex((c) => c.key === "spent");
			},
			filteredGroups() {
				return pt.filter_groups(this.groups, this.view, this.filterCtx);
			},
			// Filters first, then the sort: the order a person expects.
			filteredItems() {
				return pt.sort_rows(pt.filter_items(this.items, this.view, this.filterCtx), pt.ITEM_COLUMNS, this.layout.sort);
			},
			narrowed() {
				return pt.is_narrowed(this.view) || this.filterCtx.rules.length > 0;
			},
			byItem() {
				return this.view.view === pt.VIEW_ITEMS;
			},
			totalLines() {
				return pt.count_lines(this.groups);
			},
			// "Late · Received is at least 2 (hiding 12 of 40 items)": what is filtering and what it hides.
			filterBar() {
				const shown = this.byItem ? this.filteredItems.length : pt.count_lines(this.filteredGroups);
				const total = this.byItem ? this.items.length : this.totalLines;
				return pt.filter_bar_text(this.view, this.filterCtx.rules, this.mode, this.options, shown, total, this.byItem ? "items" : "lines");
			},
			summaryText() {
				if (this.byItem) {
					const base = this.narrowed
						? `${this.filteredItems.length} of ${this.items.length} items`
						: `${this.items.length} items`;
					return this.spentSummary ? `${base} · spent ${this.spentSummary.text}` : base;
				}
				const total = this.totalLines;
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
			// The column picker: the columns on screen first, in order, then the rest.
			pickerRows() {
				const registry = pt.registry_for(this.mode);
				const on = this.layout.columns.filter((k) => registry[k]);
				const rows = on.concat(Object.keys(registry).filter((k) => !on.includes(k)));
				return rows.map((key) => ({
					key: key,
					label: registry[key].label,
					shown: on.includes(key),
					locked: !!registry[key].price && !this.hasPrices,
					first: on.indexOf(key) === 0,
					last: on.indexOf(key) === on.length - 1,
				}));
			},
			sortableShown() {
				return this.cols.filter((c) => c.sortable);
			},
			// The columns a column filter can be put on: the ones on screen that can be filtered, and any
			// column an existing filter already names.
			ruleColumns() {
				const registry = pt.registry_for(this.mode);
				const keys = this.cols.map((c) => c.key).filter((k) => registry[k] && registry[k].filter);
				this.layout.rules.forEach((r) => {
					if (registry[r.column] && !keys.includes(r.column)) keys.push(r.column);
				});
				return keys.map((k) => ({ key: k, label: registry[k].label }));
			},
		},
		methods: {
			withCurrent(list, value) {
				const out = (list || []).slice();
				if (value && !out.some((o) => o.value === value)) out.push({ value: value, label: value });
				return out;
			},
			setView(name) {
				if (name === this.view.view) return;
				// To the other grouping: the layout last used there (or its standard view). Every toolbar filter and the
				// search come along as they stand, an unticked one included (pt.switch_grouping).
				const next = pt.switch_grouping({
					views: this.views,
					views_ok: this.viewsOk,
					memory: memory,
					view: this.view,
					name: name,
					prices: this.hasPrices,
				});
				this.notes = [];
				this.menu = "";
				this.view = next.view;
				this.layout = next.layout;
			},
			// Every filter and the search, on the toolbar and on columns. The columns stay as they are.
			clearFilters() {
				this.view = pt.cleared(this.view);
				this.layout.rules = [];
			},
			findView(id) {
				return this.views.find((v) => v.id === id) || null;
			},
			toggleMenu(name) {
				this.menu = this.menu === name ? "" : name;
			},
			// Open one of the saved views: its columns, sort and filters, replacing whatever was on screen.
			pickView(id) {
				const v = this.findView(id);
				if (!v) return;
				const next = pt.state_from_layout(v.layout, v.mode, { id: v.id, q: this.view.q, prices: this.hasPrices });
				this.notes = v.repaired ? [`"${v.name}" was out of date and has been reset where it no longer fit.`] : [];
				// A sort a person chose on one document's table belongs to that screen, not to the next view.
				Object.keys(this.sortByDoc).forEach((key) => delete this.sortByDoc[key]);
				this.menu = "";
				this.view = next.view;
				this.layout = next.layout;
			},
			// What the person has on screen, as a view's layout.
			currentLayout() {
				return pt.layout_of(this.view, this.layout);
			},
			askName(title, initial, then) {
				frappe.prompt(
					[{ fieldname: "view_name", label: __("Name"), fieldtype: "Data", reqd: 1, default: initial }],
					(values) => then(values.view_name),
					title,
					__("Save")
				);
			},
			// One call to the saved-views endpoints; the person's message from the server is shown by frappe.
			callViews(method, args, done) {
				this.busy = true;
				// frappe never calls `error` for a query timeout or a deadlock (it only shows a dialog), so a call that
				// never answers must not leave Save, Rename and Delete disabled until the page is reloaded.
				const release = setTimeout(() => {
					this.busy = false;
				}, 30000);
				const finish = () => {
					clearTimeout(release);
					this.busy = false;
				};
				frappe.call({
					method: "erpnext_enhancements.procurement_views." + method,
					type: "POST",
					args: args,
					callback: (r) => {
						finish();
						if (r && r.message) done(r.message);
					},
					error: finish,
				});
			},
			// A view the server returned goes into the list (replacing the one it changed) and is the one in use.
			takeView(saved_view) {
				const view = Object.assign({}, saved_view, { preset: false });
				const at = this.views.findIndex((v) => v.id === view.id);
				if (at >= 0) this.views.splice(at, 1, view);
				else this.views.push(view);
				this.layout.id = view.id;
				this.menu = "";
			},
			saveChanges() {
				const v = this.currentView;
				if (!this.isOwn || this.busy) return;
				this.callViews(
					"save_view",
					{ view_name: v.name, mode: v.mode, layout: JSON.stringify(this.currentLayout()), name: v.id },
					(saved_view) => {
						this.takeView(saved_view);
						frappe.show_alert({ message: __("View saved"), indicator: "green" });
					}
				);
			},
			// A copy under a new name: how a built-in view is changed, and a way to branch one of your own.
			saveAsNew() {
				if (!this.viewsOk || this.busy) return;
				const v = this.currentView;
				this.askName(__("Save as a new view"), v.preset ? "" : v.name + " copy", (name) => {
					this.callViews(
						"save_view",
						{ view_name: name, mode: this.mode, layout: JSON.stringify(this.currentLayout()) },
						(saved_view) => {
							this.takeView(saved_view);
							frappe.show_alert({ message: __("View saved"), indicator: "green" });
						}
					);
				});
			},
			renameView() {
				const v = this.currentView;
				if (!this.isOwn || this.busy) return;
				this.askName(__("Rename view"), v.name, (name) => {
					this.callViews("rename_view", { name: v.id, view_name: name }, (saved_view) => {
						// The layout on screen is not touched: only the name.
						const at = this.views.findIndex((x) => x.id === v.id);
						if (at >= 0) this.views.splice(at, 1, Object.assign({}, this.views[at], { name: saved_view.name }));
						this.menu = "";
					});
				});
			},
			deleteView() {
				const v = this.currentView;
				if (!this.isOwn || this.busy) return;
				// frappe.confirm shows its message as HTML, and a view's name is whatever its owner typed.
				frappe.confirm(__("Delete the view {0}? This cannot be undone.", [esc(v.name)]), () => {
					this.callViews("delete_view", { name: v.id }, () => {
						const at = this.views.findIndex((x) => x.id === v.id);
						if (at >= 0) this.views.splice(at, 1);
						this.pickView(pt.STANDARD_VIEW_IDS[this.mode]);
					});
				});
			},
			// Back to the view as it is saved: the changes made on top of it are dropped.
			resetView() {
				this.pickView(this.currentView.id);
			},
			toggleColumn(key, show) {
				this.layout.columns = pt.with_column(this.layout.columns, key, show, this.mode);
				this.layout.sort = pt.fix_sort(this.layout.sort, this.layout.columns, this.mode);
			},
			moveColumn(key, delta) {
				this.layout.columns = pt.move_column(this.layout.columns, key, delta);
			},
			setSortKey(key) {
				this.layout.sort = key ? { key: key, dir: (this.layout.sort && this.layout.sort.dir) || "asc" } : null;
			},
			setSortDir(dir) {
				if (this.layout.sort) this.layout.sort = { key: this.layout.sort.key, dir: dir };
			},
			// Column filters. A half-made one filters nothing and is not saved.
			addRule() {
				if (this.layout.rules.length >= pt.LIMITS.max_rules) return;
				const first = this.ruleColumns[0];
				const rule = first && pt.new_rule(first.key, this.mode);
				if (rule) this.layout.rules.push(rule);
			},
			removeRule(i) {
				this.layout.rules.splice(i, 1);
			},
			ruleColumnChanged(i) {
				const rule = pt.new_rule(this.layout.rules[i].column, this.mode);
				if (rule) this.layout.rules.splice(i, 1, rule);
			},
			ruleOpChanged(i) {
				const rule = this.layout.rules[i];
				this.layout.rules.splice(i, 1, pt.rule_with_op(rule, rule.op));
			},
			ruleKind(rule) {
				const col = pt.registry_for(this.mode)[rule.column];
				return col ? col.filter : "text";
			},
			ruleOps(rule) {
				return pt.OPERATORS[this.ruleKind(rule)] || [];
			},
			opLabel(op) {
				return pt.OPERATOR_LABELS[op] || op;
			},
			ruleChoices(rule) {
				const col = pt.registry_for(this.mode)[rule.column];
				return col ? pt.choice_options(col, this.options) : [];
			},
			ruleHasChoice(rule, value) {
				return (rule.values || []).includes(value);
			},
			toggleRuleChoice(rule, value, on) {
				const values = (rule.values || []).filter((v) => v !== value);
				if (on) values.push(value);
				rule.values = values;
			},
			columnLabel(key) {
				const col = pt.registry_for(this.mode)[key];
				return col ? col.label : key;
			},
			modeLabel(mode) {
				return mode === pt.VIEW_ITEMS ? "By item" : "By document";
			},
			noPriceCount(row) {
				return (Number(row.no_price_lines) || 0) + (Number(row.draft_no_price_lines) || 0);
			},
			// The browser's memory of the layout in use (and, with it, what is kept across a refresh of the form).
			persist() {
				keep.layout = { mode: this.view.view, id: this.layout.id, layout: pt.layout_of(this.view, this.layout) };
				memory = pt.remember(memory, this.view, this.layout);
				clearTimeout(memory_timer);
				memory_timer = setTimeout(flushMemory, 300);
			},
			// The address follows the view, a moment later: typing in the search box must not
			// write the address on every key (Safari refuses more than 100 history writes in
			// 30 seconds). Only while this project's form is the page on screen.
			writeAddress() {
				clearTimeout(address_timer);
				address_timer = setTimeout(() => {
					address_timer = null;
					// Belt and braces with beforeUnmount: only the app the form is showing writes.
					if (frm.__pt_app && frm.__pt_app !== app) return;
					const route = frappe.get_route() || [];
					if (route[0] !== "Form" || route[1] !== "Project" || route[2] !== project) return;
					// The view the layout started as: what it says is filtering, and what a cleared filter must say is OFF.
					const base = this.currentView.layout.filters;
					const next = pt.write_view_query(window.location.search, this.view, this.layout.id, base, this.layout.rules);
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
			// Supplier Returns (v1.807.0): send something that was received back to the supplier.
			// On a submitted receipt that is not itself a return and has not been returned in full,
			// for somebody who may create a Supplier Return. The launcher
			// (public/js/supplier_return_start.js, loaded with this form) opens an UNSAVED Supplier
			// Return through the mapper: nothing is saved, emailed or moved until the person does
			// that on its form.
			canReturn(doctype, doc) {
				if (doctype !== "Purchase Receipt" || doc.docstatus !== 1) return false;
				if (["Return", "Return Issued", "Closed"].includes(doc.status)) return false;
				const launcher = (window.erpnext_enhancements || {}).supplier_return;
				return !!(launcher && launcher.can_start());
			},
			returnToSupplier(receiptName, itemCode) {
				const launcher = (window.erpnext_enhancements || {}).supplier_return;
				if (!launcher) return;
				launcher.start_from_receipt(receiptName, itemCode ? { item_code: itemCode } : {});
			},
			// One line's own Return link: the row names a submitted receipt it came in on.
			canReturnLine(row) {
				if (!row.pr || !row.item_code || !(Number(row.received_qty) > 0)) return false;
				if (["Draft", "Cancelled", "Return", "Return Issued"].includes(row.pr_status)) return false;
				const launcher = (window.erpnext_enhancements || {}).supplier_return;
				return !!(launcher && launcher.can_start());
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
						filters: pt.describe_filters(this.view, this.options, this.filterCtx.rules, this.mode),
						printed: frappe.datetime.str_to_user(frappe.datetime.get_today()),
						prices: this.hasPrices,
						currency: this.currency,
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
			// A document's table is sorted as the person last clicked it (and "no sort" is a choice they can make),
			// else by the view's own sort.
			sortFor(doctype, name) {
				const dk = this.docKey(doctype, name);
				return dk in this.sortByDoc ? this.sortByDoc[dk] : this.layout.sort;
			},
			toggleSort(doctype, name, key) {
				const dk = this.docKey(doctype, name);
				this.sortByDoc[dk] = pt.next_sort(this.sortFor(doctype, name), key, pt.LINE_COLUMNS);
			},
			// The by-item table's sort is the view's: changing it is a change to the view.
			toggleItemSort(key) {
				this.layout.sort = pt.next_sort(this.layout.sort, key, pt.ITEM_COLUMNS);
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
				// The document's type goes along: what a price cell holds depends on it.
				return pt.sort_rows(doc.items || [], pt.LINE_COLUMNS, this.sortFor(doctype, doc.name), doctype);
			},
			// What things cost (v1.814.0). Money is formatted by pt.fmt_money, never as a quantity.
			money(value) {
				return pt.fmt_money(value, this.currency);
			},
			// The tooltip on a Spent cell: what Spent is and is not.
			spentNote() {
				return pt.SPENT_NOTE;
			},
			priceCells(row, doctype) {
				return pt.price_cells(row, doctype);
			},
			priceText(cells, field) {
				return pt.price_text(cells, field, this.currency);
			},
			priceTitle(cells) {
				return pt.price_title(cells, this.currency);
			},
			docPrice(doctype, doc) {
				return pt.doc_price_text(doc.price_total, doctype, this.currency);
			},
			itemPrice(row) {
				return pt.item_price_view(row, this.currency);
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

						<!-- SAVED VIEWS (v1.816.0): the layout in use, a menu of the built-in views and the person's own,
						     a column picker and the column filters. Each opens a panel under its button; Escape or a click
						     elsewhere closes it. -->
						<div class="pt-menu-wrap">
							<button type="button" class="btn btn-default btn-sm pt-view-btn" aria-haspopup="true" :aria-expanded="String(menu === 'views')" @click.stop="toggleMenu('views')">
								View: <b>{{ currentView.name }}</b><span v-if="changed" class="pt-changed"> · changed</span> <span aria-hidden="true">&#9662;</span>
							</button>
							<div v-if="menu === 'views'" class="pt-menu" role="menu" aria-label="Views" @click.stop>
								<div class="pt-menu-head">Built-in views</div>
								<button v-for="v in listedViews.presets" :key="v.id" type="button" role="menuitemradio" :aria-checked="String(v.id === layout.id)"
									class="pt-menu-item" :class="{ current: v.id === layout.id }" :title="v.description || ''" @click="pickView(v.id)">
									<span>{{ v.name }}</span><small>{{ modeLabel(v.mode) }}</small>
								</button>
								<div class="pt-menu-head">My views</div>
								<p v-if="!viewsOk" class="pt-menu-note">Your saved views could not be loaded just now. Reload the page to try again.</p>
								<p v-else-if="!listedViews.mine.length" class="pt-menu-note">You have none yet. Change the columns or filters, then choose Save as a new view. Your views are private to you; only an administrator can see them in the system.</p>
								<button v-for="v in listedViews.mine" :key="v.id" type="button" role="menuitemradio" :aria-checked="String(v.id === layout.id)"
									class="pt-menu-item" :class="{ current: v.id === layout.id }" @click="pickView(v.id)">
									<span>{{ v.name }}</span><small>{{ modeLabel(v.mode) }}</small>
								</button>
								<div class="pt-menu-actions">
									<button v-if="isOwn && changed" type="button" class="btn btn-primary btn-xs" :disabled="busy" @click="saveChanges">Save changes</button>
									<button v-if="viewsOk" type="button" class="btn btn-default btn-xs" :disabled="busy" @click="saveAsNew">Save as a new view</button>
									<button v-if="changed" type="button" class="btn btn-default btn-xs" @click="resetView">Undo changes</button>
									<button v-if="isOwn" type="button" class="btn btn-default btn-xs" :disabled="busy" @click="renameView">Rename</button>
									<button v-if="isOwn" type="button" class="btn btn-default btn-xs" :disabled="busy" @click="deleteView">Delete</button>
								</div>
								<p v-if="currentView.preset" class="pt-menu-note">A built-in view cannot be changed. Choose Save as a new view to keep your own version.</p>
							</div>
						</div>

						<div class="pt-menu-wrap">
							<button type="button" class="btn btn-default btn-sm" aria-haspopup="true" :aria-expanded="String(menu === 'columns')" @click.stop="toggleMenu('columns')">
								Columns <span class="pt-count">{{ cols.length }}</span>
							</button>
							<div v-if="menu === 'columns'" class="pt-menu pt-menu--wide" role="dialog" aria-label="Choose columns" @click.stop>
								<div class="pt-menu-head">Columns</div>
								<ul class="pt-colist">
									<li v-for="c in pickerRows" :key="c.key" :class="{ off: !c.shown }">
										<label :title="c.locked ? 'Prices are for people who may read Purchase Orders' : ''">
											<input type="checkbox" :checked="c.shown" :disabled="c.locked || (c.shown && cols.length <= 1 && !shown.withheld.length)" @change="toggleColumn(c.key, $event.target.checked)">
											{{ c.label }}<small v-if="c.locked" class="pt-where-note"> not shown to you</small>
										</label>
										<span v-if="c.shown" class="pt-colmove">
											<button type="button" class="btn btn-default btn-xs" :disabled="c.first" :aria-label="'Move ' + c.label + ' earlier'" @click="moveColumn(c.key, -1)">&uarr;</button>
											<button type="button" class="btn btn-default btn-xs" :disabled="c.last" :aria-label="'Move ' + c.label + ' later'" @click="moveColumn(c.key, 1)">&darr;</button>
										</span>
									</li>
								</ul>
								<div class="pt-sortrow">
									<label>Sort by
										<select class="form-control input-xs" :value="layout.sort ? layout.sort.key : ''" @change="setSortKey($event.target.value)">
											<option value="">Not sorted</option>
											<option v-for="c in sortableShown" :key="c.key" :value="c.key">{{ c.label }}</option>
										</select>
									</label>
									<select v-if="layout.sort" class="form-control input-xs" :value="layout.sort.dir" aria-label="Sort direction" @change="setSortDir($event.target.value)">
										<option value="asc">Lowest first</option>
										<option value="desc">Highest first</option>
									</select>
								</div>
							</div>
						</div>

						<div class="pt-menu-wrap">
							<button type="button" class="btn btn-default btn-sm" aria-haspopup="true" :aria-expanded="String(menu === 'rules')" @click.stop="toggleMenu('rules')">
								Column filters <span v-if="layout.rules.length" class="pt-count">{{ layout.rules.length }}</span>
							</button>
							<div v-if="menu === 'rules'" class="pt-menu pt-menu--wide" role="dialog" aria-label="Filter on a column" @click.stop>
								<div class="pt-menu-head">Show only rows that match all of these</div>
								<p v-if="!layout.rules.length" class="pt-menu-note">No column filters. Add one to filter on any column that is shown.</p>
								<div v-for="(rule, i) in layout.rules" :key="i" class="pt-rule">
									<select class="form-control input-xs" v-model="rule.column" aria-label="Column" @change="ruleColumnChanged(i)">
										<option v-for="c in ruleColumns" :key="c.key" :value="c.key">{{ c.label }}</option>
									</select>
									<select class="form-control input-xs" v-model="rule.op" aria-label="Test" @change="ruleOpChanged(i)">
										<option v-for="op in ruleOps(rule)" :key="op" :value="op">{{ opLabel(op) }}</option>
									</select>
									<input v-if="rule.op === 'contains'" type="text" class="form-control input-xs" v-model="rule.value" maxlength="120" aria-label="Text to look for" placeholder="Text">
									<span v-else-if="rule.op === 'between'" class="pt-rule-range">
										<input :type="ruleKind(rule) === 'date' ? 'date' : 'number'" step="any" class="form-control input-xs" v-model="rule.min" :aria-label="ruleKind(rule) === 'date' ? 'From' : 'At least'" :placeholder="ruleKind(rule) === 'date' ? 'From' : 'At least'">
										<span>to</span>
										<input :type="ruleKind(rule) === 'date' ? 'date' : 'number'" step="any" class="form-control input-xs" v-model="rule.max" :aria-label="ruleKind(rule) === 'date' ? 'Until' : 'At most'" :placeholder="ruleKind(rule) === 'date' ? 'Until' : 'At most'">
									</span>
									<span v-else-if="rule.op === 'any_of'" class="pt-rule-choices">
										<label v-for="o in ruleChoices(rule)" :key="o.value"><input type="checkbox" :checked="ruleHasChoice(rule, o.value)" @change="toggleRuleChoice(rule, o.value, $event.target.checked)"> {{ o.label }}</label>
										<small v-if="!ruleChoices(rule).length" class="pt-where-note">Nothing on this job to choose from.</small>
									</span>
									<button type="button" class="btn btn-default btn-xs" :aria-label="'Remove the ' + columnLabel(rule.column) + ' filter'" @click="removeRule(i)">Remove</button>
								</div>
								<div class="pt-menu-actions">
									<button type="button" class="btn btn-default btn-xs" :disabled="!ruleColumns.length || layout.rules.length >= 12" @click="addRule">Add a column filter</button>
								</div>
							</div>
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
						<!-- Only for a person who is shown prices: the lines purchasing has to put a price on. -->
						<label v-if="hasPrices" class="pt-check" title="Order lines whose rate is 0 (not the free ones)"><input type="checkbox" v-model="view.noprice"> No price only</label>
						<button v-if="byItem" type="button" class="btn-print pt-print-list" @click="printList"
								title="Print the items on screen, with where each one is now">Print list</button>
						<span class="pt-summary" aria-live="polite">{{ summaryText }}</span>
					</div>
				</div>

				<!-- What is filtering and how many rows it hides, with the one control that clears them all. -->
				<div v-if="filterBar" class="pt-filterbar" role="status">
					<span class="pt-filterbar-text">Filtered: {{ filterBar }}</span>
					<button type="button" class="btn btn-default btn-xs pt-clear" @click="clearFilters">Clear filters</button>
				</div>
				<!-- Why a column or a filter of the view is missing: the person may not see it. -->
				<div v-if="withheldText" class="pt-note" role="status">{{ withheldText }}</div>
				<div v-for="n in notes" :key="n" class="pt-note" role="status">{{ n }}</div>

				<div v-if="!located" class="pt-warning" role="status">
					The stock locations could not be read just now, so "Where it is now" is blank. Quantities and statuses are right. Reload the page to try again.
				</div>

				<!-- BY ITEM: one row per item for the job. The columns are the view's, in its order. -->
				<div v-if="byItem">
					<div v-if="filteredItems.length === 0" class="text-center text-muted pt-empty">
						{{ narrowed ? 'No item matches these filters.' : 'No procurement records found.' }}
					</div>
					<div v-else class="table-responsive">
						<table class="glass-table pt-table pt-items-table">
							<thead>
								<tr>
									<template v-for="col in cols" :key="col.key">
										<th v-if="col.sortable"
											class="sortable" :class="[col.cls, { sorted: !!(layout.sort && layout.sort.key === col.key) }]"
											role="button" tabindex="0" :aria-sort="sortAria(layout.sort, col.key)" :title="'Sort by ' + col.label + (col.hint ? '. ' + col.hint : '')"
											@click="toggleItemSort(col.key)"
											@keydown.enter.prevent="toggleItemSort(col.key)"
											@keydown.space.prevent="toggleItemSort(col.key)">{{ col.label }}<span class="sort-indicator">{{ sortIndicator(layout.sort, col.key) }}</span></th>
										<th v-else :class="col.cls">{{ col.label }}</th>
									</template>
								</tr>
							</thead>
							<tbody>
								<tr v-for="row in filteredItems" :key="row.row_id" class="procurement-item-row">
									<template v-for="col in cols" :key="col.key">
									<td v-if="col.key === 'item'" data-label="Item" class="pt-item" :title="itemTitle(row)">
									<span class="pt-item-code" v-html="hl(row.item_code)"></span>
									<span class="pt-item-name" v-html="hl(row.item_name)"></span>
								</td>
									<td v-else-if="col.key === 'requested'" data-label="Requested" class="qty-col">{{ fmtQty(row.requested_qty) }}</td>
									<td v-else-if="col.key === 'ordered'" data-label="Ordered" class="qty-col">
									{{ fmtQty(row.ordered_qty) }}<span v-if="row.draft_ordered_qty" class="qty-draft"> +{{ fmtQty(row.draft_ordered_qty) }} draft</span>
								</td>
									<td v-else-if="col.key === 'received'" data-label="Received" class="qty-col">{{ fmtQty(row.received_qty) }}</td>
									<td v-else-if="col.key === 'to_order'" data-label="Still to order" class="qty-col">{{ fmtQty(row.to_order_qty) }}</td>
									<td v-else-if="col.key === 'waiting'" data-label="Still to arrive" class="qty-col">{{ fmtQty(row.waiting_qty) }}</td>
									<!-- One price when the lines agree, "low - high" when they differ. "No price" is a
									     line with a rate of 0, never a $0.00. -->
									<td v-else-if="col.key === 'unit_price'" data-label="Unit price" class="money-col" :title="'Per ' + (row.stock_uom || 'unit') + ', over the order lines that have a price'"><div class="pt-money-cell">
										<span :class="{ 'pt-no-price': !(row.spent_lines > 0) && itemPrice(row).unit }">{{ itemPrice(row).unit }}</span>
										<small v-for="n in itemPrice(row).unit_notes" :key="n" class="pt-where-note">{{ n }}</small>
									</div></td>
									<td v-else-if="col.key === 'spent'" data-label="Spent" class="money-col" :title="spentNote"><div class="pt-money-cell">
										<span :class="{ 'pt-no-price': !(row.spent_lines > 0) && itemPrice(row).spent }">{{ itemPrice(row).spent }}</span>
										<small v-for="n in itemPrice(row).spent_notes" :key="n" class="pt-where-note">{{ n }}</small>
									</div></td>
									<td v-else-if="col.key === 'received_value'" data-label="Received value" class="money-col" title="What has arrived, at the order lines' rates. ERPNext holds no bills, so this is not what was billed or paid.">{{ row.received_value > 0 ? money(row.received_value) : '-' }}</td>
									<td v-else-if="col.key === 'no_price'" data-label="Lines with no price" class="qty-col" title="Order lines of this item whose rate is 0 (not the free ones)"><span :class="{ 'pt-no-price': noPriceCount(row) > 0 }">{{ noPriceCount(row) || '-' }}</span></td>
									<td v-else-if="col.key === 'status'" data-label="Status" class="pt-status">
									<span v-for="s in row.states" :key="s" class="es-badge pt-pill" :data-theme="stateTheme(s)">{{ stateLabel(s) }}</span>
								</td>
									<template v-else-if="col.key === 'where'">${WHERE_CELL}</template>
									<td v-else-if="col.key === 'due'" data-label="Due" class="pt-due">{{ row.expected_date ? formatDate(row.expected_date) : '-' }}</td>
									<td v-else-if="col.key === 'supplier'" data-label="Supplier" class="pt-supplier">
									<span v-for="s in row.suppliers" :key="s" class="pt-supplier-name" v-html="hl(s)"></span>
									<span v-if="!(row.suppliers || []).length">-</span>
								</td>
									<td v-else-if="col.key === 'documents'" data-label="Documents" class="pt-docs">
									<div v-for="(names, doctype) in row.documents" :key="doctype" class="doc-chain-step">
										<span class="text-muted">{{ chainLabel(doctype) }}:</span>
										<span v-for="name in names" :key="name" class="doc-link" @click="openDoc(doctype, name)" v-html="hl(name)"></span>
									</div>
								</td>
									<td v-else-if="col.key === 'received_at'" data-label="Received at" class="pt-received-at">{{ receivedAt(row) || '-' }}</td>
									<td v-else-if="col.key === 'unit'" data-label="Unit">{{ row.stock_uom || '-' }}</td>
									<td v-else-if="col.key === 'lines'" data-label="Lines on this job" class="qty-col">{{ row.line_count }}</td>
									</template>
								</tr>
							</tbody>
							<!-- The total follows the filters: it is over the items shown, and sits under the Spent column when that is on screen. -->
							<tfoot v-if="spentSummary">
								<tr class="pt-total">
									<td v-if="spentIndex > 0" :colspan="spentIndex" data-label="Total">Total spent on these items <small class="pt-where-note">Ordered, not billed or paid</small></td>
									<td class="money-col" data-label="Spent"><div class="pt-money-cell">{{ spentSummary.text }}
										<small v-for="n in spentSummary.notes" :key="n" class="pt-where-note">{{ n }}</small>
									</div></td>
									<td v-if="cols.length - spentIndex - 1 > 0" :colspan="cols.length - spentIndex - 1"></td>
								</tr>
							</tfoot>
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
									<!-- This document's total for the job's lines. Only for a person who is shown prices. -->
									<span v-if="showDocPrice && docPrice(group.doctype, doc).text" class="doc-meta doc-price" :title="docPrice(group.doctype, doc).title">{{ docPrice(group.doctype, doc).text }}</span>
									<span class="doc-meta doc-itemcount">{{ itemCountText(doc) }}</span>
									<!-- @click.stop is load-bearing: .doc-header's own click toggles
									     expansion, so without it every Receive click also collapses
									     the row the user was reading. -->
									<button v-if="canReceive(group.doctype, doc)" type="button" class="btn-receive"
											@click.stop="receiveAgainst(doc.name)"
											:title="'Create a Purchase Receipt against ' + doc.name">Receive</button>
									<!-- Supplier Returns (v1.807.0). @click.stop for the same reason as Receive. -->
									<button v-if="canReturn(group.doctype, doc)" type="button" class="btn-receive"
											@click.stop="returnToSupplier(doc.name)"
											:title="'Start a Supplier Return for items on ' + doc.name">Return</button>
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
												<template v-for="col in cols" :key="col.key">
													<th v-if="col.sortable"
														class="sortable"
														:class="[col.cls, { sorted: !!(sortFor(group.doctype, doc.name) && sortFor(group.doctype, doc.name).key === col.key) }]"
														role="button" tabindex="0"
														:aria-sort="sortAria(sortFor(group.doctype, doc.name), col.key)"
														:title="'Sort by ' + col.label + (col.hint ? '. ' + col.hint : '')"
														@click="toggleSort(group.doctype, doc.name, col.key)"
														@keydown.enter.prevent="toggleSort(group.doctype, doc.name, col.key)"
														@keydown.space.prevent="toggleSort(group.doctype, doc.name, col.key)">{{ col.label }}<span class="sort-indicator">{{ sortIndicator(sortFor(group.doctype, doc.name), col.key) }}</span></th>
													<!-- Doc Chain is not sortable: seven chain nodes in one cell, no single value to order by. -->
													<th v-else :class="col.cls">{{ col.label }}</th>
												</template>
												</tr>
											</thead>
											<tbody>
												<tr v-if="(doc.items || []).length === 0">
													<td :colspan="cols.length" class="text-center text-muted">No items.</td>
												</tr>
												<tr v-for="row in displayItems(group.doctype, doc)" :key="row.row_id" class="procurement-item-row">
													<template v-for="col in cols" :key="col.key">
													<td v-if="col.key === 'item'" data-label="Item" class="pt-item"
													@click="row.source_doc_type && row.source_doc_name && openDoc(row.source_doc_type, row.source_doc_name)"
													:class="{ 'doc-link': row.source_doc_type && row.source_doc_name }">
													<span class="pt-item-code" v-html="hl(row.item_code)"></span>
													<span class="pt-item-name" v-html="hl(row.item_name)"></span>
												</td>
													<template v-else-if="col.key === 'where'">${WHERE_CELL}</template>
													<!-- "-", not 0, when nothing was requested: a direct Purchase Order line has no request behind it. -->
													<td v-else-if="col.key === 'requested'" data-label="Requested" class="qty-col" :title="uomTitle(row)">{{ fmtQty(row.requested_qty) }}</td>
													<td v-else-if="col.key === 'ordered'" data-label="Ordered" class="qty-col" :title="lineOrderTitle(row)">
													{{ fmtQty(row.ordered_qty) }}<span v-if="row.draft_ordered_qty" class="qty-draft"> +{{ fmtQty(row.draft_ordered_qty) }} draft</span>
												</td>
													<td v-else-if="col.key === 'received'" data-label="Received" class="qty-col" :title="lineReceiveTitle(row)">{{ fmtQty(row.received_qty) }}</td>
													<td v-else-if="col.key === 'to_order'" data-label="Still to order" class="qty-col">{{ fmtQty(row.to_order_qty) }}</td>
													<td v-else-if="col.key === 'waiting'" data-label="Still to arrive" class="qty-col">{{ fmtQty(row.waiting_qty) }}</td>
													<!-- Prices, by the kind of document: an order's own line, a receipt at the ORDER's rate, a quote on its own. A request has no price: its cells stay empty. -->
													<td v-else-if="col.key === 'rate'" data-label="Rate" class="money-col"><div class="pt-money-cell">
														<span :class="{ 'pt-no-price': priceCells(row, group.doctype) && priceCells(row, group.doctype).state !== 'priced' }">{{ priceText(priceCells(row, group.doctype), 'rate') }}</span>
														<small v-if="priceCells(row, group.doctype) && priceCells(row, group.doctype).kind === 'quote'" class="pt-where-note">{{ priceCells(row, group.doctype).draft ? 'draft quote' : 'quote' }}</small>
														<small v-if="priceCells(row, group.doctype) && priceCells(row, group.doctype).kind === 'receipt' && priceCells(row, group.doctype).draft" class="pt-where-note">draft receipt</small>
													</div></td>
													<td v-else-if="col.key === 'amount'" data-label="Amount" class="money-col" :title="priceTitle(priceCells(row, group.doctype))"><div class="pt-money-cell">
														<span v-if="priceCells(row, group.doctype) && priceCells(row, group.doctype).state === 'priced'">{{ priceText(priceCells(row, group.doctype), 'amount') }}</span>
														<small v-if="priceCells(row, group.doctype) && priceCells(row, group.doctype).kind === 'order' && priceCells(row, group.doctype).received > 0 && priceCells(row, group.doctype).received !== priceCells(row, group.doctype).amount" class="pt-where-note">received {{ money(priceCells(row, group.doctype).received) }}</small>
														<small v-if="priceCells(row, group.doctype) && priceCells(row, group.doctype).kind === 'quote'" class="pt-where-note">{{ priceCells(row, group.doctype).draft ? 'draft quote, not spent' : 'quoted, not spent' }}</small>
													</div></td>
													<td v-else-if="col.key === 'status'" data-label="Status" class="pt-status">
													<!-- THIS line's own state, from its own quantities; never the
													     document's label (the v1.194.0 defect). -->
													<span v-for="s in row.line_states" :key="s" class="es-badge pt-pill" :data-theme="stateTheme(s)" :title="lineReceiveTitle(row)">{{ stateLabel(s) }}</span>
													<small v-if="dueText(row)" class="pt-where-note">{{ dueText(row) }}</small>
												</td>
													<td v-else-if="col.key === 'due'" data-label="Due" class="pt-due">{{ row.expected_date ? formatDate(row.expected_date) : '-' }}</td>
													<td v-else-if="col.key === 'supplier'" data-label="Supplier" class="pt-supplier"><span v-if="row.supplier" class="pt-supplier-name" v-html="hl(row.supplier)"></span><span v-else>-</span></td>
													<td v-else-if="col.key === 'received_at'" data-label="Received at" class="pt-received-at">{{ receivedAt(row) || '-' }}</td>
													<td v-else-if="col.key === 'unit'" data-label="Unit">{{ row.stock_uom || '-' }}</td>
													<td v-else-if="col.key === 'doc_chain'" data-label="Doc Chain">
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
															<!-- Supplier Returns (v1.807.0): this line's own Return, on the receipt it came in on. -->
															<span v-if="canReturnLine(row)" class="doc-link" role="button" tabindex="0"
																:title="'Return ' + row.item_code + ' on ' + row.pr + ' to the supplier'"
																@click="returnToSupplier(row.pr, row.item_code)"
																@keydown.enter.prevent="returnToSupplier(row.pr, row.item_code)">Return</span>
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
													</template>
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
				frm.__pt_keep = { project: project, collapsedGroups: null, collapsedDocs: {}, sortByDoc: {}, layout: null };
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

			// The person's saved views (v1.816.0), fetched beside the feed. The tracker must never depend on them: this
			// settles on the answer, on an error, on the promise frappe.call returns, or after SAVED_VIEWS_WAIT_MS,
			// whichever comes first (frappe never calls `error` for a query timeout or a deadlock, so on its own a
			// slow call would leave the tracker on "Loading..." with the feed already in hand). With nothing, the
			// tracker draws from the standard views and says its saved views could not be loaded.
			const SAVED_VIEWS_WAIT_MS = 4000;
			const saved = new Promise((resolve) => {
				let settled = false;
				const settle = (value) => {
					if (settled) return;
					settled = true;
					clearTimeout(timer);
					resolve(value);
				};
				const timer = setTimeout(() => settle(null), SAVED_VIEWS_WAIT_MS);
				try {
					const call = frappe.call({
						method: "erpnext_enhancements.procurement_views.get_views",
						callback: (r) => settle((r && r.message) || null),
						error: () => settle(null),
						silent: true,
					});
					if (call && typeof call.then === "function") call.then(null, () => settle(null));
				} catch (e) {
					settle(null);
				}
			});

			Promise.all([logic, feed, saved]).then(
				([, r, views]) => {
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
						frm.__pt_app = mountProcurementTracker(frm, mount, pt, data, frm.__pt_keep, views);
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
