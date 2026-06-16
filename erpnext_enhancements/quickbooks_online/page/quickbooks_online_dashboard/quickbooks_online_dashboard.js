/**
 * Client controller for the "QuickBooks Online" desk dashboard page.
 *
 * Renders the operator console for the QBO accounting integration and wires its
 * controls to the whitelisted RPC endpoints under
 * erpnext_enhancements.quickbooks_online.core.api:
 *
 *  - Status tiles (connection, environment, realm id, failed-log count) and a
 *    recent-sync-logs list, populated from `get_dashboard_status` (refresh()).
 *  - Per-entity panel: enter a QuickBooks ID and "Sync" a single entity via
 *    `sync_entity` (syncEntity()).
 *  - Toolbar/page actions:
 *      * Connect QuickBooks  -> `start_oauth` then redirect to Intuit (connectQuickBooks()).
 *      * Import All          -> confirm, then `import_all` (runImportAll()).
 *      * Preview Resync      -> `preview_resync`, show a summary, then optionally
 *                               `run_resync` to overwrite QBO-owned fields (previewResync()).
 *      * Retry Failed        -> `retry_failed` (retryFailed()).
 *      * Link Existing Records -> `preview_existing_matches`, then a dialog whose
 *                               "Link" button calls `link_existing_record`
 *                               (previewExistingMatches() / showMatchDialog()).
 *
 * The page is otherwise stateless; all data comes from the RPCs above.
 */
frappe.pages["quickbooks-online-dashboard"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("QuickBooks Online"),
		single_column: true,
	});

	page.set_primary_action(__("Import All"), () => runImportAll(), "download");
	page.add_action_item(__("Open Settings"), () => frappe.set_route("Form", "QuickBooks Online Settings"));
	page.add_action_item(__("Link Existing Records"), () => previewExistingMatches());
	page.add_action_item(__("Preview Resync"), () => previewResync());
	page.add_action_item(__("Retry Failed"), () => retryFailed());
	page.add_action_item(__("Compare Balances"), () =>
		frappe.set_route("query-report", "QuickBooks Balance Comparison"),
	);
	page.add_action_item(__("Reconcile Transactions"), () => reconcileTransactions());
	page.add_action_item(__("Import Opening Balances"), () => importOpeningBalances());

	const root = $(`
		<div class="qbo-dashboard">
			<div class="qbo-status-grid">
				<div class="qbo-status-item">
					<div class="qbo-label">${__("Connection")}</div>
					<div class="qbo-value" data-field="status">-</div>
				</div>
				<div class="qbo-status-item">
					<div class="qbo-label">${__("Environment")}</div>
					<div class="qbo-value" data-field="environment">-</div>
				</div>
				<div class="qbo-status-item">
					<div class="qbo-label">${__("Realm ID")}</div>
					<div class="qbo-value" data-field="realm_id">-</div>
				</div>
				<div class="qbo-status-item">
					<div class="qbo-label">${__("Failed Logs")}</div>
					<div class="qbo-value" data-field="failed_records">0</div>
				</div>
			</div>
			<div class="qbo-toolbar">
				<button class="btn btn-default" data-action="connect">${__("Connect QuickBooks")}</button>
				<button class="btn btn-default" data-action="matches">${__("Link Existing Records")}</button>
				<button class="btn btn-default" data-action="preview">${__("Preview Resync")}</button>
				<button class="btn btn-primary" data-action="import">${__("Import All")}</button>
			</div>
			<div class="qbo-section">
				<h4>${__("Accounting Core")}</h4>
				<div class="qbo-entity-list"></div>
			</div>
			<div class="qbo-section">
				<h4>${__("Recent Sync Logs")}</h4>
				<div class="qbo-log-list"></div>
			</div>
		</div>
	`).appendTo(page.body);

	root.on("click", "[data-action='connect']", () => connectQuickBooks());
	root.on("click", "[data-action='matches']", () => previewExistingMatches());
	root.on("click", "[data-action='preview']", () => previewResync());
	root.on("click", "[data-action='import']", () => runImportAll());
	root.on("click", "[data-entity]", (event) => {
		const entity = $(event.currentTarget).attr("data-entity");
		const qboId = root.find(`[data-qbo-id='${entity}']`).val();
		if (!qboId) {
			frappe.msgprint(__("Enter a QuickBooks ID before syncing this entity."));
			return;
		}
		syncEntity(entity, qboId);
	});

	renderEntities(root);
	refresh(root);
};

