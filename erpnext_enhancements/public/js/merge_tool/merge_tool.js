/**
 * Document Merge tool — global "Merge into…" form button + list-view bulk merge.
 *
 * Targets: every desk form and list view, any doctype (global).
 * Loaded via: erpnext_enhancements.bundle.js (global desk bundle).
 * Server: erpnext_enhancements.document_merge (get_merge_preview / perform_merge).
 *
 * Consolidates a duplicate ("loser") into the record you keep ("survivor"):
 * every reference is repointed at the survivor, the survivor's blank fields are
 * backfilled from the loser, and the loser is deleted. The open form is always
 * the SURVIVOR — you pick the duplicate to absorb (a Swap control flips it). A
 * mandatory side-by-side preview shows exactly what is kept vs. discarded vs.
 * backfilled, and a typed confirmation guards the irreversible delete.
 *
 * Gating: bails unless `frappe.boot.ee_merge_tool` is truthy (the
 * `document_merge_enabled` switch on ERPNext Enhancements Settings, shipped via
 * boot.boot_session) AND the user is a System Manager. The server endpoints
 * enforce the same — this is only about showing the button.
 */
(function () {
	if (window.__ee_merge_tool_loaded) return;
	window.__ee_merge_tool_loaded = true;

	function is_enabled() {
		return !!(frappe.boot && frappe.boot.ee_merge_tool) && frappe.user.has_role("System Manager");
	}

	function esc(v) {
		return frappe.utils.escape_html(v == null ? "" : String(v));
	}

	// ---------------------------------------------------------------------
	// Form button
	// ---------------------------------------------------------------------
	$(document).on("form-refresh", function (e, frm) {
		if (!is_enabled()) return;
		if (!frm || frm.is_new()) return;
		if (frm.meta && frm.meta.issingle) return; // singletons can't be merged
		if (frm.doc.docstatus === 1) return; // submitted docs are refused server-side

		frm.add_custom_button(__("Merge into…"), function () {
			open_loser_picker(frm.doctype, frm.doc.name);
		});
	});

	// Prompt for the document to absorb (the loser); the current doc is the survivor.
	function open_loser_picker(doctype, survivor) {
		frappe.prompt(
			[
				{
					label: __("Document to merge in (will be deleted)"),
					fieldname: "loser",
					fieldtype: "Link",
					options: doctype,
					reqd: 1,
					get_query: function () {
						return { filters: [[doctype, "name", "!=", survivor]] };
					},
				},
			],
			function (values) {
				load_and_preview(doctype, survivor, values.loser);
			},
			__("Merge {0}", [__(doctype)]),
			__("Preview Merge")
		);
	}

	// ---------------------------------------------------------------------
	// Preview dialog
	// ---------------------------------------------------------------------
	function load_and_preview(doctype, survivor, loser) {
		frappe.call({
			method: "erpnext_enhancements.document_merge.get_merge_preview",
			args: { doctype: doctype, survivor: survivor, loser: loser },
			freeze: true,
			freeze_message: __("Analyzing merge…"),
			callback: function (r) {
				if (r.message) show_preview_dialog(r.message);
			},
		});
	}

	function fmt_doc(name, title) {
		const t = title && title !== name ? ` <span class="text-muted">(${esc(title)})</span>` : "";
		return `<b>${esc(name)}</b>${t}`;
	}

	function build_fields_table(p) {
		if (!p.fields || !p.fields.length) {
			return `<p class="text-muted small">${__("No conflicting or backfilled fields.")}</p>`;
		}
		let rows = "";
		p.fields.forEach(function (f) {
			if (f.action === "backfill") {
				rows += `<tr>
					<td>${esc(f.label)}</td>
					<td class="text-muted"><i>${__("(empty)")}</i></td>
					<td class="text-success">${esc(f.loser)} <span class="text-muted small">← ${__(
						"fills blank"
					)}</span></td>
				</tr>`;
			} else {
				// differs — survivor kept, loser discarded
				rows += `<tr>
					<td>${esc(f.label)}</td>
					<td><b>${esc(f.survivor)}</b></td>
					<td class="text-muted"><s>${esc(f.loser)}</s> <span class="small">${__(
						"discarded"
					)}</span></td>
				</tr>`;
			}
		});
		return `<table class="table table-bordered table-condensed">
			<thead><tr>
				<th>${__("Field")}</th>
				<th>${__("Survivor (kept)")}</th>
				<th>${__("Merged doc")}</th>
			</tr></thead>
			<tbody>${rows}</tbody>
		</table>`;
	}

	function build_children(p) {
		if (!p.child_tables || !p.child_tables.length) return "";
		const items = p.child_tables
			.map(
				(c) =>
					`<li>${esc(c.label)}: <b>${c.appended}</b> ${__("row(s) appended")}${
						c.appended !== c.loser_rows
							? ` <span class="text-muted small">(${c.loser_rows - c.appended} ${__(
									"duplicate(s) skipped"
							  )})</span>`
							: ""
					}</li>`
			)
			.join("");
		return `<p class="mt-3"><b>${__("Child rows")}</b></p><ul>${items}</ul>`;
	}

	function build_references(p) {
		const hard = (p.hard_references || []).map(
			(h) =>
				`<li>${esc(h.doctype)} <span class="text-muted">· ${esc(h.fieldname)}</span>: <b>${
					h.count
				}</b></li>`
		);
		const soft = (p.soft_references || []).map(
			(s) => `<li>${esc(s.table)}: <b>${s.count}</b></li>`
		);
		const inner =
			hard.length || soft.length
				? `<ul style="max-height:160px; overflow-y:auto;">${hard.join("")}${soft.join("")}</ul>`
				: `<p class="text-muted small">${__("No references to repoint.")}</p>`;
		return `<p class="mt-3"><b>${__("References to be repointed")}</b> — ${__(
			"total"
		)} <b>${p.reference_total}</b>${
			p.background
				? ` <span class="indicator-pill orange">${__("runs in background")}</span>`
				: ""
		}</p>${inner}`;
	}

	function build_manual_review(p) {
		if (!p.manual_review || !p.manual_review.length) return "";
		const items = p.manual_review
			.map(
				(m) =>
					`<li>${esc(m.doctype)} <b>${esc(m.name)}</b> <span class="text-muted">· ${esc(
						m.fieldname
					)}</span></li>`
			)
			.join("");
		return `<div class="alert alert-warning mt-3" style="font-size:0.9em;">
			<b>${__("Needs manual review")}</b> — ${__(
			"these mention the merged document's name in free text and were NOT rewritten:"
		)}
			<ul style="max-height:120px; overflow-y:auto; margin-bottom:0;">${items}</ul>
		</div>`;
	}

	function show_preview_dialog(p) {
		const content = `
			<div>
				<p>${__("Merging")} ${fmt_doc(p.loser, p.loser_title)} ${__("into")} ${fmt_doc(
			p.survivor,
			p.survivor_title
		)}.
					<button class="btn btn-xs btn-default ee-merge-swap" style="margin-left:8px;">⇄ ${__(
						"Swap"
					)}</button>
				</p>
				<p class="text-muted small">${__(
					"The survivor keeps its values; its blank fields are filled from the merged doc, whose child rows are appended. The merged document is then deleted."
				)}</p>
				${build_fields_table(p)}
				${build_children(p)}
				${build_references(p)}
				${build_manual_review(p)}
				<p class="text-danger mt-3 mb-1">${__(
					"This cannot be undone — {0} will be permanently deleted.",
					[`<b>${esc(p.loser)}</b>`]
				)}</p>
			</div>`;

		const d = new frappe.ui.Dialog({
			title: __("Confirm Merge"),
			size: "large",
			fields: [
				{ fieldtype: "HTML", fieldname: "preview", options: content },
				{
					fieldtype: "Data",
					fieldname: "confirm_name",
					label: __("Type the name of the document being deleted to confirm"),
					description: p.loser,
				},
			],
			primary_action_label: __("Merge"),
			primary_action: function () {
				const typed = (d.get_value("confirm_name") || "").trim();
				if (typed !== p.loser) {
					frappe.msgprint({
						title: __("Confirmation does not match"),
						message: __("Type <b>{0}</b> exactly to confirm.", [esc(p.loser)]),
						indicator: "red",
					});
					return;
				}
				d.hide();
				execute_merge(p);
			},
		});

		// Swap survivor/loser and re-fetch the preview.
		d.$wrapper.on("click", ".ee-merge-swap", function () {
			d.hide();
			load_and_preview(p.doctype, p.loser, p.survivor);
		});

		d.show();
	}

	function execute_merge(p) {
		frappe.call({
			method: "erpnext_enhancements.document_merge.perform_merge",
			args: { doctype: p.doctype, survivor: p.survivor, loser: p.loser },
			freeze: true,
			freeze_message: __("Merging…"),
			callback: function (r) {
				if (r.exc) return;
				const msg = r.message || {};
				if (msg.queued) {
					frappe.msgprint({ title: __("Merge queued"), message: msg.message, indicator: "blue" });
					return;
				}
				frappe.show_alert({ message: msg.message || __("Merged."), indicator: "green" });
				// The loser is gone; land on the survivor.
				frappe.set_route("Form", p.doctype, p.survivor);
				if (
					cur_frm &&
					cur_frm.doctype === p.doctype &&
					cur_frm.doc.name === p.survivor
				) {
					cur_frm.reload_doc();
				}
			},
		});
	}

	// ---------------------------------------------------------------------
	// List-view bulk merge (… menu → "Merge Selected…")
	// ---------------------------------------------------------------------
	function add_list_action() {
		if (!is_enabled()) return;
		const route = frappe.get_route();
		if (!route || route[0] !== "List") return;
		const lv = window.cur_list;
		if (!lv || lv.doctype !== route[1] || !lv.page) return;
		if (lv.__ee_merge_added) return;
		if (frappe.get_meta(lv.doctype) && frappe.get_meta(lv.doctype).issingle) return;
		lv.__ee_merge_added = true;
		lv.page.add_menu_item(__("Merge Selected…"), function () {
			start_bulk_merge(lv);
		});
	}

	// Run on initial readiness AND on every route change (frappe.router 'change'
	// does NOT fire for the page the desk lands on directly — a hard refresh or
	// deep link straight to a list view). add_list_action is idempotent per
	// cur_list (the __ee_merge_added guard), so the extra retry is harmless and
	// closes the race where cur_list isn't built yet on a slow first paint.
	const schedule_list_action = () => {
		setTimeout(add_list_action, 300);
		setTimeout(add_list_action, 1200);
	};
	$(document).on("app_ready", schedule_list_action);
	frappe.router.on("change", schedule_list_action);
	if (frappe.router && frappe.get_route_str && frappe.get_route_str()) schedule_list_action();

	function start_bulk_merge(lv) {
		const checked = (lv.get_checked_items && lv.get_checked_items()) || [];
		if (checked.length < 2) {
			frappe.msgprint(__("Select at least two documents to merge."));
			return;
		}
		const doctype = lv.doctype;
		const names = checked.map((d) => d.name);
		const options = names.join("\n");

		const d = new frappe.ui.Dialog({
			title: __("Merge {0} Documents", [names.length]),
			fields: [
				{
					fieldtype: "Select",
					fieldname: "survivor",
					label: __("Survivor (keep this one)"),
					options: options,
					default: names[0],
					reqd: 1,
				},
				{ fieldtype: "HTML", fieldname: "info" },
				{
					fieldtype: "Check",
					fieldname: "ack",
					label: __("I understand the other documents will be permanently deleted"),
				},
			],
			primary_action_label: __("Merge"),
			primary_action: function () {
				const v = d.get_values();
				if (!v) return;
				if (!v.ack) {
					frappe.msgprint(__("Please confirm you understand the other documents will be deleted."));
					return;
				}
				const losers = names.filter((n) => n !== v.survivor);
				d.hide();
				run_bulk(doctype, v.survivor, losers, lv);
			},
		});

		const refresh_info = () => {
			const survivor = d.get_value("survivor");
			const losers = names.filter((n) => n !== survivor);
			d.fields_dict.info.$wrapper.html(
				`<p class="text-muted small">${__("These will each be merged into")} <b>${esc(
					survivor
				)}</b> ${__("and deleted")}:</p><ul>${losers
					.map((n) => `<li>${esc(n)}</li>`)
					.join("")}</ul>`
			);
		};
		d.fields_dict.survivor.$input && d.fields_dict.survivor.$input.on("change", refresh_info);
		d.show();
		refresh_info();
	}

	async function run_bulk(doctype, survivor, losers, lv) {
		let ok = 0;
		let queued = 0;
		let failed = 0;
		frappe.show_progress(__("Merging"), 0, losers.length);
		for (let i = 0; i < losers.length; i++) {
			try {
				const res = await frappe.xcall("erpnext_enhancements.document_merge.perform_merge", {
					doctype: doctype,
					survivor: survivor,
					loser: losers[i],
				});
				// A large merge returns {queued:true} — it is NOT done yet (the loser
				// is deleted asynchronously); don't count it as completed.
				if (res && res.queued) queued++;
				else ok++;
			} catch (e) {
				failed++;
			}
			frappe.show_progress(__("Merging"), i + 1, losers.length);
		}
		frappe.hide_progress();
		const parts = [__("{0} merged into {1}", [ok, esc(survivor)])];
		if (queued) parts.push(__("{0} running in background", [queued]));
		if (failed) parts.push(__("{0} failed (see Error Log)", [failed]));
		frappe.msgprint({
			title: __("Bulk merge complete"),
			message: parts.join(", ") + ".",
			indicator: failed ? "orange" : queued ? "blue" : "green",
		});
		if (lv && lv.refresh) lv.refresh();
	}

	// ---------------------------------------------------------------------
	// Background-merge completion notice
	// ---------------------------------------------------------------------
	$(document).on("app_ready", function () {
		frappe.realtime.on("document_merge_done", function (data) {
			if (!data) return;
			frappe.show_alert(
				{
					message: data.success
						? __("Merge of {0} into {1} finished.", [esc(data.loser), esc(data.survivor)])
						: __("Background merge of {0} failed (see Error Log).", [esc(data.loser)]),
					indicator: data.success ? "green" : "red",
				},
				10
			);
		});
	});
})();
