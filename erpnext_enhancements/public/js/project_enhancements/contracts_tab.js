/**
 * @file Contracts tab — every contract on a Project or a Customer, readable in place.
 * @description
 * Renders `custom_contracts_html` on both host forms (Project: between Budget
 * and Costing; Customer: after Accounting) as one list answering one question —
 * what are we committed to here, and what does it say?
 *
 * Two kinds of document share the list, because "my contracts" is one question
 * even though the data model has two answers:
 *
 *  - **Project Contract** — the signed agreement. Opens in place to its full
 *    legal text, the same document the print format puts on paper (a signed one
 *    shows its executed instrument rather than a fresh render).
 *  - **Sapphire Maintenance Contract** — the operational schedule. It carries no
 *    legal language of its own; when it was mapped from a signed Maintenance
 *    Services Agreement the row opens *that*, and when it was created directly
 *    the row says so plainly and offers to raise the agreement, rather than
 *    rendering contract text nobody signed.
 *
 * Bodies are fetched only when a row is opened, and cached on the row after.
 * Rendering and styling both come from the server (contract_viewer.js), so an
 * agreement cannot read differently here than it does in the customer's PDF.
 *
 * Loaded via hooks.py `doctype_js` for Project and Customer.
 */

(function () {
	const M =
		"erpnext_enhancements.project_enhancements.doctype.project_contract.project_contract";

	// Status → indicator colour, across both doctypes. Same vocabulary the forms
	// themselves use, so a status means one thing wherever it is read.
	const STATUS_COLOR = {
		// Project Contract
		Draft: "gray",
		"Out for Signature": "orange",
		Signed: "green",
		Void: "red",
		// Sapphire Maintenance Contract
		Active: "green",
		Expired: "orange",
		Canceled: "red",
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
			.ee-contract.not-readable .ee-contract-head { cursor: default; }
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
			.ee-contract-actions {
				padding: 6px 12px; border-top: 1px solid var(--border-color);
				display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
			}
			.ee-contract-actions .ee-contract-meta { flex: 1 1 auto; }
			.ee-contract-empty { color: var(--text-muted); padding: 6px 0; }
		</style>
	`;

	const esc = (value) => frappe.utils.escape_html(String(value == null ? "" : value));

	function status_indicator(row) {
		const color = STATUS_COLOR[row.status] || "gray";
		return `<span class="indicator-pill ${color}"><span>${esc(
			row.status || __("Draft")
		)}</span></span>`;
	}

	function meta_line(row) {
		const bits = [];
		if (row.party_display) bits.push(esc(row.party_display));
		bits.push(esc(row.name));
		if (row.revision) bits.push(__("Revision {0}", [row.revision]));

		if (row.doctype_name === "Sapphire Maintenance Contract") {
			if (row.default_frequency) bits.push(esc(row.default_frequency));
			if (row.invoicing_frequency) bits.push(__("billed {0}", [esc(row.invoicing_frequency)]));
			if (row.start_date) {
				bits.push(
					row.end_date
						? __("{0} to {1}", [
								frappe.datetime.str_to_user(row.start_date),
								frappe.datetime.str_to_user(row.end_date),
						  ])
						: __("from {0}", [frappe.datetime.str_to_user(row.start_date)])
				);
			}
		} else if (row.signed_on) {
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
				? `<span class="ee-contract-value">${esc(row.value_label)}: ${format_currency(
						row.value
				  )}</span>`
				: "";
		// An operational contract with no agreement behind it has nothing to
		// open; saying so beats an expander onto an empty panel.
		const caret = row.readable ? "&#9656;" : "&nbsp;";
		return `
			<div class="ee-contract ${row.readable ? "" : "not-readable"}"
					data-name="${esc(row.name)}"
					data-doctype="${esc(row.doctype_name)}"
					data-agreement="${esc(row.agreement || row.name)}"
					data-readable="${row.readable ? 1 : 0}"
					data-template-key="${esc(row.template_key || "")}">
				<div class="ee-contract-head">
					<span class="ee-contract-caret">${caret}</span>
					<span class="ee-contract-title">${esc(row.type_label || "")}</span>
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
		return `<div class="ee-contracts-group-title">${esc(title)}</div>${rows
			.map(contract_card)
			.join("")}`;
	}

	/** Fetch (once) and reveal the full agreement for a card. */
	function toggle_body($card) {
		if ($card.attr("data-readable") !== "1") return;
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
			.fetch({ name: $card.attr("data-agreement") }, { freeze: false })
			.then((payload) => {
				if (!payload) return;
				erpnext_enhancements.contract_viewer.paint($body, payload, {
					template_key: $card.attr("data-template-key"),
				});
				$card.data("loaded", true);
			})
			.catch(() => {
				// The server has already said why; leave an honest line rather
				// than an empty panel.
				$body.html(
					`<div class="text-muted">${__("This agreement could not be rendered.")}</div>`
				);
			});
	}

	function wire(frm, $wrapper) {
		$wrapper.find(".ee-contract-head").on("click", function () {
			toggle_body($(this).closest(".ee-contract"));
		});

		$wrapper.find(".ee-contract").each(function () {
			const $card = $(this);
			const name = $card.attr("data-name");
			const doctype = $card.attr("data-doctype");
			const $actions = $card.find(".ee-contract-actions");

			// An operational contract with no agreement behind it: say what is
			// missing, and offer the thing that fixes it.
			if ($card.attr("data-readable") !== "1") {
				$actions.append(
					`<span class="text-muted" style="font-size:12px">${__(
						"Schedule only — no signed agreement linked."
					)}</span>`
				);
				if (frappe.boot.ee_process_automation) {
					$actions.append(
						$(
							`<button class="btn btn-xs btn-default">${__("Generate Agreement")}</button>`
						).on("click", (e) => {
							e.stopPropagation();
							explain_agreement_route(frm);
						})
					);
				}
			}

			$actions.append(
				$(`<button class="btn btn-xs btn-default">${__("Open")}</button>`).on("click", (e) => {
					e.stopPropagation();
					frappe.set_route("Form", doctype, name);
				})
			);
		});
	}

	/**
	 * Point at the Generate Contract flow already on this form.
	 *
	 * Deliberately an explanation rather than a one-click generate: issuing an
	 * agreement is a decision about what the customer is asked to sign, and the
	 * dialog on this form is where that decision belongs.
	 */
	function explain_agreement_route(frm) {
		frappe.msgprint({
			title: __("Generate the agreement"),
			message: __(
				"Use <b>Create &rsaquo; Generate Contract</b> on this form and choose <b>Maintenance Services Agreement</b>. Once it is signed, set it on the maintenance contract's <b>Project Contract</b> field — its full text will then open here."
			),
			indicator: "blue",
		});
	}

	function empty_state(message) {
		return `${STYLE}<div class="ee-contracts"><div class="ee-contract-empty">${message}</div></div>`;
	}

	function render(frm) {
		const field = frm.get_field("custom_contracts_html");
		if (!field || !field.$wrapper) return; // field not provisioned on this site
		if (frm.is_new()) {
			field.$wrapper.html(empty_state(__("Save this record to see its contracts.")));
			return;
		}

		frappe
			.call({
				method: `${M}.get_contracts`,
				args: { source_doctype: frm.doctype, source_name: frm.doc.name },
			})
			.then((r) => {
				const rows = r.message || [];
				if (!rows.length) {
					field.$wrapper.html(
						empty_state(
							__("No contracts here yet.") +
								(frappe.boot.ee_process_automation
									? " " + __("Use Create &rsaquo; Generate Contract to issue one.")
									: "")
						)
					);
					return;
				}

				const ours = rows.filter((row) => !row.is_subcontract);
				const subs = rows.filter((row) => row.is_subcontract);
				field.$wrapper.html(
					`${STYLE}<div class="ee-contracts">${group(
						__("Contracts & Agreements"),
						ours
					)}${group(__("Subcontractor Agreements"), subs)}</div>`
				);
				wire(frm, field.$wrapper);
			});
	}

	["Project", "Customer"].forEach((doctype) => {
		frappe.ui.form.on(doctype, {
			refresh(frm) {
				render(frm);
			},
		});
	});
})();