// Keep in sync with ACCOUNTING_ENTITIES in quickbooks_online/core/constants.py.
const QBO_ENTITIES = [
	"Term",
	"PaymentMethod",
	"Account",
	"Customer",
	"Vendor",
	"Item",
	"TaxCode",
	"Class",
	"Estimate",
	"Invoice",
	"SalesReceipt",
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
];

function renderEntities(root) {
	const list = root.find(".qbo-entity-list");
	list.empty();
	QBO_ENTITIES.forEach((entity) => {
		$(`
			<div class="qbo-entity-row">
				<div class="qbo-entity-name">${entity}</div>
				<input class="form-control input-sm" data-qbo-id="${entity}" placeholder="${__("QuickBooks ID")}" />
				<button class="btn btn-xs btn-default" data-entity="${entity}">${__("Sync")}</button>
			</div>
		`).appendTo(list);
	});
}

function refresh(root) {
	frappe.call({
		method: "erpnext_enhancements.quickbooks_online.core.api.get_dashboard_status",
		callback(response) {
			const data = response.message || {};
			const settings = data.settings || {};
			root.find("[data-field='status']").text(settings.status || "-");
			root.find("[data-field='environment']").text(settings.environment || "-");
			root.find("[data-field='realm_id']").text(settings.realm_id || "-");
			root.find("[data-field='failed_records']").text(data.failed_records || 0);
			renderLogs(root, data.latest_logs || []);
		},
	});
}

function renderLogs(root, logs) {
	const list = root.find(".qbo-log-list");
	list.empty();
	if (!logs.length) {
		list.html(`<div class="text-muted">${__("No sync logs yet.")}</div>`);
		return;
	}
	logs.forEach((log) => {
		$(`
			<div class="qbo-log-row">
				<div>
					<a data-route="Form/QuickBooks Sync Log/${log.name}">${log.name}</a>
					<div class="text-muted">${log.sync_type || ""} ${log.entity_type || ""}</div>
				</div>
				<div>${log.status}</div>
				<div>
					${__("C")} ${log.created_count || 0} /
					${__("L")} ${log.linked_count || 0} /
					${__("R")} ${log.manual_review_count || 0} /
					${__("X")} ${log.conflict_count || 0}
				</div>
			</div>
		`).appendTo(list);
	});
}

function connectQuickBooks() {
	frappe.db.get_single_value("QuickBooks Online Settings", "environment").then((environment) => {
		frappe.call({
			method: "erpnext_enhancements.quickbooks_online.core.api.start_oauth",
			args: { environment },
			callback(response) {
				const url = response.message && response.message.authorization_url;
				if (url) {
					window.location.href = url;
				}
			},
		});
	});
}

function runImportAll() {
	frappe.confirm(__("Import accounting-core QuickBooks Online data now?"), () => {
		frappe.call({
			method: "erpnext_enhancements.quickbooks_online.core.api.import_all",
			freeze: true,
			freeze_message: __("Importing QuickBooks Online data..."),
			callback(response) {
				frappe.msgprint(__("Import started/completed in log {0}", [response.message]));
				frappe.pages["quickbooks-online-dashboard"].page.wrapper && location.reload();
			},
		});
	});
}

