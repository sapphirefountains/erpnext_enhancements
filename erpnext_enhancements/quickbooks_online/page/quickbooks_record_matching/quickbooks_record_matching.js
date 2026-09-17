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
 *    parked row after its cause is fixed). "Accept suggestions" links every row on
 *    the page whose best suggestion clears the threshold, in one call.
 *  - Parked transactions: Invoices, Bills, Purchases etc. that failed validation on
 *    import, with the stored reason, the draft if one exists, and Retry.
 *
 * Endpoints (erpnext_enhancements.quickbooks_online.core.api): get_match_queue,
 * decide_match, decide_matches, confirm_match, get_parked_transactions, sync_entity.
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

	const state = {
		tab: "masters",
		entity: "",
		status: "Needs decision",
		search: "",
		start: 0,
		pageLength: 50,
		threshold: 90,
		rows: [],
		total: 0,
		counts: {},
		txn: { entity: "", start: 0, pageLength: 50, rows: [], total: 0, counts: {} },
	};
	const pickers = {};

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
					<span class="qbo-mt-spacer"></span>
					<label class="qbo-mt-threshold">${__("Accept at")}
						<input type="number" class="form-control input-sm" data-filter="threshold" min="50" max="100" step="5" value="90" />%
					</label>
					<button class="btn btn-default btn-sm" data-action="accept-page">${__("Accept suggestions on this page")}</button>
					<button class="btn btn-default btn-sm" data-action="refresh">${__("Refresh")}</button>
				</div>
				<div class="qbo-mt-counts"></div>
				<div class="qbo-mt-body"></div>
				<div class="qbo-mt-pager"></div>
			</div>
			<div class="qbo-mt-panel" data-panel="transactions" hidden>
				<div class="qbo-mt-toolbar">
					<select class="form-control input-sm" data-txn-filter="entity"></select>
					<span class="qbo-mt-spacer"></span>
					<button class="btn btn-default btn-sm" data-action="retry-page">${__("Retry all on this page")}</button>
					<button class="btn btn-default btn-sm" data-action="txn-refresh">${__("Refresh")}</button>
				</div>
				<div class="qbo-mt-counts" data-txn-counts></div>
				<div class="qbo-mt-body" data-txn-body></div>
				<div class="qbo-mt-pager" data-txn-pager></div>
			</div>
		</div>
	`).appendTo(page.body);

	page.set_secondary_action(__("QuickBooks Dashboard"), () => frappe.set_route("quickbooks-online-dashboard"));
	page.add_action_item(__("Sync Mapping list"), () => frappe.set_route("List", "QuickBooks Sync Mapping"));

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
	$root.find("[data-filter='threshold']").on("change", (event) => {
		state.threshold = parseInt($(event.currentTarget).val(), 10) || 90;
	});
	$root.on("click", "[data-action='refresh']", () => load());
	$root.on("click", "[data-action='accept-page']", () => acceptPage());
	$txnEntity.on("change", () => {
		state.txn.entity = $txnEntity.val();
		state.txn.start = 0;
		loadTransactions();
	});
	$root.on("click", "[data-action='txn-refresh']", () => loadTransactions());
	$root.on("click", "[data-action='retry-page']", () => retryPage());

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
				renderCounts();
				renderRows();
				renderPager();
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
				const canRetry = row.match_status === "Pending Review" && !row.unmapped;
				return `
					<tr class="qbo-mt-row" data-row="${index}">
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
							<label class="qbo-mt-fill"><input type="checkbox" data-fill="${index}" /> ${__("Fill blank fields from QBO")}</label>
						</td>
						<td class="qbo-mt-col-actions">
							<button class="btn btn-xs btn-primary" data-act="link" data-row="${index}">${__("Link")}</button>
							${row.unmapped ? "" : `<button class="btn btn-xs btn-default" data-act="keep" data-row="${index}">${__("Keep")}</button>`}
							${canRetry ? `<button class="btn btn-xs btn-default" data-act="retry" data-row="${index}">${__("Retry")}</button>` : ""}
						</td>
					</tr>`;
			})
			.join("");
		$body.html(`
			<table class="qbo-mt-table">
				<thead>
					<tr>
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

	function mountPicker(row, index) {
		const $cell = $root.find(`[data-picker='${index}']`);
		if (!row.expected_doctype) {
			$cell.html(`<div class="qbo-mt-sub">${__("No ERPNext destination")}</div>`);
			return;
		}
		const control = frappe.ui.form.make_control({
			parent: $cell,
			df: {
				fieldtype: "Link",
				options: row.expected_doctype,
				fieldname: `target_${index}`,
				placeholder: __("Pick a {0}", [row.expected_doctype]),
			},
			render_input: true,
			only_input: true,
		});
		control.refresh();
		const best = (row.suggestions || [])[0];
		if (best) {
			control.set_value(best.name);
		}
		pickers[index] = control;
	}

	$root.on("click", "[data-use]", (event) => {
		const $button = $(event.currentTarget);
		const index = $button.attr("data-row");
		const control = pickers[index];
		if (control) {
			control.set_value($button.attr("data-use"));
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

	function linkRow(row, index) {
		const target = pickedValue(index);
		if (!target) {
			frappe.msgprint(__("Pick the {0} to link this QuickBooks record to.", [row.expected_doctype]));
			return;
		}
		const fill = $root.find(`[data-fill='${index}']`).is(":checked") ? 1 : 0;
		frappe.call({
			method: API + "decide_match",
			args: { entity_type: row.entity_type, qbo_id: row.qbo_id, erpnext_name: target, fill_blanks: fill, merge_duplicate: 1 },
			freeze: true,
			freeze_message: __("Linking…"),
			callback(response) {
				reportDecision(row, response.message || {});
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
			() => {
				frappe.call({
					method: API + "decide_matches",
					args: { decisions: decisions.map(({ entity_type, qbo_id, erpnext_name }) => ({ entity_type, qbo_id, erpnext_name })), fill_blanks: 0, merge_duplicate: 1 },
					freeze: true,
					freeze_message: __("Linking {0} records…", [decisions.length]),
					callback(response) {
						const results = response.message || [];
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
						frappe.msgprint({ title: __("Accepted suggestions"), message, indicator: failed.length || mergeFailed.length ? "orange" : "green" });
						load();
					},
				});
			},
		);
	}

	function renderPager() {
		renderPagerInto($root.find(".qbo-mt-pager").first(), state.start, state.pageLength, state.total, (start) => {
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
				renderTransactions();
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
			renderPagerInto($root.find("[data-txn-pager]"), 0, state.txn.pageLength, 0, () => {});
			return;
		}
		const rows = state.txn.rows
			.map((row, index) => {
				const qbo = row.qbo || {};
				const total = qbo.total === null || qbo.total === undefined ? "" : format_currency(qbo.total);
				const draft = row.name
					? row.exists
						? formLink(row.doctype, row.name, row.name)
						: `<span class="qbo-mt-missing">${esc(row.name)} ${__("(gone)")}</span>`
					: `<span class="qbo-mt-sub">${__("no draft")}</span>`;
				const issues = (row.issues || []).map((i) => `<div class="qbo-mt-issue">${esc(i)}</div>`).join("") || `<div class="qbo-mt-sub">${esc(row.match_rule || "")}</div>`;
				return `
					<tr class="qbo-mt-row" data-txn-row="${index}">
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
						<th>${__("QuickBooks transaction")}</th>
						<th>${__("ERPNext draft")}</th>
						<th>${__("Why it parked")}</th>
						<th></th>
					</tr>
				</thead>
				<tbody>${rows}</tbody>
			</table>
		`);
		renderPagerInto($root.find("[data-txn-pager]"), state.txn.start, state.txn.pageLength, state.txn.total, (start) => {
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

	function retryPage() {
		const rows = state.txn.rows.slice();
		if (!rows.length) {
			return;
		}
		frappe.confirm(__("Re-sync all {0} parked transactions on this page, one after another?", [rows.length]), () => {
			let index = 0;
			const outcomes = { ok: 0, parked: 0, failed: 0 };
			const step = () => {
				if (index >= rows.length) {
					frappe.msgprint({
						title: __("Retry finished"),
						message: __("{0} imported, {1} still parked, {2} failed.", [outcomes.ok, outcomes.parked, outcomes.failed]),
						indicator: outcomes.failed || outcomes.parked ? "orange" : "green",
					});
					loadTransactions();
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
		});
	}

	load();
};
