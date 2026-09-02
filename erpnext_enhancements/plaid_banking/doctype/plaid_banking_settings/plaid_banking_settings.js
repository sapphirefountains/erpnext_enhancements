/**
 * Client script for the Plaid Banking Settings form.
 *
 * This form owns the Bank Balances widget only. Linking a bank is the NATIVE
 * ERPNext flow and does not happen here:
 *  - /app/plaid-settings (ERPNext Integrations): enter Client ID + Secret, pick
 *    the Environment, tick Enabled, save. "Link a new bank account" opens Plaid
 *    Link; on success it asks for a Company, then creates or updates the Bank
 *    record (storing the access token on Bank.plaid_access_token) and creates a
 *    Bank Account named "<Plaid account name> - <Bank>" for every account the
 *    user shared -- plus a new GL Account for any it does not find.
 *  - /app/bank/<name>: "Refresh Plaid Link" (shown once the Bank holds a token)
 *    re-authenticates that one institution in Link update mode.
 *
 * Buttons here delegate to whitelisted methods under
 * erpnext_enhancements.plaid_banking.core.api:
 *  - "Test Connection": checks the native keys, then /item/get per linked bank.
 *  - "Refresh Balances Now": /accounts/balance/get per linked bank, writes the cache.
 *  - "Map Plaid accounts": lists each linked bank's Plaid accounts and stamps the
 *    chosen one onto an EXISTING company Bank Account (integration_id + mask),
 *    so native Link's "<Plaid name> - <Bank>" naming never strands our masters.
 */
const PLAID_API = "erpnext_enhancements.plaid_banking.core.api";

function showResult(frm, m, okText, failText) {
	frappe.show_alert({
		message: m.message || (m.ok ? okText : failText),
		indicator: m.ok ? "green" : "orange",
	});
	frm.reload_doc();
}

function openMappingDialog(overview) {
	const banks = overview.banks || [];
	if (!banks.length) {
		frappe.msgprint(
			__("No bank is linked to Plaid yet. Link one on the native Plaid Settings form first."),
		);
		return;
	}

	const fields = [];
	const rows = [];
	// Masters whose Bank holds no token: native Link names the Bank after Plaid's
	// institution ("KeyBank"), not ours ("Key Bank"), so the masters may sit under a Bank
	// that was never linked. Offered under every linked bank; picking one moves it there.
	const movable = (overview.unlinked_bank_accounts || []).map((ba) => ba.name);
	banks.forEach((bank, bi) => {
		fields.push({ fieldtype: "Section Break", label: bank.bank });
		if (bank.error) {
			fields.push({
				fieldtype: "HTML",
				options: `<div class="text-danger">${frappe.utils.escape_html(bank.error)}</div>`,
			});
			return;
		}
		const already = {};
		(bank.bank_accounts || []).forEach((ba) => {
			if (ba.integration_id) already[ba.integration_id] = ba.name;
		});
		const unmapped = (bank.bank_accounts || [])
			.filter((ba) => !ba.integration_id)
			.map((ba) => ba.name)
			.concat(movable);
		(bank.plaid_accounts || []).forEach((pa, pi) => {
			const label = [pa.name, pa.mask ? `••${pa.mask}` : "", pa.subtype || pa.type || ""]
				.filter(Boolean)
				.join(" ");
			if (already[pa.account_id]) {
				fields.push({
					fieldtype: "HTML",
					options: `<div class="text-muted">${frappe.utils.escape_html(label)} → ${__("already mapped to")} ${frappe.utils.escape_html(already[pa.account_id])}</div>`,
				});
				return;
			}
			const fieldname = `map_${bi}_${pi}`;
			fields.push({
				fieldtype: "Select",
				fieldname,
				label,
				options: ["", ...unmapped].join("\n"),
				description: __(
					"Pick the company Bank Account this Plaid account is, or leave blank to skip. A Bank Account under a different Bank is moved under {0} when mapped.",
					[bank.bank],
				),
			});
			rows.push({ fieldname, bank: bank.bank, account_id: pa.account_id, mask: pa.mask });
		});
		if (!(bank.plaid_accounts || []).length) {
			fields.push({ fieldtype: "HTML", options: `<div class="text-muted">${__("Plaid returned no accounts.")}</div>` });
		}
	});

	const dialog = new frappe.ui.Dialog({
		title: __("Map Plaid accounts to Bank Accounts"),
		fields,
		primary_action_label: __("Map selected"),
		primary_action(values) {
			const chosen = rows.filter((r) => values[r.fieldname]);
			if (!chosen.length) {
				frappe.msgprint(__("Nothing selected."));
				return;
			}
			const seen = {};
			for (const r of chosen) {
				if (seen[values[r.fieldname]]) {
					frappe.msgprint(__("{0} is selected twice.", [values[r.fieldname]]));
					return;
				}
				seen[values[r.fieldname]] = true;
			}
			dialog.hide();
			const run = (i) => {
				if (i >= chosen.length) {
					frappe.show_alert({ message: __("Mapped {0} account(s).", [chosen.length]), indicator: "green" });
					return;
				}
				const r = chosen[i];
				frappe.call({
					method: `${PLAID_API}.map_plaid_account`,
					args: { bank_account: values[r.fieldname], account_id: r.account_id, mask: r.mask, bank: r.bank },
					freeze: true,
					freeze_message: __("Mapping {0}…", [values[r.fieldname]]),
					callback() {
						run(i + 1);
					},
				});
			};
			run(0);
		},
	});
	dialog.show();
}

frappe.ui.form.on("Plaid Banking Settings", {
	refresh(frm) {
		frm.set_intro(
			__(
				"Linking happens in the native {0}: enter the client id / secret, choose the environment, tick Enabled, then use <b>Link a new bank account</b>. Re-authenticate a bank from its own {1} form (<b>Refresh Plaid Link</b>). This page only switches the Bank Balances widget on and maps the linked Plaid accounts onto the existing company Bank Accounts.",
				[
					`<a href="/app/plaid-settings">${__("Plaid Settings")}</a>`,
					`<a href="/app/bank">${__("Bank")}</a>`,
				],
			),
			"blue",
		);

		frm.add_custom_button(__("Test Connection"), () => {
			frappe.call({
				method: `${PLAID_API}.test_connection`,
				freeze: true,
				freeze_message: __("Testing the Plaid connection…"),
				callback(r) {
					showResult(frm, r.message || {}, __("Connection OK."), __("Connection failed."));
				},
			});
		});

		frm.add_custom_button(__("Refresh Balances Now"), () => {
			frappe.call({
				method: `${PLAID_API}.refresh_now`,
				freeze: true,
				freeze_message: __("Refreshing balances…"),
				callback(r) {
					showResult(frm, r.message || {}, __("Balances refreshed."), __("Could not refresh."));
				},
			});
		});

		frm.add_custom_button(__("Map Plaid accounts"), () => {
			frappe.call({
				method: `${PLAID_API}.mapping_overview`,
				freeze: true,
				freeze_message: __("Reading the linked banks…"),
				callback(r) {
					openMappingDialog(r.message || {});
				},
			});
		});
	},
});