function previewResync() {
	frappe.call({
		method: "erpnext_enhancements.quickbooks_online.core.api.preview_resync",
		freeze: true,
		freeze_message: __("Building resync preview..."),
		callback(response) {
			const result = response.message || {};
			const summary = result.summary || {};
			const message = __(
				"Preview {0}: {1} creates, {2} updates, {3} deletes, {4} conflicts.",
				[
					result.preview_id,
					summary.created || 0,
					summary.updated || 0,
					summary.deleted || 0,
					summary.conflicts || 0,
				],
			);
			frappe.confirm(message + "<br>" + __("Run overwrite resync for QuickBooks-owned fields?"), () => {
				frappe.call({
					method: "erpnext_enhancements.quickbooks_online.core.api.run_resync",
					args: { preview_id: result.preview_id },
					freeze: true,
					freeze_message: __("Running resync..."),
					callback(runResponse) {
						frappe.msgprint(__("Resync completed in log {0}", [runResponse.message.sync_log]));
					},
				});
			});
		},
	});
}

function retryFailed() {
	frappe.call({
		method: "erpnext_enhancements.quickbooks_online.core.api.retry_failed",
		freeze: true,
		freeze_message: __("Retrying failed syncs..."),
		callback() {
			frappe.msgprint(__("Retry requested."));
		},
	});
}

function previewExistingMatches() {
	frappe.call({
		method: "erpnext_enhancements.quickbooks_online.core.api.preview_existing_matches",
		freeze: true,
		freeze_message: __("Scanning existing ERPNext records..."),
		callback(response) {
			const matches = response.message || [];
			if (!matches.length) {
				frappe.msgprint(__("No unlinked QuickBooks raw payloads were found. Run Preview Resync or Import All first."));
				return;
			}
			showMatchDialog(matches);
		},
	});
}

function showMatchDialog(matches) {
	const dialog = new frappe.ui.Dialog({
		title: __("Link Existing ERPNext Records"),
		size: "extra-large",
		fields: [{ fieldtype: "HTML", fieldname: "matches_html" }],
	});
	const rows = matches
		.map((match, index) => {
			const auto = match.match && match.match.status === "matched";
			const label = auto
				? `${__("Suggested")}: ${match.match.name} (${match.match.rule})`
				: match.match && match.match.status === "ambiguous"
					? __("Needs manual review")
					: __("No match found");
			return `
				<div class="qbo-match-row" data-index="${index}">
					<div>
						<div class="qbo-entity-name">${match.entity_type} ${match.qbo_id}</div>
						<div class="text-muted">${frappe.utils.escape_html(match.qbo_name || "")}</div>
						<div class="text-muted">${label}</div>
					</div>
					<div>
						<input class="form-control input-sm" data-doctype="${index}" value="${frappe.utils.escape_html(match.erpnext_doctype || "")}" placeholder="${__("ERPNext DocType")}" />
					</div>
					<div>
						<input class="form-control input-sm" data-name="${index}" value="${auto ? frappe.utils.escape_html(match.match.name) : ""}" placeholder="${__("ERPNext Record Name")}" />
					</div>
					<div>
						<label class="checkbox-inline">
							<input type="checkbox" data-apply="${index}" />
							${__("Fill blanks")}
						</label>
					</div>
					<button class="btn btn-xs btn-primary" data-link-index="${index}">${__("Link")}</button>
				</div>
			`;
		})
		.join("");
	dialog.fields_dict.matches_html.$wrapper.html(`<div class="qbo-match-list">${rows}</div>`);
	dialog.fields_dict.matches_html.$wrapper.on("click", "[data-link-index]", (event) => {
		const index = $(event.currentTarget).attr("data-link-index");
		const match = matches[index];
		const erpnextDoctype = dialog.fields_dict.matches_html.$wrapper.find(`[data-doctype='${index}']`).val();
		const erpnextName = dialog.fields_dict.matches_html.$wrapper.find(`[data-name='${index}']`).val();
		const applyQboData = dialog.fields_dict.matches_html.$wrapper.find(`[data-apply='${index}']`).is(":checked") ? 1 : 0;
		if (!erpnextDoctype || !erpnextName) {
			frappe.msgprint(__("Choose an ERPNext DocType and record name before linking."));
			return;
		}
		frappe.call({
			method: "erpnext_enhancements.quickbooks_online.core.api.link_existing_record",
			args: {
				entity_type: match.entity_type,
				qbo_id: match.qbo_id,
				erpnext_doctype: erpnextDoctype,
				erpnext_name: erpnextName,
				apply_qbo_data: applyQboData,
			},
			freeze: true,
			freeze_message: __("Linking record..."),
			callback(linkResponse) {
				frappe.msgprint(__("Created mapping {0}", [linkResponse.message]));
				$(event.currentTarget).closest(".qbo-match-row").remove();
			},
		});
	});
	dialog.show();
}

