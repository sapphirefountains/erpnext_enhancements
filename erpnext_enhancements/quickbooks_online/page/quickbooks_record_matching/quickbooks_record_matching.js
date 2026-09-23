/**
 * Client controller for the "QuickBooks Record Matching" desk page.
 *
 * The accountant's review queue for the QBO -> ERPNext migration. Two tabs:
 *
 *  - Masters: every master-record Sync Mapping (Customer / Vendor / Item / Account /
 *    Class / Term / PaymentMethod / TaxCode) with what the import decided, the
 *    ERPNext records it could have picked instead, a Link picker for any other
 *    record, and three actions per row -- Link (re-point, folding away the copy the
 *    import created), Keep (stamp the row reviewed as it is) and Retry (re-sync a
 *    parked row after its cause is fixed). Tick rows to do the same to several at
 *    once: "Link selected" links each ticked row to whatever its *own* picker holds,
 *    with its own "Fill blank fields" box; "Keep selected" and "Retry selected" are
 *    the other two buttons in bulk. "Accept suggestions" links every row on the page
 *    whose best suggestion clears the threshold.
 *  - Parked transactions: Invoices, Bills, Purchases etc. that failed validation on
 *    import, with the stored reason, the draft if one exists, and Retry -- per row,
 *    for the ticked rows, or for the whole page.
 *
 * Things this script is careful about:
 *
 *  - A tick is keyed on entity type + QBO id, and every load drops the ticks of rows
 *    no longer on screen, so a bulk action only ever reaches rows the accountant can
 *    see. A reload (Refresh, a finished bulk action, a new page size) keeps the ticks,
 *    picks and "Fill blank fields" boxes of the rows that are still there; a row that
 *    links successfully forgets its own.
 *  - Choosing a record in a row's picker, or clicking one of its suggestions, ticks
 *    that row. The pre-fill does not: it goes through set_input, which never fires
 *    the picker's onchange -- and which, unlike set_value, does not validate the
 *    record with a request of its own. The server has just confirmed every
 *    suggestion exists, and a 500-row page would otherwise open with 500 requests.
 *  - Pages hold 50, 100, 200 or 500 rows (remembered per browser, per tab). The
 *    pager moves by the page length the server says it served, not the one asked
 *    for: the server clamps at matching.MAX_PAGE_LENGTH, and paging by the request
 *    would skip every row between the two.
 *  - Bulk links go to the server LINK_CHUNK at a time. A link can fold a duplicate
 *    away with rename_doc, which re-points every Link to it across the database; a
 *    few hundred of those in one request would outrun the gateway timeout, and the
 *    page would get no word on which of them had landed.
 *
 * Endpoints (erpnext_enhancements.quickbooks_online.core.api): get_match_queue,
 * decide_match, decide_matches, confirm_match, confirm_matches,
 * get_parked_transactions, sync_entity.
 * Every server-supplied string is escaped before it reaches the DOM.
 */
