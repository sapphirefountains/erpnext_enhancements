/**
 * Client script for the Plaid Banking Settings form.
 *
 * This form owns the Bank Balances widget only. Linking a bank is the NATIVE
 * ERPNext flow and does not happen here:
 *  - /desk/plaid-settings (ERPNext Integrations): enter Client ID + Secret, pick
 *    the Environment, tick Enabled, save. "Link a new bank account" opens Plaid
 *    Link; on success it asks for a Company, then creates or updates the Bank
 *    record (storing the access token on Bank.plaid_access_token) and creates a
 *    Bank Account named "<Plaid account name> - <Bank>" for every account the
 *    user shared -- plus a new GL Account for any it does not find.
 *  - /desk/bank/<name>: "Refresh Plaid Link" (shown once the Bank holds a token)
 *    re-authenticates that one institution in Link update mode.
 *
 * Buttons here delegate to whitelisted methods under
 * erpnext_enhancements.plaid_banking.core.api:
 *  - "Test Connection": checks the native keys, then /item/get per linked bank.
 *  - "Refresh Balances Now": /accounts/balance/get per linked bank, writes the cache.
 *  - "Map Plaid accounts": lists each linked bank's Plaid accounts and puts each on
 *    the company account it really is (apply_plaid_mappings): an existing master,
 *    or -- for a credit card, which has none -- the card's Liability GL account,
 *    which gets a Bank Account of its own. The duplicate a first native Link always
 *    creates is folded in, and one "Feed starts on" date becomes every mapped
 *    account's Last Integration Date.
 */
const PLAID_API = "erpnext_enhancements.plaid_banking.core.api";

function showResult(frm, m, okText, failText) {
	frappe.show_alert({
		message: m.message || (m.ok ? okText : failText),
		indicator: m.ok ? "green" : "orange",
	});
	frm.reload_doc();
}

// The Plaid account's label as the dialog and the results show it: name, ••mask, subtype.
function plaidLabel(pa) {
	return [pa.name, pa.mask ? `••${pa.mask}` : "", pa.subtype || pa.type || ""].filter(Boolean).join(" ");
}

const ACTION_LABELS = {
	mapped: __("Mapped"),
	absorbed: __("Linked; the duplicate Plaid Link made was removed"),
	"already mapped": __("Already on this account; start date updated"),
};

function showMappingResults(chosen, payload) {
	const results = (payload && payload.results) || [];
	const esc = frappe.utils.escape_html;
	const rows = results.map((res, i) => {
		const label = (chosen[i] && chosen[i].label) || res.account_id || "";
		let outcome;
		if (res.ok) {
			const parts = [
				`${ACTION_LABELS[res.action] || esc(res.action || "")} → <b>${esc(res.bank_account || "")}</b>`,
			];
			if (res.card_master_created) parts.push(__("card Bank Account created"));
			if (res.gl_account_type_set) parts.push(__("its GL account's Account Type set to Bank"));
			if (res.last_integration_date) parts.push(__("feed starts {0}", [esc(res.last_integration_date)]));
			if (res.gl_account_note) parts.push(esc(res.gl_account_note));
			outcome = parts.join("; ");
		} else {
			outcome = `<span class="text-danger">${__("Not changed")}: ${esc(res.message || "")}</span>`;
		}
		return `<tr><td>${esc(label)}</td><td>${outcome}</td></tr>`;
	});
	const failed = results.filter((r) => !r.ok).length;
	frappe.msgprint({
		title: failed ? __("Mapped with {0} problem(s)", [failed]) : __("Plaid accounts mapped"),
		indicator: failed ? "orange" : "green",
		message: `<table class="table table-bordered table-sm"><tbody>${rows.join("")}</tbody></table>`,
		wide: true,
	});
}

