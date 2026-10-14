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
 *      * Disconnect QuickBooks -> confirm, then `disconnect` (revoke at Intuit + clear
 *                               tokens), then reload (disconnectQuickBooks()).
 *      * Import All          -> confirm, then `import_all` (runImportAll()).
 *      * Preview Resync      -> `preview_resync`, show a summary, then optionally
 *                               `run_resync` to overwrite QBO-owned fields (previewResync()).
 *      * Retry Failed        -> `retry_failed` (retryFailed()).
 *      * Record Matching     -> routes to the quickbooks-record-matching page, the
 *                               accountant's review queue. Its predecessor here was a
 *                               dialog that listed only QBO records with NO mapping
 *                               row -- after a full import, none of them.
 *
 * The page is otherwise stateless; all data comes from the RPCs above.
 *
 * The status load and the resync-preview poll run on sf_kit's quiet path (v1.642.0): the load
 * draws its loading, empty, error and no-access states from the kit where the sync logs go, and
 * the poll, which no one is watching, never puts up a frappe dialog (it retries a network or
 * server failure, and stops on a 4xx or a lapsed session). The buttons keep
 * frappe.call and its dialogs: those failures answer a click.
 */
frappe.pages["quickbooks-online-dashboard"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("QuickBooks Online"),
		single_column: true,
	});

	page.set_primary_action(__("Import All"), () => runImportAll(), "download");
	page.add_action_item(__("Open Settings"), () => frappe.set_route("Form", "QuickBooks Online Settings"));
	page.add_action_item(__("Record Matching"), () => frappe.set_route("quickbooks-record-matching"));
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
					<button class="btn btn-default" data-action="disconnect">${__("Disconnect QuickBooks")}</button>
				<button class="btn btn-default" data-action="matches">${__("Record Matching")}</button>
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
	root.on("click", "[data-action='disconnect']", () => disconnectQuickBooks());
	root.on("click", "[data-action='matches']", () => frappe.set_route("quickbooks-record-matching"));
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
	qbo_root = root;
	refresh(root);
};

// sf_kit from the shared loader (public/js/sf_kit_loader.js, in the global bundle). It never
// rejects: a kit that never arrived resolves to a plain stand-in (frappe.call and a line of plain
// text), so the dashboard carries on. A Desk opened before the deploy that added the loader has no
// sf_kit_ready yet, and asks frappe.require for the kit itself.
function qboKit() {
	if (typeof window.sf_kit_ready === "function") return window.sf_kit_ready();
	return new Promise((resolve) =>
		frappe.require(["sf_kit.bundle.js", "sf_kit.bundle.css"], () => resolve(window.sf_kit)),
	);
}

// The dashboard on screen, for the resync poll: a lapsed session ends the poll with the same
// signed-out panel the status load draws where the sync logs go.
let qbo_root = null;

function qboSignedOut(sf, list) {
	sf.state.render(list, "error", {
		title: __("You have been signed out."),
		body: __("Reload the page to sign in again."),
		action_label: __("Reload"),
		on_action: () => window.location.reload(),
	});
}

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
	"CreditMemo",
	"SalesReceipt",
	"RefundReceipt",
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

// Loaded once, on the way in: the tiles keep their "-" and the log list carries the state.
function refresh(root) {
	qboKit().then((sf) => {
		const list = root.find(".qbo-log-list");
		sf.state.render(list, "loading", { title: __("Loading QuickBooks status…") });
		sf.call("erpnext_enhancements.quickbooks_online.core.api.get_dashboard_status", {}, { quiet: true }).then(
			(message) => {
				const data = message || {};
				const settings = data.settings || {};
				root.find("[data-field='status']").text(settings.status || "-");
				root.find("[data-field='environment']").text(settings.environment || "-");
				root.find("[data-field='realm_id']").text(settings.realm_id || "-");
				root.find("[data-field='failed_records']").text(data.failed_records || 0);
				renderLogs(sf, root, data.latest_logs || []);
			},
			(err) => {
				err = err || {};
				if (err.session_expired) {
					qboSignedOut(sf, list);
				} else if (err.status === 403) {
					sf.state.render(list, "no-access", {
						title: __("You do not have access to the QuickBooks Online dashboard."),
						body: err.message,
					});
				} else {
					sf.state.render(list, "error", {
						title: __("Could not load the QuickBooks status."),
						body: err.message,
						on_action: () => refresh(root),
					});
				}
			},
		);
	});
}

// Every value in a row is the server's, so every one goes through the kit's escape (v1.642.0).
function renderLogs(sf, root, logs) {
	const esc = sf.esc;
	const list = root.find(".qbo-log-list");
	list.empty();
	if (!logs.length) {
		sf.state.render(list, "empty", {
			title: __("No sync logs yet."),
			body: __("Import All or sync a single entity above, and its log appears here."),
		});
		return;
	}
	logs.forEach((log) => {
		$(`
			<div class="qbo-log-row">
				<div>
					<a data-route="Form/QuickBooks Sync Log/${esc(log.name)}">${esc(log.name)}</a>
					<div class="text-muted">${esc(log.sync_type || "")} ${esc(log.entity_type || "")}</div>
				</div>
				<div>${esc(log.status)}</div>
				<div>
					${__("C")} ${esc(log.created_count || 0)} /
					${__("L")} ${esc(log.linked_count || 0)} /
					${__("R")} ${esc(log.manual_review_count || 0)} /
					${__("X")} ${esc(log.conflict_count || 0)} /
					${__("I")} ${esc(log.ignored_count || 0)}
				</div>
			</div>
		`).appendTo(list);
	});
}

