/**
 * @file Project form — Contracts tab: every agreement on the job, readable in place.
 * @description
 * Renders `custom_contracts_html` (Contracts tab, between Budget and Costing)
 * as the list of Project Contracts issued on this project — customer
 * agreements and subcontractor paper alike, grouped apart because they are
 * commitments in opposite directions.
 *
 * Each row opens **in place** to the complete agreement: the Contract
 * Template's legal language with this contract's data filled in, the same
 * document the print format puts on paper (a signed contract shows its
 * executed instrument instead of a fresh render). Bodies are fetched only when
 * a row is opened — listing a project with a dozen agreements costs one small
 * query — and cached on the row afterwards.
 *
 * Rendering and styling both come from the server (see contract_viewer.js), so
 * nothing here can make an agreement read differently on screen than it does
 * in the customer's PDF.
 *
 * Loaded via hooks.py `doctype_js["Project"]`.
 */

(function () {
	const M =
		"erpnext_enhancements.project_enhancements.doctype.project_contract.project_contract";

	// Status → indicator colour. Deliberately the same vocabulary the Project
	// Contract form uses, so a status means one thing wherever it is read.
	const STATUS_COLOR = {
		Draft: "gray",
		"Out for Signature": "orange",
		Signed: "green",
		Void: "red",
	};

	const STYLE = `
		<style>
			.ee-contracts { padding: 4px 0; }
			.ee-contracts-group-title {
				font-size: 11px; font-weight: 600; letter-spacing: 0.04em; text-transform: uppercase;
				color: var(--text-muted); margin: 12px 0 6px;
			}
			.ee-contracts-group-title:first-child { margin-top: 0; }
			.ee-contract {
				border: 1px solid var(--border-color); border-radius: 8px;
				background: var(--fg-color); margin-bottom: 8px; overflow: hidden;
			}
			.ee-contract-head {
				display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
				padding: 10px 12px; cursor: pointer;
			}
			.ee-contract-head:hover { background: var(--bg-color); }
			.ee-contract-caret { color: var(--text-muted); width: 10px; flex: 0 0 10px; }
			.ee-contract-title { font-weight: 600; color: var(--heading-color); }
			.ee-contract-meta { font-size: 12px; color: var(--text-muted); }
			.ee-contract-spacer { flex: 1 1 auto; }
			.ee-contract-value { font-size: 12px; color: var(--heading-color); white-space: nowrap; }
			/* The agreement draws its own white sheet (contract_viewer.js);
			   this panel only scrolls it. */
			.ee-contract-body {
				display: none; border-top: 1px solid var(--border-color);
				padding: 12px; max-height: 60vh; overflow-y: auto;
			}
			.ee-contract.open .ee-contract-body { display: block; }
			.ee-contract-actions { padding: 6px 12px; border-top: 1px solid var(--border-color); }
			.ee-contract-empty { color: var(--text-muted); padding: 6px 0; }
		</style>
	`;

	function status_indicator(row) {
		const color = STATUS_COLOR[row.status] || "gray";
		const label = frappe.utils.escape_html(row.status || __("Draft"));
		return `<span class="indicator-pill ${color}"><span>${label}</span></span>`;
	}

	function meta_line(row) {
		const bits = [];
		if (row.party_display || row.party) {
			bits.push(frappe.utils.escape_html(row.party_display || row.party));
		}
		bits.push(frappe.utils.escape_html(row.name));
		if (row.revision) bits.push(__("Revision {0}", [row.revision]));
		if (row.signed_on) {
			bits.push(__("signed {0}", [frappe.datetime.str_to_user(row.signed_on)]));
		} else if (row.contract_date) {
			bits.push(__("dated {0}", [frappe.datetime.str_to_user(row.contract_date)]));
		}
		if (row.executed) bits.push(__("signed copy on file"));
		return bits.join(" &middot; ");
	}

	function contract_card(row) {
		const value =
			row.value_label && row.value
				? `<span class="ee-contract-value">${frappe.utils.escape_html(
						row.value_label
				  )}: ${format_currency(row.value)}</span>`
				: "";
		return `
			<div class="ee-contract" data-name="${frappe.utils.escape_html(row.name)}"
					data-template-key="${frappe.utils.escape_html(row.template_key || "")}">
				<div class="ee-contract-head">
					<span class="ee-contract-caret">&#9656;</span>
					<span class="ee-contract-title">${frappe.utils.escape_html(row.type_label || "")}</span>
					${status_indicator(row)}
					<span class="ee-contract-spacer"></span>
					${value}
				</div>
				<div class="ee-contract-actions">
					<span class="ee-contract-meta">${meta_line(row)}</span>
				</div>
				<div class="ee-contract-body"></div>
			</div>
		`;
	}

	function group(title, rows) {
		if (!rows.length) return "";
		return `<div class="ee-contracts-group-title">${frappe.utils.escape_html(title)}</div>${rows
			.map(contract_card)
			.join("")}`;
	}

	/** Fetch (once) and reveal the full agreement for a card. */
	function toggle_body($card) {
		const name = $card.attr("data-name");
		const $body = $card.find(".ee-contract-body");
		const $caret = $card.find(".ee-contract-caret");

		if ($card.hasClass("open")) {
			$card.removeClass("open");
			$caret.html("&#9656;");
			return;
		}
		$card.addClass("open");
		$caret.html("&#9662;");

		if ($card.data("loaded")) return;
		$body.html(`<div class="text-muted">${__("Loading the agreement…")}</div>`);
		erpnext_enhancements.contract_viewer
			.fetch({ name: name }, { freeze: false })
			.then((payload) => {
				if (!payload) return;
				erpnext_enhancements.contract_viewer.paint($body, payload, {
					template_key: $card.attr("data-template-key"),
				});
				$card.data("loaded", true);
			})
			.catch(() => {
				// The server has already told the user why; leave the card open
				// with an honest line rather than an empty panel.
				$body.html(
					`<div class="text-muted">${__("This agreement could not be rendered.")}</div>`
				);
			});
	}

	function render(frm) {
		const field = frm.get_field("custom_contracts_html");
		if (!field || !field.$wrapper) return; // field not provisioned on this site
		if (frm.is_new()) {
			field.$wrapper.html(
				`${STYLE}<div class="ee-contracts"><div class="ee-contract-empty">${__(
					"Save the project to see its contracts."
				)}</div></div>`
			);
			return;
		}

		frappe.call({ method: `${M}.get_project_contracts`, args: { project: frm.doc.name } }).then(
			(r) => {
				const rows = r.message || [];
				if (!rows.length) {
					field.$wrapper.html(
						`${STYLE}<div class="ee-contracts"><div class="ee-contract-empty">${__(
							"No contracts on this project yet."
						)}${
							frappe.boot.ee_process_automation
								? " " + __("Use Create &rsaquo; Generate Contract to issue one.")
								: ""
						}</div></div>`
					);
					return;
				}

				const customer = rows.filter((row) => !row.is_subcontract);
				const subs = rows.filter((row) => row.is_subcontract);
				field.$wrapper.html(
					`${STYLE}<div class="ee-contracts">${group(__("Customer Agreements"), customer)}${group(
						__("Subcontractor Agreements"),
						subs
					)}</div>`
				);

				field.$wrapper.find(".ee-contract-head").on("click", function () {
					toggle_body($(this).closest(".ee-contract"));
				});
				// The contract number opens the record; the row itself opens the
				// text, so reading never costs a page navigation.
				field.$wrapper.find(".ee-contract").each(function () {
					const name = $(this).attr("data-name");
					$(this)
						.find(".ee-contract-actions")
						.append(
							$(`<button class="btn btn-xs btn-default pull-right">${__("Open")}</button>`).on(
								"click",
								(e) => {
									e.stopPropagation();
									frappe.set_route("Form", "Project Contract", name);
								}
							)
						);
				});
			}
		);
	}

	frappe.ui.form.on("Project", {
		refresh(frm) {
			render(frm);
		},
	});
})();