frappe.pages["quickbooks-record-matching"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("QuickBooks Record Matching"),
		single_column: true,
	});

	const esc = frappe.utils.escape_html;
	const API = "erpnext_enhancements.quickbooks_online.core.api.";

	// Keep in sync with MASTER_ENTITIES / TRANSACTION_ENTITIES in core/constants.py.
	const MASTER_TYPES = ["Customer", "Vendor", "Item", "Account", "TaxCode", "Class", "Term", "PaymentMethod"];
	const TXN_TYPES = [
		"Invoice",
		"SalesReceipt",
		"Estimate",
		"Bill",
		"VendorCredit",
		"Payment",
		"BillPayment",
		"Purchase",
		"Transfer",
		"CreditCardPayment",
		"JournalEntry",
		"PurchaseOrder",
		"Deposit",
		"CreditMemo",
		"RefundReceipt",
	];
	const STATUSES = [
		["Needs decision", __("Needs decision")],
		["All", __("All")],
		["Pending Review", __("Pending review")],
		["Conflict", __("Conflict")],
		["Auto Matched", __("Auto matched")],
		["Created", __("Created by import")],
		["Manual Matched", __("Decided")],
		["Unmapped", __("Unmapped payloads")],
	];
	const STATUS_COLOUR = {
		"Auto Matched": "blue",
		"Manual Matched": "green",
		"Pending Review": "orange",
		Created: "gray",
		"Not Matched": "red",
	};
	// The largest must not exceed matching.MAX_PAGE_LENGTH; tests/test_quickbooks_matching.py
	// holds it there.
	const PAGE_LENGTHS = [50, 100, 200, 500];
	// Decisions per decide_matches request -- see "Things this script is careful about".
	const LINK_CHUNK = 20;
	// Rows per confirm_matches request. A keep is one stamped row, so these can be larger.
	const KEEP_CHUNK = 100;

	function storedPageLength(tab) {
		try {
			const value = parseInt(window.localStorage.getItem(`qbo-record-matching:${tab}:page-length`), 10);
			return PAGE_LENGTHS.includes(value) ? value : PAGE_LENGTHS[0];
		} catch (e) {
			return PAGE_LENGTHS[0];
		}
	}

	function rememberPageLength(tab, value) {
		try {
			window.localStorage.setItem(`qbo-record-matching:${tab}:page-length`, String(value));
		} catch (e) {
			// Storage is blocked (a private window): the choice lasts until the page is left.
		}
	}

	const state = {
		tab: "masters",
		entity: "",
		status: "Needs decision",
		search: "",
		start: 0,
		pageLength: storedPageLength("masters"),
		served: storedPageLength("masters"),
		threshold: 90,
		rows: [],
		total: 0,
		counts: {},
		// By row key: the record the accountant chose by hand ("" = cleared it), and the
		// rows whose "Fill blank fields" box is ticked. Both outlive a reload.
		picks: {},
		fills: new Set(),
		txn: {
			entity: "",
			start: 0,
			pageLength: storedPageLength("transactions"),
			served: storedPageLength("transactions"),
			rows: [],
			total: 0,
			counts: {},
		},
	};
	const pickers = {};

	const rowKey = (row) => `${row.entity_type}:${row.qbo_id}`;
	const pageLengthOptions = PAGE_LENGTHS.map((n) => `<option value="${n}">${__("{0} per page", [n])}</option>`).join("");

	const $root = $(`
		<div class="qbo-matching">
			<div class="qbo-mt-tabs">
				<button class="btn btn-default btn-sm active" data-tab="masters">${__("Masters")}</button>
				<button class="btn btn-default btn-sm" data-tab="transactions">${__("Parked transactions")}</button>
			</div>
			<div class="qbo-mt-panel" data-panel="masters">
				<div class="qbo-mt-toolbar">
					<select class="form-control input-sm" data-filter="entity"></select>
					<select class="form-control input-sm" data-filter="status"></select>
					<input class="form-control input-sm" data-filter="search" placeholder="${__("Search ERPNext name or QBO id")}" />
					<select class="form-control input-sm qbo-mt-page-length" data-filter="page-length" title="${__("Rows per page")}">${pageLengthOptions}</select>
					<span class="qbo-mt-spacer"></span>
					<label class="qbo-mt-threshold">${__("Accept at")}
						<input type="number" class="form-control input-sm" data-filter="threshold" min="50" max="100" step="5" value="90" />%
					</label>
					<button class="btn btn-default btn-sm" data-action="accept-page">${__("Accept suggestions on this page")}</button>
					<button class="btn btn-default btn-sm" data-action="refresh">${__("Refresh")}</button>
				</div>
				<div class="qbo-mt-counts"></div>
				<div class="qbo-mt-body"></div>
				<div class="qbo-mt-selbar" data-selbar="masters" hidden>
					<span class="qbo-mt-selcount" data-selcount></span>
					<button class="btn btn-primary btn-sm" data-action="link-selected" disabled>${__("Link selected")}</button>
					<button class="btn btn-default btn-sm" data-action="keep-selected" disabled>${__("Keep selected")}</button>
					<button class="btn btn-default btn-sm" data-action="retry-selected" disabled>${__("Retry selected")}</button>
					<button class="btn btn-default btn-sm" data-action="clear-selected" disabled>${__("Clear")}</button>
				</div>
				<div class="qbo-mt-pager"></div>
			</div>
			<div class="qbo-mt-panel" data-panel="transactions" hidden>
				<div class="qbo-mt-toolbar">
					<select class="form-control input-sm" data-txn-filter="entity"></select>
					<select class="form-control input-sm qbo-mt-page-length" data-txn-filter="page-length" title="${__("Rows per page")}">${pageLengthOptions}</select>
					<span class="qbo-mt-spacer"></span>
					<button class="btn btn-default btn-sm" data-action="retry-page">${__("Retry all on this page")}</button>
					<button class="btn btn-default btn-sm" data-action="txn-refresh">${__("Refresh")}</button>
				</div>
				<div class="qbo-mt-counts" data-txn-counts></div>
				<div class="qbo-mt-body" data-txn-body></div>
				<div class="qbo-mt-selbar" data-selbar="transactions" hidden>
					<span class="qbo-mt-selcount" data-txn-selcount></span>
					<button class="btn btn-primary btn-sm" data-action="txn-retry-selected" disabled>${__("Retry selected")}</button>
					<button class="btn btn-default btn-sm" data-action="txn-clear-selected" disabled>${__("Clear")}</button>
				</div>
				<div class="qbo-mt-pager" data-txn-pager></div>
			</div>
		</div>
	`).appendTo(page.body);

	page.set_secondary_action(__("QuickBooks Dashboard"), () => frappe.set_route("quickbooks-online-dashboard"));
	page.add_action_item(__("Sync Mapping list"), () => frappe.set_route("List", "QuickBooks Sync Mapping"));

	// ---------------------------------------------------------------- selection
	/**
	 * Tick-box selection over one table: a box per row (data-pick-row = row index) and
	 * one in the header (data-pick-all). Shift-click ticks or unticks the run of rows
	 * between the last box clicked and this one.
	 */
	function makeSelection({ $panel, getRows, selectable, onChange }) {
		const keys = new Set();
		let anchor = null;
		const selection = {
			has: (row) => selectable(row) && keys.has(rowKey(row)),
			chosen: () =>
				getRows()
					.map((row, index) => ({ row, index }))
					.filter(({ row }) => selection.has(row)),
			set(index, on) {
				const row = getRows()[index];
				if (!row || !selectable(row)) {
					return;
				}
				if (on) {
					keys.add(rowKey(row));
				} else {
					keys.delete(rowKey(row));
				}
				$panel.find(`[data-pick-row='${index}']`).prop("checked", on);
				$panel.find(`tr[data-row-index='${index}']`).toggleClass("is-selected", on);
			},
			drop(key) {
				keys.delete(key);
			},
			clear() {
				getRows().forEach((row, index) => selection.set(index, false));
				keys.clear();
				selection.sync();
			},
			// After a load: forget rows that are no longer on screen.
			prune() {
				const onScreen = new Set(getRows().map(rowKey));
				[...keys].forEach((key) => {
					if (!onScreen.has(key)) {
						keys.delete(key);
					}
				});
				anchor = null;
			},
			sync() {
				const eligible = getRows().filter(selectable).length;
				const count = selection.chosen().length;
				$panel.find("[data-pick-all]").prop({
					checked: count > 0 && count === eligible,
					indeterminate: count > 0 && count < eligible,
					disabled: !eligible,
				});
				onChange();
			},
		};
		$panel.on("click", "[data-pick-row]", (event) => {
			const index = parseInt($(event.currentTarget).attr("data-pick-row"), 10);
			// A click has already flipped the box by the time the handler runs.
			const on = event.currentTarget.checked;
			if (event.shiftKey && anchor !== null && anchor !== index) {
				const [from, to] = index < anchor ? [index, anchor] : [anchor, index];
				for (let i = from; i <= to; i += 1) {
					selection.set(i, on);
				}
			} else {
				selection.set(index, on);
			}
			anchor = index;
			selection.sync();
		});
		$panel.on("click", "[data-pick-all]", (event) => {
			const on = event.currentTarget.checked;
			getRows().forEach((row, index) => selection.set(index, on));
			selection.sync();
		});
		return selection;
	}

	function canRetry(row) {
		return row.match_status === "Pending Review" && !row.unmapped;
	}

	// A row can be ticked if at least one bulk action applies: Link needs a destination
	// doctype, Keep and Retry need a mapping row.
	function canSelect(row) {
		return Boolean(row.expected_doctype) || !row.unmapped;
	}

	const masterSelection = makeSelection({
		$panel: $root.find("[data-panel='masters']"),
		getRows: () => state.rows,
		selectable: canSelect,
		onChange: renderMasterBar,
	});
	const txnSelection = makeSelection({
		$panel: $root.find("[data-panel='transactions']"),
		getRows: () => state.txn.rows,
		selectable: () => true,
		onChange: renderTxnBar,
	});

	function withCount($button, label, count) {
		$button.prop("disabled", !count).text(count ? `${label} (${count})` : label);
	}

	function renderMasterBar() {
		const chosen = masterSelection.chosen().map(({ row }) => row);
		const $bar = $root.find("[data-selbar='masters']");
		$bar.prop("hidden", !state.rows.length).toggleClass("has-selection", chosen.length > 0);
		$bar.find("[data-selcount]").text(
			chosen.length
				? __("{0} of {1} on this page selected", [chosen.length, state.rows.length])
				: __("Tick rows to link, keep or retry several at once. Shift-click ticks a range."),
		);
		withCount($bar.find("[data-action='link-selected']"), __("Link selected"), chosen.filter((row) => row.expected_doctype).length);
		withCount($bar.find("[data-action='keep-selected']"), __("Keep selected"), chosen.filter((row) => !row.unmapped).length);
		withCount($bar.find("[data-action='retry-selected']"), __("Retry selected"), chosen.filter(canRetry).length);
		$bar.find("[data-action='clear-selected']").prop("disabled", !chosen.length);
	}

	function renderTxnBar() {
		const count = txnSelection.chosen().length;
		const $bar = $root.find("[data-selbar='transactions']");
		$bar.prop("hidden", !state.txn.rows.length).toggleClass("has-selection", count > 0);
		$bar.find("[data-txn-selcount]").text(
			count
				? __("{0} of {1} on this page selected", [count, state.txn.rows.length])
				: __("Tick transactions to retry several at once. Shift-click ticks a range."),
		);
		withCount($bar.find("[data-action='txn-retry-selected']"), __("Retry selected"), count);
		$bar.find("[data-action='txn-clear-selected']").prop("disabled", !count);
	}

	// A successful link is finished business: its pick, fill box and tick go.
	function forget(key) {
		delete state.picks[key];
		state.fills.delete(key);
		masterSelection.drop(key);
	}

	// ------------------------------------------------------------------ filters
	const $entity = $root.find("[data-filter='entity']");
	$entity.append(`<option value="">${__("All master types")}</option>`);
	MASTER_TYPES.forEach((type) => $entity.append(`<option value="${type}">${type}</option>`));
	const $status = $root.find("[data-filter='status']");
	STATUSES.forEach(([value, label]) => $status.append(`<option value="${value}">${label}</option>`));
	$status.val(state.status);
	const $txnEntity = $root.find("[data-txn-filter='entity']");
	$txnEntity.append(`<option value="">${__("All transaction types")}</option>`);
	TXN_TYPES.forEach((type) => $txnEntity.append(`<option value="${type}">${type}</option>`));
	const $pageLength = $root.find("[data-filter='page-length']").val(String(state.pageLength));
	const $txnPageLength = $root.find("[data-txn-filter='page-length']").val(String(state.txn.pageLength));

	$root.on("click", "[data-tab]", (event) => {
		const tab = $(event.currentTarget).attr("data-tab");
		state.tab = tab;
		$root.find("[data-tab]").removeClass("active");
		$(event.currentTarget).addClass("active");
		$root.find("[data-panel]").attr("hidden", true);
		$root.find(`[data-panel='${tab}']`).removeAttr("hidden");
		if (tab === "transactions" && !state.txn.rows.length) {
			loadTransactions();
		}
	});
	$entity.on("change", () => {
		state.entity = $entity.val();
		state.start = 0;
		load();
	});
	$status.on("change", () => {
		state.status = $status.val();
		state.start = 0;
		load();
	});
	let searchTimer = null;
	$root.find("[data-filter='search']").on("input", (event) => {
		clearTimeout(searchTimer);
		searchTimer = setTimeout(() => {
			state.search = $(event.currentTarget).val();
			state.start = 0;
			load();
		}, 350);
	});
	$pageLength.on("change", () => {
		const length = parseInt($pageLength.val(), 10) || PAGE_LENGTHS[0];
		// Stay on the page holding the first row now on screen, not back at the top.
		state.start = Math.floor(state.start / length) * length;
		state.pageLength = length;
		rememberPageLength("masters", length);
		load();
	});
	$root.find("[data-filter='threshold']").on("change", (event) => {
		state.threshold = parseInt($(event.currentTarget).val(), 10) || 90;
	});
	$root.on("click", "[data-action='refresh']", () => load());
	$root.on("click", "[data-action='accept-page']", () => acceptPage());
	$root.on("click", "[data-action='link-selected']", () => linkSelected());
	$root.on("click", "[data-action='keep-selected']", () => keepSelected());
	$root.on("click", "[data-action='retry-selected']", () => retrySelected());
	$root.on("click", "[data-action='clear-selected']", () => masterSelection.clear());
	$txnEntity.on("change", () => {
		state.txn.entity = $txnEntity.val();
		state.txn.start = 0;
		loadTransactions();
	});
	$txnPageLength.on("change", () => {
		const length = parseInt($txnPageLength.val(), 10) || PAGE_LENGTHS[0];
		state.txn.start = Math.floor(state.txn.start / length) * length;
		state.txn.pageLength = length;
		rememberPageLength("transactions", length);
		loadTransactions();
	});
	$root.on("click", "[data-action='txn-refresh']", () => loadTransactions());
	$root.on("click", "[data-action='retry-page']", () => retryPage());
	$root.on("click", "[data-action='txn-retry-selected']", () => retryTxnSelected());
	$root.on("click", "[data-action='txn-clear-selected']", () => txnSelection.clear());

	// ------------------------------------------------------------------ masters
	function load() {
		const $body = $root.find(".qbo-mt-body").first();
		$body.html(`<div class="qbo-mt-empty">${__("Loading…")}</div>`);
		frappe.call({
			method: API + "get_match_queue",
			args: {
				entity_types: state.entity ? [state.entity] : null,
				status: state.status,
				search: state.search || null,
				start: state.start,
				page_length: state.pageLength,
			},
			callback(response) {
				const data = response.message || {};
				state.rows = data.rows || [];
				state.total = data.total || 0;
				state.counts = data.counts || {};
				state.served = data.page_length || state.pageLength;
				masterSelection.prune();
				renderCounts();
				renderRows();
				renderPager();
				masterSelection.sync();
			},
			error() {
				$body.html(`<div class="qbo-mt-empty">${__("Could not load the queue. You may not have permission.")}</div>`);
			},
		});
	}

	function renderCounts() {
		const c = state.counts;
		const chips = [
			["Needs decision", __("need a decision")],
			["Pending Review", __("pending review")],
			["Conflict", __("in conflict")],
			["Auto Matched", __("auto matched")],
			["Created", __("created by import")],
			["Manual Matched", __("decided")],
		];
		const html = chips
			.map(
				([key, label]) =>
					`<button class="qbo-mt-chip ${state.status === key ? "active" : ""}" data-chip="${key}"><b>${c[key] || 0}</b> ${label}</button>`,
			)
			.join("");
		$root
			.find(".qbo-mt-counts")
			.first()
			.html(`${html}<span class="qbo-mt-chip qbo-mt-chip-static"><b>${c.total || 0}</b> ${__("mapped in total")}</span>`);
	}
	$root.on("click", "[data-chip]", (event) => {
		state.status = $(event.currentTarget).attr("data-chip");
		$status.val(state.status);
		state.start = 0;
		load();
	});

	function formLink(doctype, name, title) {
		if (!doctype || !name) {
			return "";
		}
		const href = frappe.utils.get_form_link(doctype, name);
		return `<a href="${esc(href)}" target="_blank" rel="noopener">${esc(title || name)}</a>`;
	}

	function pill(text, colour) {
		return `<span class="indicator-pill ${colour || "gray"} qbo-mt-pill">${esc(text)}</span>`;
	}

	function checkCell(index, checked, enabled) {
		return `<td class="qbo-mt-col-check"><input type="checkbox" data-pick-row="${index}" ${checked ? "checked" : ""} ${enabled ? "" : "disabled"} aria-label="${__("Select this row")}" /></td>`;
	}

	function checkHeader() {
		return `<th class="qbo-mt-col-check"><input type="checkbox" data-pick-all aria-label="${__("Select every row on this page")}" /></th>`;
	}

	function renderRows() {
		const $body = $root.find(".qbo-mt-body").first();
		Object.keys(pickers).forEach((key) => delete pickers[key]);
		if (!state.rows.length) {
			$body.html(`<div class="qbo-mt-empty">${__("Nothing here. Change the filter, or you are done.")}</div>`);
			return;
		}
		const rows = state.rows
			.map((row, index) => {
				const qbo = row.qbo || {};
				const selected = masterSelection.has(row);
				const detail = (qbo.detail || []).map((d) => `<div class="qbo-mt-sub">${esc(d)}</div>`).join("");
				const flags = [];
				if (qbo.job) {
					flags.push(pill(__("QBO job"), "purple"));
				}
				if (qbo.inactive) {
					flags.push(pill(__("inactive in QBO"), "gray"));
				}
				if (!row.has_payload && !row.unmapped) {
					flags.push(pill(__("no payload stored"), "red"));
				}
				let current = `<div class="qbo-mt-sub">${__("Not linked")}</div>`;
				if (row.link) {
					const link = row.link;
					current = link.exists
						? `<div>${formLink(link.doctype, link.name, link.title)}</div>`
						: `<div class="qbo-mt-missing">${esc(link.doctype)} ${esc(link.name)} ${__("(no longer exists)")}</div>`;
					if (link.title && link.title !== link.name) {
						current += `<div class="qbo-mt-sub">${esc(link.name)}</div>`;
					}
				}
				const statusBits = [pill(row.match_status || "", STATUS_COLOUR[row.match_status] || "gray")];
				if (row.conflict_status === "Conflict") {
					statusBits.push(pill(__("conflict"), "red"));
				}
				if (row.agrees) {
					statusBits.push(`<span class="qbo-mt-agree">${__("name matches")} (${esc(row.agrees)})</span>`);
				} else if (row.match_rule && row.match_status !== "Pending Review") {
					statusBits.push(`<span class="qbo-mt-sub">${esc(row.match_rule)}</span>`);
				}
				if (row.reviewed_by) {
					statusBits.push(
						`<span class="qbo-mt-sub">${__("reviewed by {0}", [esc(frappe.user.full_name(row.reviewed_by))])}</span>`,
					);
				}
				const issues = (row.issues || []).map((i) => `<div class="qbo-mt-issue">${esc(i)}</div>`).join("");
				const suggestions = (row.suggestions || []).length
					? row.suggestions
							.map(
								(s) => `
						<div class="qbo-mt-sugg">
							<button class="btn btn-xs btn-default" data-use="${esc(s.name)}" data-row="${index}" title="${__("Use this record")}">${esc(s.title || s.name)}</button>
							<span class="qbo-mt-score" data-score="${scoreBand(s.score)}">${s.score}%</span>
							<span class="qbo-mt-sub">${esc(s.rule || "")}</span>
						</div>`,
							)
							.join("")
					: `<div class="qbo-mt-sub">${__("No similar {0} found", [esc(row.expected_doctype || "record")])}</div>`;
				return `
					<tr class="qbo-mt-row ${selected ? "is-selected" : ""}" data-row="${index}" data-row-index="${index}">
						${checkCell(index, selected, canSelect(row))}
						<td class="qbo-mt-col-qbo">
							<div class="qbo-mt-name">${esc(qbo.name || row.qbo_id)}</div>
							<div class="qbo-mt-sub">${esc(row.entity_type)} #${esc(row.qbo_id)}</div>
							${detail}
							<div>${flags.join(" ")}</div>
						</td>
						<td class="qbo-mt-col-link">
							${current}
							<div class="qbo-mt-status">${statusBits.join(" ")}</div>
							${issues}
						</td>
						<td class="qbo-mt-col-sugg">${suggestions}</td>
						<td class="qbo-mt-col-pick">
							<div class="qbo-mt-picker" data-picker="${index}"></div>
							<label class="qbo-mt-fill"><input type="checkbox" data-fill="${index}" ${state.fills.has(rowKey(row)) ? "checked" : ""} /> ${__("Fill blank fields from QBO")}</label>
						</td>
						<td class="qbo-mt-col-actions">
							<button class="btn btn-xs btn-primary" data-act="link" data-row="${index}">${__("Link")}</button>
							${row.unmapped ? "" : `<button class="btn btn-xs btn-default" data-act="keep" data-row="${index}">${__("Keep")}</button>`}
							${canRetry(row) ? `<button class="btn btn-xs btn-default" data-act="retry" data-row="${index}">${__("Retry")}</button>` : ""}
						</td>
					</tr>`;
			})
			.join("");
		$body.html(`
			<table class="qbo-mt-table">
				<thead>
					<tr>
						${checkHeader()}
						<th>${__("QuickBooks record")}</th>
						<th>${__("Linked to now")}</th>
						<th>${__("Could also be")}</th>
						<th>${__("Link to")}</th>
						<th></th>
					</tr>
				</thead>
				<tbody>${rows}</tbody>
			</table>
		`);
		state.rows.forEach((row, index) => mountPicker(row, index));
	}

	function scoreBand(score) {
		if (score >= 90) {
			return "high";
		}
		if (score >= 70) {
			return "mid";
		}
		return "low";
	}

	// Pre-seed the title a title-link doctype shows in the picker, so it needs no lookup.
	function seedTitle(row, name) {
		const known = (row.suggestions || []).find((s) => s.name === name);
		if (known && known.title) {
			frappe.utils.add_link_title(row.expected_doctype, name, known.title);
		}
	}

	function mountPicker(row, index) {
		const $cell = $root.find(`[data-picker='${index}']`);
		if (!row.expected_doctype) {
			$cell.html(`<div class="qbo-mt-sub">${__("No ERPNext destination")}</div>`);
			return;
		}
		const key = rowKey(row);
		const control = frappe.ui.form.make_control({
			parent: $cell,
			df: {
				fieldtype: "Link",
				options: row.expected_doctype,
				fieldname: `target_${index}`,
				placeholder: __("Pick a {0}", [row.expected_doctype]),
				// Only a change the accountant makes gets here (see the header): choosing
				// a record ticks the row.
				onchange: () => {
					const value = String(control.value || "").trim();
					state.picks[key] = value;
					if (value) {
						masterSelection.set(index, true);
						masterSelection.sync();
					}
				},
			},
			render_input: true,
			only_input: true,
		});
		control.refresh();
		const best = (row.suggestions || [])[0];
		const chosen = Object.prototype.hasOwnProperty.call(state.picks, key) ? state.picks[key] : best ? best.name : "";
		if (chosen) {
			seedTitle(row, chosen);
			control.set_input(chosen);
		}
		pickers[index] = control;
	}

	$root.on("click", "[data-use]", (event) => {
		const $button = $(event.currentTarget);
		const index = parseInt($button.attr("data-row"), 10);
		const row = state.rows[index];
		const control = pickers[index];
		if (!row || !control) {
			return;
		}
		const name = $button.attr("data-use");
		seedTitle(row, name);
		control.set_value(name);
		state.picks[rowKey(row)] = name;
		masterSelection.set(index, true);
		masterSelection.sync();
	});

	$root.on("change", "[data-fill]", (event) => {
		const row = state.rows[parseInt($(event.currentTarget).attr("data-fill"), 10)];
		if (!row) {
			return;
		}
		if (event.currentTarget.checked) {
			state.fills.add(rowKey(row));
		} else {
			state.fills.delete(rowKey(row));
		}
	});

	$root.on("click", "[data-act]", (event) => {
		const $button = $(event.currentTarget);
		const index = parseInt($button.attr("data-row"), 10);
		const row = state.rows[index];
		if (!row) {
			return;
		}
		const action = $button.attr("data-act");
		if (action === "link") {
			linkRow(row, index);
		} else if (action === "keep") {
			keepRow(row);
		} else if (action === "retry") {
			retryRow(row.entity_type, row.qbo_id, () => load());
		}
	});

	function pickedValue(index) {
		const control = pickers[index];
		return control ? (control.get_value() || "").trim() : "";
	}

	function pickedLabel(index) {
		const control = pickers[index];
		return control && control.get_label_value ? (control.get_label_value() || "").trim() : "";
	}

	function fillTicked(index) {
		return $root.find(`[data-fill='${index}']`).is(":checked");
	}

	function linkRow(row, index) {
		const target = pickedValue(index);
		if (!target) {
			frappe.msgprint(__("Pick the {0} to link this QuickBooks record to.", [row.expected_doctype]));
			return;
		}
		const fill = fillTicked(index) ? 1 : 0;
		frappe.call({
			method: API + "decide_match",
			args: { entity_type: row.entity_type, qbo_id: row.qbo_id, erpnext_name: target, fill_blanks: fill, merge_duplicate: 1 },
			freeze: true,
			freeze_message: __("Linking…"),
			callback(response) {
				reportDecision(row, response.message || {});
				forget(rowKey(row));
				load();
			},
		});
	}

	function reportDecision(row, result) {
		const linked = result.linked || {};
		const merge = result.merge || {};
		let message = __("{0} #{1} linked to {2} {3}.", [esc(row.entity_type), esc(row.qbo_id), esc(linked.doctype || ""), esc(linked.name || "")]);
		let indicator = "green";
		if (merge.status === "merged") {
			message += " " + __("Merged the import's copy {0} into it.", [esc(merge.from)]);
		} else if (merge.status === "failed") {
			indicator = "orange";
			message += " " + __("The import's copy {0} was NOT merged: {1}", [esc(merge.from), esc(merge.error || "")]);
		} else if (merge.duplicate) {
			indicator = "orange";
			message += " " + __("The import's copy {0} was left in place.", [esc(merge.duplicate)]);
		}
		frappe.show_alert({ message, indicator }, indicator === "green" ? 6 : 12);
	}

	function keepRow(row) {
		frappe.call({
			method: API + "confirm_match",
			args: { entity_type: row.entity_type, qbo_id: row.qbo_id },
			callback() {
				frappe.show_alert({ message: __("Kept as is."), indicator: "green" }, 3);
				masterSelection.drop(rowKey(row));
				load();
			},
		});
	}

	function retryRow(entityType, qboId, done) {
		frappe.call({
			method: API + "sync_entity",
			args: { entity_type: entityType, qbo_id: qboId },
			freeze: true,
			freeze_message: __("Re-syncing {0} #{1}…", [entityType, qboId]),
			callback(response) {
				const result = (response.message || {}).result || {};
				const action = result.action || "done";
				frappe.show_alert(
					{
						message: __("{0} #{1}: {2}{3}", [
							esc(entityType),
							esc(qboId),
							esc(action),
							result.reason ? " — " + esc(result.reason) : "",
						]),
						indicator: action === "manual_review" || action === "failed" ? "orange" : "green",
					},
					8,
				);
				if (done) {
					done();
				}
			},
		});
	}

	/**
	 * Send `items` to a list endpoint (decide_matches, confirm_matches) `size` at a time,
	 * one request after another, and hand `done` one result per item, in order. A request
	 * that fails outright (a timeout, a dropped connection) marks its chunk failed and the
	 * run carries on -- each item is its own decision on the server. Some of a failed
	 * chunk may still have landed, which the reload that follows shows.
	 */
	function runInChunks({ method, argName, items, size, extra, progress, done }) {
		const results = [];
		let offset = 0;
		const next = () => {
			if (offset >= items.length) {
				done(results);
				return;
			}
			const chunk = items.slice(offset, offset + size);
			frappe.dom.freeze(progress(offset + 1, offset + chunk.length, items.length));
			offset += chunk.length;
			frappe.call({
				method: API + method,
				args: Object.assign({ [argName]: chunk }, extra || {}),
				callback(response) {
					results.push(...(response.message || []));
					frappe.dom.unfreeze();
					next();
				},
				error() {
					chunk.forEach((item) =>
						results.push({
							entity_type: item.entity_type,
							qbo_id: item.qbo_id,
							ok: false,
							error: __("The request failed; Refresh to see whether it landed."),
						}),
					);
					frappe.dom.unfreeze();
					next();
				},
			});
		};
		next();
	}

	function runDecisions(decisions, title) {
		runInChunks({
			method: "decide_matches",
			argName: "decisions",
			items: decisions.map(({ entity_type, qbo_id, erpnext_name, fill_blanks }) =>
				fill_blanks === undefined ? { entity_type, qbo_id, erpnext_name } : { entity_type, qbo_id, erpnext_name, fill_blanks },
			),
			size: LINK_CHUNK,
			extra: { fill_blanks: 0, merge_duplicate: 1 },
			progress: (from, to, total) => __("Linking {0}–{1} of {2}…", [from, to, total]),
			done(results) {
				results.filter((r) => r.ok).forEach((r) => forget(`${r.entity_type}:${r.qbo_id}`));
				reportLinks(results, title);
				load();
			},
		});
	}

	function reportLinks(results, title) {
		const ok = results.filter((r) => r.ok).length;
		const merged = results.filter((r) => r.ok && r.merge && r.merge.status === "merged").length;
		const mergeFailed = results.filter((r) => r.ok && r.merge && r.merge.status === "failed");
		const failed = results.filter((r) => !r.ok);
		let message = __("{0} linked, {1} copies merged.", [ok, merged]);
		if (mergeFailed.length) {
			message += "<br>" + __("{0} copies could not be merged:", [mergeFailed.length]) + "<ul>" + mergeFailed.map((r) => `<li>${esc(r.merge.from)}: ${esc(r.merge.error || "")}</li>`).join("") + "</ul>";
		}
		if (failed.length) {
			message += "<br>" + __("{0} failed:", [failed.length]) + "<ul>" + failed.map((r) => `<li>${esc(r.entity_type)} #${esc(r.qbo_id)}: ${esc(r.error || "")}</li>`).join("") + "</ul>";
		}
		frappe.msgprint({ title, message, indicator: failed.length || mergeFailed.length ? "orange" : "green" });
	}

	function acceptPage() {
		const decisions = [];
		state.rows.forEach((row) => {
			const best = (row.suggestions || [])[0];
			if (best && best.score >= state.threshold && row.expected_doctype) {
				decisions.push({ entity_type: row.entity_type, qbo_id: row.qbo_id, erpnext_name: best.name, label: `${row.qbo.name} → ${best.title || best.name}` });
			}
		});
		if (!decisions.length) {
			frappe.msgprint(__("No row on this page has a suggestion at {0}% or better.", [state.threshold]));
			return;
		}
		const preview = decisions
			.slice(0, 12)
			.map((d) => `<li>${esc(d.label)}</li>`)
			.join("");
		const more = decisions.length > 12 ? `<li>… ${__("and {0} more", [decisions.length - 12])}</li>` : "";
		frappe.confirm(
			`${__("Link {0} record(s) to their best suggestion and merge any copies the import created?", [decisions.length])}<ul>${preview}${more}</ul>`,
			() => runDecisions(decisions, __("Accepted suggestions")),
		);
	}

	/**
	 * Link every ticked row to the record in its own picker. The confirmation says, in
	 * counts, what the batch will do -- how many rows get a first link, how many move off
	 * the record they point at now, how many are already there -- and then lists every
	 * row, because the picker's pre-fill is the best *other* record: a ticked row that
	 * is already right would otherwise be re-pointed without anyone noticing.
	 */
	function linkSelected() {
		const decisions = [];
		const unpicked = [];
		masterSelection
			.chosen()
			.filter(({ row }) => row.expected_doctype)
			.forEach(({ row, index }) => {
				const target = pickedValue(index);
				if (!target) {
					unpicked.push(row);
					return;
				}
				decisions.push({
					entity_type: row.entity_type,
					qbo_id: row.qbo_id,
					erpnext_name: target,
					fill_blanks: fillTicked(index) ? 1 : 0,
					row,
					label: pickedLabel(index),
				});
			});
		if (!decisions.length) {
			frappe.msgprint(__("None of the selected rows has a record picked. Choose one in the Link to column first."));
			return;
		}
		const stays = (d) => d.row.link.doctype === d.row.expected_doctype && d.row.link.name === d.erpnext_name;
		const fresh = decisions.filter((d) => !d.row.link).length;
		const moving = decisions.filter((d) => d.row.link && !stays(d));
		const copies = moving.filter((d) => d.row.match_status === "Created").length;
		const unchanged = decisions.filter((d) => d.row.link && stays(d)).length;
		const filling = decisions.filter((d) => d.fill_blanks).length;
		const facts = [];
		if (fresh) {
			facts.push(__("{0} get their first link.", [fresh]));
		}
		if (moving.length) {
			let fact = `<b>${__("{0} move off the record they are linked to now.", [moving.length])}</b>`;
			if (copies) {
				fact += " " + __("{0} of those are leaving a record the import created, which is merged into the new one where ERPNext allows it.", [copies]);
			}
			facts.push(fact);
		}
		if (unchanged) {
			facts.push(__("{0} already point at the picked record and are only marked reviewed.", [unchanged]));
		}
		if (filling) {
			facts.push(__("{0} also fill their blank fields from QuickBooks.", [filling]));
		}
		if (unpicked.length) {
			facts.push(__("{0} selected rows have no record picked and are left alone.", [unpicked.length]));
		}
		const lines = decisions
			.map((d) => {
				const qbo = d.row.qbo || {};
				const now = d.row.link ? `${esc(d.row.link.title || d.row.link.name)} <span class="qbo-mt-sub">(${esc(d.row.match_status || "")})</span>` : `<span class="qbo-mt-sub">${__("not linked")}</span>`;
				const to = d.label && d.label !== d.erpnext_name ? `${esc(d.label)} <span class="qbo-mt-sub">${esc(d.erpnext_name)}</span>` : esc(d.erpnext_name);
				return `
					<tr>
						<td>${esc(qbo.name || d.qbo_id)}<div class="qbo-mt-sub">${esc(d.entity_type)} #${esc(d.qbo_id)}</div></td>
						<td>${now}</td>
						<td>→ ${to}</td>
					</tr>`;
			})
			.join("");
		frappe.confirm(
			`<p>${__("Link {0} selected record(s) to the record picked on each row?", [decisions.length])}</p>
			<ul>${facts.map((f) => `<li>${f}</li>`).join("")}</ul>
			<div class="qbo-mt-confirm-list">
				<table class="qbo-mt-confirm-table">
					<thead><tr><th>${__("QuickBooks record")}</th><th>${__("Linked to now")}</th><th>${__("Will link to")}</th></tr></thead>
					<tbody>${lines}</tbody>
				</table>
			</div>`,
			() => runDecisions(decisions, __("Linked selected records")),
		);
	}

	function keepSelected() {
		const rows = masterSelection
			.chosen()
			.map(({ row }) => row)
			.filter((row) => !row.unmapped);
		if (!rows.length) {
			return;
		}
		frappe.confirm(__("Mark the {0} selected rows reviewed, leaving each one linked as it is now?", [rows.length]), () =>
			runInChunks({
				method: "confirm_matches",
				argName: "pairs",
				items: rows.map((row) => ({ entity_type: row.entity_type, qbo_id: row.qbo_id })),
				size: KEEP_CHUNK,
				progress: (from, to, total) => __("Marking {0}–{1} of {2} reviewed…", [from, to, total]),
				done(results) {
					const kept = results.filter((r) => r.ok);
					const failed = results.filter((r) => !r.ok);
					kept.forEach((r) => masterSelection.drop(`${r.entity_type}:${r.qbo_id}`));
					if (failed.length) {
						frappe.msgprint({
							title: __("Kept selected rows"),
							message:
								__("{0} kept as they are; {1} failed:", [kept.length, failed.length]) +
								"<ul>" +
								failed.map((r) => `<li>${esc(r.entity_type)} #${esc(r.qbo_id)}: ${esc(r.error || "")}</li>`).join("") +
								"</ul>",
							indicator: "orange",
						});
					} else {
						frappe.show_alert({ message: __("{0} kept as they are.", [kept.length]), indicator: "green" }, 5);
					}
					load();
				},
			}),
		);
	}

	function retrySelected() {
		const rows = masterSelection
			.chosen()
			.map(({ row }) => row)
			.filter(canRetry);
		if (!rows.length) {
			return;
		}
		frappe.confirm(__("Re-sync the {0} selected parked records from QuickBooks, one after another?", [rows.length]), () =>
			retrySequence(rows, () => {
				rows.forEach((row) => masterSelection.drop(rowKey(row)));
				load();
			}),
		);
	}

	function renderPager() {
		renderPagerInto($root.find(".qbo-mt-pager").first(), state.start, state.served, state.total, (start) => {
			state.start = start;
			load();
		});
	}

	function renderPagerInto($pager, start, pageLength, total, go) {
		if (!total) {
			$pager.empty();
			return;
		}
		const from = start + 1;
		const to = Math.min(start + pageLength, total);
		$pager.html(`
			<button class="btn btn-default btn-xs" data-page="prev" ${start <= 0 ? "disabled" : ""}>${__("Previous")}</button>
			<span class="qbo-mt-sub">${__("{0}–{1} of {2}", [from, to, total])}</span>
			<button class="btn btn-default btn-xs" data-page="next" ${to >= total ? "disabled" : ""}>${__("Next")}</button>
		`);
		$pager.find("[data-page='prev']").on("click", () => go(Math.max(0, start - pageLength)));
		$pager.find("[data-page='next']").on("click", () => go(start + pageLength));
	}

	// ------------------------------------------------------------- transactions
	function loadTransactions() {
		const $body = $root.find("[data-txn-body]");
		$body.html(`<div class="qbo-mt-empty">${__("Loading…")}</div>`);
		frappe.call({
			method: API + "get_parked_transactions",
			args: {
				entity_types: state.txn.entity ? [state.txn.entity] : null,
				start: state.txn.start,
				page_length: state.txn.pageLength,
			},
			callback(response) {
				const data = response.message || {};
				state.txn.rows = data.rows || [];
				state.txn.total = data.total || 0;
				state.txn.counts = data.counts || {};
				state.txn.served = data.page_length || state.txn.pageLength;
				txnSelection.prune();
				renderTransactions();
				txnSelection.sync();
			},
			error() {
				$body.html(`<div class="qbo-mt-empty">${__("Could not load parked transactions.")}</div>`);
			},
		});
	}

	function renderTransactions() {
		const counts = state.txn.counts;
		$root.find("[data-txn-counts]").html(
			Object.keys(counts)
				.sort()
				.map((type) => `<button class="qbo-mt-chip ${state.txn.entity === type ? "active" : ""}" data-txn-chip="${esc(type)}"><b>${counts[type]}</b> ${esc(type)}</button>`)
				.join("") || `<span class="qbo-mt-sub">${__("No parked transactions.")}</span>`,
		);
		const $body = $root.find("[data-txn-body]");
		if (!state.txn.rows.length) {
			$body.html(`<div class="qbo-mt-empty">${__("Nothing parked.")}</div>`);
			renderPagerInto($root.find("[data-txn-pager]"), 0, state.txn.served, 0, () => {});
			return;
		}
		const rows = state.txn.rows
			.map((row, index) => {
				const qbo = row.qbo || {};
				const selected = txnSelection.has(row);
				const total = qbo.total === null || qbo.total === undefined ? "" : format_currency(qbo.total);
				const draft = row.name
					? row.exists
						? formLink(row.doctype, row.name, row.name)
						: `<span class="qbo-mt-missing">${esc(row.name)} ${__("(gone)")}</span>`
					: `<span class="qbo-mt-sub">${__("no draft")}</span>`;
				const issues = (row.issues || []).map((i) => `<div class="qbo-mt-issue">${esc(i)}</div>`).join("") || `<div class="qbo-mt-sub">${esc(row.match_rule || "")}</div>`;
				return `
					<tr class="qbo-mt-row ${selected ? "is-selected" : ""}" data-txn-row="${index}" data-row-index="${index}">
						${checkCell(index, selected, true)}
						<td class="qbo-mt-col-qbo">
							<div class="qbo-mt-name">${esc(row.entity_type)} ${esc(qbo.doc_number ? "#" + qbo.doc_number : "")}</div>
							<div class="qbo-mt-sub">${esc(qbo.txn_date || "")} ${qbo.party ? "· " + esc(qbo.party) : ""} ${total ? "· " + total : ""}</div>
							<div class="qbo-mt-sub">QBO id ${esc(row.qbo_id)}</div>
						</td>
						<td>${draft}<div class="qbo-mt-sub">${esc(row.doctype || "")}</div></td>
						<td>${issues}</td>
						<td class="qbo-mt-col-actions">
							<button class="btn btn-xs btn-default" data-txn-retry="${index}">${__("Retry")}</button>
						</td>
					</tr>`;
			})
			.join("");
		$body.html(`
			<table class="qbo-mt-table">
				<thead>
					<tr>
						${checkHeader()}
						<th>${__("QuickBooks transaction")}</th>
						<th>${__("ERPNext draft")}</th>
						<th>${__("Why it parked")}</th>
						<th></th>
					</tr>
				</thead>
				<tbody>${rows}</tbody>
			</table>
		`);
		renderPagerInto($root.find("[data-txn-pager]"), state.txn.start, state.txn.served, state.txn.total, (start) => {
			state.txn.start = start;
			loadTransactions();
		});
	}

	$root.on("click", "[data-txn-chip]", (event) => {
		const type = $(event.currentTarget).attr("data-txn-chip");
		state.txn.entity = state.txn.entity === type ? "" : type;
		$txnEntity.val(state.txn.entity);
		state.txn.start = 0;
		loadTransactions();
	});

	$root.on("click", "[data-txn-retry]", (event) => {
		const row = state.txn.rows[parseInt($(event.currentTarget).attr("data-txn-retry"), 10)];
		if (row) {
			retryRow(row.entity_type, row.qbo_id, () => loadTransactions());
		}
	});

	// Re-sync `rows` one request at a time, then report the tally and call `done`.
	function retrySequence(rows, done) {
		let index = 0;
		const outcomes = { ok: 0, parked: 0, failed: 0 };
		const step = () => {
			if (index >= rows.length) {
				frappe.msgprint({
					title: __("Retry finished"),
					message: __("{0} imported, {1} still parked, {2} failed.", [outcomes.ok, outcomes.parked, outcomes.failed]),
					indicator: outcomes.failed || outcomes.parked ? "orange" : "green",
				});
				done();
				return;
			}
			const row = rows[index++];
			frappe.dom.freeze(__("Re-syncing {0} of {1}…", [index, rows.length]));
			frappe.call({
				method: API + "sync_entity",
				args: { entity_type: row.entity_type, qbo_id: row.qbo_id },
				callback(response) {
					const action = ((response.message || {}).result || {}).action;
					if (action === "manual_review") {
						outcomes.parked += 1;
					} else {
						outcomes.ok += 1;
					}
					frappe.dom.unfreeze();
					step();
				},
				error() {
					outcomes.failed += 1;
					frappe.dom.unfreeze();
					step();
				},
			});
		};
		step();
	}

	function retryTxnRows(rows, question) {
		if (!rows.length) {
			return;
		}
		frappe.confirm(question, () =>
			retrySequence(rows, () => {
				rows.forEach((row) => txnSelection.drop(rowKey(row)));
				loadTransactions();
			}),
		);
	}

	function retryPage() {
		const rows = state.txn.rows.slice();
		retryTxnRows(rows, __("Re-sync all {0} parked transactions on this page, one after another?", [rows.length]));
	}

	function retryTxnSelected() {
		const rows = txnSelection.chosen().map(({ row }) => row);
		retryTxnRows(rows, __("Re-sync the {0} selected parked transactions, one after another?", [rows.length]));
	}

	load();
};