function syncEntity(entity, qboId) {
	frappe.call({
		method: "erpnext_enhancements.quickbooks_online.core.api.sync_entity",
		args: { entity_type: entity, qbo_id: qboId },
		freeze: true,
		freeze_message: __("Syncing {0}...", [entity]),
		callback(response) {
			frappe.msgprint(__("{0} synced in log {1}", [entity, response.message.sync_log]));
		},
	});
}

function reconcileTransactions() {
	frappe.call({
		method: "erpnext_enhancements.quickbooks_online.core.api.reconcile_transactions",
		freeze: true,
		freeze_message: __("Reconciling imported transactions against QuickBooks..."),
		callback(response) {
			const summary = (response.message || {}).summary || {};
			frappe.msgprint({
				title: __("Transaction Reconciliation"),
				indicator: summary.mismatched || summary.missing ? "orange" : "green",
				message: __(
					"{0} matched, {1} amount mismatch(es), {2} missing ERPNext document(s).",
					[summary.matched || 0, summary.mismatched || 0, summary.missing || 0],
				),
			});
		},
	});
}

function importOpeningBalances() {
	const dialog = new frappe.ui.Dialog({
		title: __("Import Opening Balances from QuickBooks"),
		fields: [
			{
				fieldname: "as_of_date",
				label: __("As of Date"),
				fieldtype: "Date",
				default: frappe.datetime.get_today(),
				reqd: 1,
			},
			{
				fieldname: "auto_submit",
				label: __("Submit the Journal Entry (otherwise leave as a draft to review)"),
				fieldtype: "Check",
				default: 0,
			},
			{
				fieldname: "note",
				fieldtype: "HTML",
				options: `<p class="text-muted small">${__(
					"Builds one balanced opening Journal Entry from the QuickBooks Trial Balance and open customer/vendor balances. Review the draft before submitting.",
				)}</p>`,
			},
		],
		primary_action_label: __("Build Opening Entry"),
		primary_action(values) {
			dialog.hide();
			frappe.call({
				method: "erpnext_enhancements.quickbooks_online.core.api.sync_opening_balances",
				args: { as_of_date: values.as_of_date, auto_submit: values.auto_submit ? 1 : 0 },
				freeze: true,
				freeze_message: __("Building opening balances..."),
				callback(response) {
					const result = response.message || {};
					if (!result.journal_entry) {
						frappe.msgprint(__("No opening balances were found to import."));
						return;
					}
					let message = __("Opening Journal Entry {0} created ({1} lines).", [
						result.journal_entry,
						result.line_count || 0,
					]);
					if ((result.skipped_stock || []).length) {
						message +=
							"<br>" +
							__("Stock accounts excluded (post opening stock via Stock Reconciliation): {0}", [
								result.skipped_stock.join(", "),
							]);
					}
					if ((result.unmapped || []).length) {
						message +=
							"<br>" +
							__("{0} QuickBooks account(s) had a balance but no linked ERPNext account.", [
								result.unmapped.length,
							]);
					}
					frappe.msgprint({
						title: __("Opening Balances"),
						indicator: (result.skipped_stock || []).length || (result.unmapped || []).length ? "orange" : "green",
						message,
					});
				},
			});
		},
	});
	dialog.show();
}