// No environment is sent: start_oauth then connects to the one stored on QuickBooks Online
// Settings. This used to read it first with frappe.db.get_single_value only to pass it straight
// back, and that read refuses anyone without read on the Settings with "No permission for
// QuickBooks Online Settings" (v1.592.1). The Settings form still sends its own unsaved choice.
function connectQuickBooks() {
	frappe.call({
		method: "erpnext_enhancements.quickbooks_online.core.api.start_oauth",
		callback(response) {
			const url = response.message && response.message.authorization_url;
			if (url) {
				window.location.href = url;
			}
		},
	});
}

function disconnectQuickBooks() {
	frappe.confirm(
		__(
			"Disconnect from QuickBooks Online? This revokes the connection at Intuit and clears the stored tokens. You can reconnect at any time.",
		),
		() => {
			frappe.call({
				method: "erpnext_enhancements.quickbooks_online.core.api.disconnect",
				freeze: true,
				freeze_message: __("Disconnecting from QuickBooks Online..."),
				callback() {
					frappe.show_alert({ message: __("Disconnected from QuickBooks Online."), indicator: "orange" });
					location.reload();
				},
			});
		},
	);
}

function runImportAll() {
	frappe.confirm(
		__(
			"Import accounting-core QuickBooks Online data now? This runs in the background and can take a while for large companies.",
		),
		() => {
			frappe.call({
				method: "erpnext_enhancements.quickbooks_online.core.api.import_all",
				callback(response) {
					const status = (response.message || {}).status;
					if (status === "already_running") {
						frappe.msgprint(
							__("A QuickBooks import is already running. Watch its progress under Recent Sync Logs."),
						);
						return;
					}
					frappe.msgprint(
						__(
							"QuickBooks import started in the background. Progress appears under Recent Sync Logs (this can take a while for large companies).",
						),
					);
					setTimeout(() => location.reload(), 2500);
				},
			});
		},
	);
}

function previewResync() {
	// The preview pages the whole company (like a full import), so it runs as a
	// background job; we poll its Sync Log and, once complete, offer Run Resync.
	frappe.call({
		method: "erpnext_enhancements.quickbooks_online.core.api.preview_resync",
		callback(response) {
			const previewId = (response.message || {}).preview_id;
			if (!previewId) {
				frappe.msgprint(__("Could not start the resync preview."));
				return;
			}
			frappe.show_alert(
				{
					message: __("Resync preview building in the background (Sync Log {0})...", [previewId]),
					indicator: "blue",
				},
				7,
			);
			pollResyncPreview(previewId, 0);
		},
	});
}

function pollResyncPreview(previewId, attempts) {
	// ~3s cadence, ~30 min cap; the job keeps running even if we stop polling.
	if (attempts > 600) {
		frappe.msgprint(
			__(
				"The resync preview is still running. Review Sync Log {0} and re-run Preview Resync once it has completed.",
				[previewId],
			),
		);
		return;
	}
	// Quiet (v1.642.0): a poll every 3 s must not put up a frappe dialog for each failed tick. Only
	// a failure that can pass is retried, as an attempt: the network (status 0) or the server
	// (5xx). A 4xx is an answer rather than a hiccup (a refusal, a log that is not there) and asking
	// again every 3 s cannot change it, so the poll stops and says why in a toast. A lapsed session
	// stops it too, with the signed-out panel the status load draws.
	qboKit().then((sf) =>
		sf.call("erpnext_enhancements.quickbooks_online.core.api.get_sync_log_summary", { name: previewId }, { quiet: true }).then(
			(message) => {
				const data = message || {};
				if (data.status === "Completed") {
					offerRunResync(previewId, data.summary || {});
				} else if (data.status === "Failed") {
					frappe.msgprint({
						title: __("Resync preview failed"),
						indicator: "red",
						message: frappe.utils.escape_html(data.error_message || __("See Sync Log {0}.", [previewId])),
					});
				} else {
					setTimeout(() => pollResyncPreview(previewId, attempts + 1), 3000);
				}
			},
			(err) => {
				err = err || {};
				const status = Number(err.status) || 0;
				if (err.session_expired) {
					if (qbo_root) qboSignedOut(sf, qbo_root.find(".qbo-log-list"));
					sf.toast(__("You have been signed out. Sign in again and review Sync Log {0}.", [previewId]), { tone: "warning" });
					return;
				}
				if (status === 0 || status >= 500) {
					setTimeout(() => pollResyncPreview(previewId, attempts + 1), 3000);
					return;
				}
				sf.toast(
					__("Stopped checking the resync preview: {0} Review Sync Log {1}.", [
						err.message || __("The server refused this request."),
						previewId,
					]),
					{ tone: "warning" },
				);
			},
		),
	);
}

function offerRunResync(previewId, summary) {
	const message = __("Preview {0}: {1} creates, {2} updates, {3} deletes, {4} conflicts, {5} ignored.", [
		previewId,
		summary.created || 0,
		summary.updated || 0,
		summary.deleted || 0,
		summary.conflicts || 0,
		summary.ignored || 0,
	]);
	frappe.confirm(message + "<br>" + __("Run overwrite resync for QuickBooks-owned fields?"), () => {
		frappe.call({
			method: "erpnext_enhancements.quickbooks_online.core.api.run_resync",
			args: { preview_id: previewId },
			callback() {
				frappe.msgprint(
					__("Resync started in the background. Progress appears under Recent Sync Logs."),
				);
			},
		});
	});
}

function retryFailed() {
	frappe.call({
		method: "erpnext_enhancements.quickbooks_online.core.api.retry_failed",
		callback() {
			frappe.msgprint(
				__("Retry started in the background. Progress appears under Recent Sync Logs."),
			);
		},
	});
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