function openMappingDialog(overview) {
	const banks = overview.banks || [];
	if (!banks.length) {
		frappe.msgprint(
			__("No bank is linked to Plaid yet. Link one on the native Plaid Settings form first."),
		);
		return;
	}

	const fields = [
		{
			fieldtype: "Date",
			fieldname: "feed_start",
			label: __("Feed starts on"),
			reqd: 1,
			default: frappe.datetime.month_start(),
			description: __(
				"Written to each account you map here as its Last Integration Date: the first sync pulls Plaid transactions from this day on. Plaid keeps about 90 days of history. Not a future date.",
			),
		},
	];
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
		// A master already carrying a Plaid id is done; so is a Link-created duplicate,
		// which is never itself a target.
		const unmapped = (bank.bank_accounts || [])
			.filter((ba) => !ba.integration_id)
			.map((ba) => ba.name)
			.concat(movable);
		(bank.plaid_accounts || []).forEach((pa, pi) => {
			const label = plaidLabel(pa);
			if (pa.holder && !pa.holder_link_created) {
				fields.push({
					fieldtype: "HTML",
					options: `<div class="text-muted">${frappe.utils.escape_html(label)} → ${__("already mapped to")} ${frappe.utils.escape_html(pa.holder)}</div>`,
				});
				return;
			}
			const row = { bank: bank.bank, account_id: pa.account_id, mask: pa.mask, label };
			row.map_field = `map_${bi}_${pi}`;
			fields.push({
				fieldtype: "Select",
				fieldname: row.map_field,
				label,
				options: ["", ...unmapped].join("\n"),
				description: pa.holder_link_created
					? __(
							"Plaid Link created {0} for this account. Pick the company Bank Account it really is: the link moves there, and the duplicate (with its GL account, when unused) is deleted.",
							[frappe.utils.escape_html(pa.holder)],
						)
					: __(
							"Pick the company Bank Account this Plaid account is, or leave blank to skip. A Bank Account under a different Bank is moved under {0} when mapped.",
							[bank.bank],
						),
			});
			if (pa.type === "credit") {
				row.card_field = `card_${bi}_${pi}`;
				fields.push({
					fieldtype: "Link",
					fieldname: row.card_field,
					options: "Account",
					label: __("…or this card's GL account"),
					default: pa.suggested_card_gl_account || "",
					get_query: () => ({ filters: { root_type: "Liability", is_group: 0, disabled: 0 } }),
					description: __(
						"Cards have no Bank Account yet. Picking the card's Liability account sets its Account Type to Bank (ERPNext requires it) and creates the card's Bank Account with Is Credit Card ticked.",
					),
				});
			}
			rows.push(row);
		});
		if (!(bank.plaid_accounts || []).length) {
			fields.push({ fieldtype: "HTML", options: `<div class="text-muted">${__("Plaid returned no accounts.")}</div>` });
		}
	});

	const dialog = new frappe.ui.Dialog({
		title: __("Map Plaid accounts to Bank Accounts"),
		size: "large",
		fields,
		primary_action_label: __("Map selected"),
		primary_action(values) {
			const chosen = [];
			const seen = {};
			for (const r of rows) {
				const bank_account = values[r.map_field] || null;
				const card_gl_account = (r.card_field && values[r.card_field]) || null;
				if (bank_account && card_gl_account) {
					frappe.msgprint(__("{0}: pick a Bank Account or a card's GL account, not both.", [r.label]));
					return;
				}
				const target = bank_account || card_gl_account;
				if (!target) continue;
				if (seen[target]) {
					frappe.msgprint(__("{0} is selected twice.", [target]));
					return;
				}
				seen[target] = true;
				chosen.push({
					bank: r.bank,
					account_id: r.account_id,
					mask: r.mask,
					bank_account,
					card_gl_account,
					label: r.label,
				});
			}
			if (!chosen.length) {
				frappe.msgprint(__("Nothing selected."));
				return;
			}
			if (values.feed_start > frappe.datetime.get_today()) {
				frappe.msgprint(__("Feed starts on can't be a future date."));
				return;
			}
			dialog.hide();
			frappe
				.call({
					method: `${PLAID_API}.apply_plaid_mappings`,
					args: {
						rows: chosen.map(({ label, ...row }) => row),
						start_date: values.feed_start,
					},
					freeze: true,
					freeze_message: __("Mapping {0} account(s)…", [chosen.length]),
				})
				.then((r) => showMappingResults(chosen, r && r.message));
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
					`<a href="/desk/plaid-settings">${__("Plaid Settings")}</a>`,
					`<a href="/desk/bank">${__("Bank")}</a>`,
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
