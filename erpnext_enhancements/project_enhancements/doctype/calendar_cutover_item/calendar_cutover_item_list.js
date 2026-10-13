// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// Calendar cutover (C5): the review list IS the page. The Cutover menu runs the four steps in order:
// 1. Scan the Maintenance calendar (read-only on Google) → review rows (Approve / Ignore below, or on
//    the form); 2. Apply approved (shows what it would write first); 3. End series (a dry run first,
//    then a confirm that sends the dry run's token, so only what was shown can happen); Undo end.
// Every button calls api/calendar_cutover.py, which is System Manager only.
//
// The dialogs read no dialog variable while they are being built: a Frappe control that asks for its
// options during construction would hit the `const`'s temporal dead zone (the v1.602.0 quick-add bug).
// tests/test_calendar_cutover.py builds both through a stand-in that does exactly that.

const CC_API = "erpnext_enhancements.api.calendar_cutover.";
const CC_COLORS = { Pending: "orange", Approved: "blue", Applied: "green", Ignored: "gray" };

frappe.listview_settings["Calendar Cutover Item"] = {
	add_fields: ["status", "match_quality", "series_state", "profile"],
	get_indicator(doc) {
		return [__(doc.status), CC_COLORS[doc.status] || "gray", "status,=," + doc.status];
	},
	onload(listview) {
		const group = __("Cutover");
		listview.page.add_inner_button(__("1. Scan the Maintenance calendar"), () => cc_scan(listview), group);
		listview.page.add_inner_button(__("2. Apply approved"), () => cc_apply(listview), group);
		listview.page.add_inner_button(__("3. End the old series…"), () => cc_end_dialog(listview), group);
		listview.page.add_inner_button(__("Undo end…"), () => cc_undo_dialog(listview), group);
		listview.page.add_actions_menu_item(__("Approve"), () => cc_set_status(listview, "Approved"), false);
		listview.page.add_actions_menu_item(__("Ignore"), () => cc_set_status(listview, "Ignored"), false);
		listview.page.add_actions_menu_item(__("Back to Pending"), () => cc_set_status(listview, "Pending"), false);
	},
};

function cc_esc(value) {
	return frappe.utils.escape_html(value === undefined || value === null ? "" : String(value));
}

function cc_refresh(listview) {
	if (listview && listview.refresh) listview.refresh();
}

function cc_skipped_html(rows) {
	if (!rows || !rows.length) return "";
	return (
		`<ul style="margin:4px 0 0 18px">` +
		rows.map((r) => `<li>${cc_esc(r.title || r[0])}: ${cc_esc(r.reason || r[1])}</li>`).join("") +
		"</ul>"
	);
}

function cc_set_status(listview, status) {
	const names = listview.get_checked_items(true) || [];
	if (!names.length) {
		frappe.msgprint(__("Tick the rows first."));
		return null;
	}
	return frappe
		.call({ method: CC_API + "set_status", type: "POST", args: { names: JSON.stringify(names), status } })
		.then((r) => {
			const out = r.message || {};
			const done = (out.done || []).length;
			frappe.show_alert({ message: __("{0} row(s) now {1}", [done, __(status)]), indicator: "green" });
			if ((out.skipped || []).length) {
				frappe.msgprint({ title: __("Not changed"), message: cc_skipped_html(out.skipped) });
			}
			cc_refresh(listview);
		});
}

function cc_scan(listview) {
	return frappe
		.call({ method: CC_API + "scan", type: "POST", freeze: true, freeze_message: __("Reading the Maintenance calendar…") })
		.then((r) => {
			const out = r.message || {};
			const c = out.counts || {};
			const lines = [
				__("{0} events read; {1} recurring series, {2} single events, {3} moved occurrences, {4} ERPNext events (ignored).", [c.events || 0, c.series || 0, c.single || 0, c.exception || 0, c.erpnext || 0]),
				__("Review rows: {0} new, {1} refreshed (Pending), {2} kept as reviewed.", [c.created || 0, c.refreshed || 0, c.kept || 0]),
			];
			if (c.already_ended) lines.push(__("{0} series had already ended before today and were not listed.", [c.already_ended]));
			if (c.gone) lines.push(__("{0} row(s) whose series is no longer on the calendar are flagged.", [c.gone]));
			if (!out.complete) lines.push(__("The calendar is larger than one scan reads: some events were not seen."));
			let html = lines.map((l) => `<p>${cc_esc(l)}</p>`).join("");
			if ((out.unmatched || []).length) {
				html += `<p>${cc_esc(__("Pick a profile by hand for these:"))}</p><ul style="margin:4px 0 0 18px">` + out.unmatched.map((t) => `<li>${cc_esc(t)}</li>`).join("") + "</ul>";
			}
			frappe.msgprint({ title: __("Scan finished"), message: html, indicator: "green" });
			cc_refresh(listview);
		});
}

function cc_apply_html(out) {
	const rows = (out.rows || [])
		.map(
			(r) =>
				`<tr><td>${cc_esc(r.title)}</td><td>${cc_esc(r.profile)}</td><td>${cc_esc((r.notes || []).join("; ") || __("nothing new"))}</td><td>${cc_esc(r.address)}</td><td class="text-danger">${cc_esc(r.error)}</td></tr>`
		)
		.join("");
	if (!rows) return `<p>${cc_esc(__("No approved rows."))}</p>`;
	return `<div style="max-height:50vh;overflow:auto"><table class="table table-bordered table-condensed"><thead><tr><th>${cc_esc(__("Series"))}</th><th>${cc_esc(__("Profile"))}</th><th>${cc_esc(__("Profile changes"))}</th><th>${cc_esc(__("Address"))}</th><th>${cc_esc(__("Error"))}</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

function cc_apply(listview) {
	return frappe.call({ method: CC_API + "apply_approved", type: "POST", args: { confirm: 0 } }).then((r) => {
		const plan = r.message || {};
		const ready = (plan.rows || []).filter((row) => !row.error).length;
		if (!ready) {
			frappe.msgprint({ title: __("Apply approved"), message: cc_apply_html(plan) });
			return;
		}
		frappe.confirm(
			`<p>${cc_esc(__("This writes to {0} Maintenance Profile(s). Text is appended, never written over.", [ready]))}</p>` + cc_apply_html(plan),
			() =>
				frappe.call({ method: CC_API + "apply_approved", type: "POST", args: { confirm: 1 } }).then((done) => {
					const out = done.message || {};
					frappe.msgprint({ title: __("Applied {0}", [out.applied || 0]), message: cc_apply_html(out) });
					cc_refresh(listview);
				})
		);
	});
}

function cc_end_html(plan) {
	const parts = [];
	parts.push(`<p>${cc_esc(__("Cutover {0}: each series below keeps its occurrences up to {1} and has none from {0} on. Nothing is deleted.", [plan.cutover, plan.last_day]))}</p>`);
	if (!plan.sync_on) parts.push(`<p class="text-warning">${cc_esc(__("Calendar sync is off. Ending the series needs it on."))}</p>`);
	const changes = (plan.changes || [])
		.map(
			(c) =>
				`<tr><td>${cc_esc(c.title)}</td><td><code>${cc_esc((c.before || []).join(" / "))}</code></td><td><code>${cc_esc((c.after || []).join(" / "))}</code></td><td>${cc_esc(c.note)}${c.moved_after ? "<br>" + cc_esc(__("{0} moved occurrence(s) after the cutover: check them by hand.", [c.moved_after])) : ""}</td></tr>`
		)
		.join("");
	parts.push(
		changes
			? `<div style="max-height:45vh;overflow:auto"><table class="table table-bordered table-condensed"><thead><tr><th>${cc_esc(__("Series"))}</th><th>${cc_esc(__("Rule now"))}</th><th>${cc_esc(__("Rule after"))}</th><th>${cc_esc(__("Note"))}</th></tr></thead><tbody>${changes}</tbody></table></div>`
			: `<p>${cc_esc(__("Nothing to change."))}</p>`
	);
	if ((plan.unchanged || []).length) parts.push(`<p>${cc_esc(__("Left as they are:"))}</p>` + cc_skipped_html(plan.unchanged));
	if ((plan.skipped || []).length) parts.push(`<p>${cc_esc(__("Not touched:"))}</p>` + cc_skipped_html(plan.skipped));
	if ((plan.not_on_review_list || []).length) {
		parts.push(`<p class="text-warning">${cc_esc(__("Running series not on the review list (scan again):"))} ${cc_esc(plan.not_on_review_list.join(", "))}</p>`);
	}
	if ((plan.failed || []).length) parts.push(`<p class="text-danger">${cc_esc(__("Failed:"))}</p>` + cc_skipped_html(plan.failed.map((f) => ({ title: f.title, reason: f.error }))));
	return parts.join("");
}

// Step 3. The first click is the dry run; the button then offers to end exactly what it showed, and
// sends the dry run's token. Changing the date means a new dry run.
function cc_end_dialog(listview) {
	const state = { token: null, cutover: null, count: 0 };
	const dialog = new frappe.ui.Dialog({
		title: __("End the old Maintenance series"),
		size: "extra-large",
		fields: [
			{
				fieldname: "cutover_date",
				fieldtype: "Date",
				label: __("Cutover date"),
				reqd: 1,
				default: frappe.datetime.get_today(),
				description: __("The first day ERPNext's sync owns the Maintenance calendar. Occurrences before it stay as history."),
			},
			{ fieldname: "result", fieldtype: "HTML" },
		],
		primary_action_label: __("Dry run"),
		primary_action(values) {
			return cc_end_step(dialog, state, values, listview);
		},
	});
	dialog.show();
	return dialog;
}

function cc_end_step(dialog, state, values, listview) {
	const out = dialog.get_field("result").$wrapper;
	const cutover_date = values && values.cutover_date;
	if (state.token && state.cutover === cutover_date && state.count) {
		return frappe.confirm(__("End {0} series on the shared Maintenance calendar now? Undo end can put them back.", [state.count]), () =>
			frappe
				.call({ method: CC_API + "end_series", type: "POST", freeze: true, args: { cutover_date, confirm: 1, token: state.token } })
				.then((r) => {
					const done = r.message || {};
					state.token = null;
					out.html(`<p><b>${cc_esc(__("Ended {0} series.", [(done.done || []).length]))}</b></p>` + cc_end_html(done));
					dialog.set_primary_action(__("Dry run"), (v) => cc_end_step(dialog, state, v || dialog.get_values(), listview));
					cc_refresh(listview);
				})
		);
	}
	out.html(`<div class="text-muted">${cc_esc(__("Reading the calendar… nothing is changed."))}</div>`);
	return frappe.call({ method: CC_API + "end_series", type: "POST", args: { cutover_date, confirm: 0 } }).then((r) => {
		const plan = r.message || {};
		state.token = plan.token;
		state.cutover = cutover_date;
		state.count = (plan.changes || []).length;
		out.html(cc_end_html(plan));
		const label = state.count ? __("End {0} series", [state.count]) : __("Dry run");
		dialog.set_primary_action(label, (v) => cc_end_step(dialog, state, v || dialog.get_values(), listview));
	});
}

function cc_undo_html(plan) {
	const rows = (plan.changes || []).map((c) => `<li>${cc_esc(c.title)}: <code>${cc_esc((c.after || []).join(" / "))}</code></li>`).join("");
	let html = rows ? `<p>${cc_esc(__("These rules go back to what they were:"))}</p><ul style="margin:4px 0 0 18px">${rows}</ul>` : `<p>${cc_esc(__("Nothing to undo."))}</p>`;
	if ((plan.skipped || []).length) html += `<p>${cc_esc(__("Not touched:"))}</p>` + cc_skipped_html(plan.skipped);
	if ((plan.failed || []).length) html += `<p class="text-danger">${cc_esc(__("Failed:"))}</p>` + cc_skipped_html(plan.failed.map((f) => ({ title: f.title, reason: f.error })));
	return html;
}

// Undo: the same two clicks. It works with the sync switched off, because that is when it is needed.
function cc_undo_dialog(listview) {
	const state = { token: null, count: 0 };
	const dialog = new frappe.ui.Dialog({
		title: __("Undo: put the old series' rules back"),
		size: "large",
		fields: [{ fieldname: "result", fieldtype: "HTML" }],
		primary_action_label: __("Dry run"),
		primary_action() {
			return cc_undo_step(dialog, state, listview);
		},
	});
	dialog.show();
	return dialog;
}

function cc_undo_step(dialog, state, listview) {
	const out = dialog.get_field("result").$wrapper;
	if (state.token && state.count) {
		return frappe.confirm(__("Restore {0} series on the shared Maintenance calendar now?", [state.count]), () =>
			frappe.call({ method: CC_API + "undo_end", type: "POST", freeze: true, args: { confirm: 1, token: state.token } }).then((r) => {
				const done = r.message || {};
				state.token = null;
				state.count = 0;
				out.html(`<p><b>${cc_esc(__("Restored {0} series.", [(done.done || []).length]))}</b></p>` + cc_undo_html(done));
				dialog.set_primary_action(__("Dry run"), () => cc_undo_step(dialog, state, listview));
				cc_refresh(listview);
			})
		);
	}
	out.html(`<div class="text-muted">${cc_esc(__("Reading the calendar… nothing is changed."))}</div>`);
	return frappe.call({ method: CC_API + "undo_end", type: "POST", args: { confirm: 0 } }).then((r) => {
		const plan = r.message || {};
		state.token = plan.token;
		state.count = (plan.changes || []).length;
		out.html(cc_undo_html(plan));
		dialog.set_primary_action(state.count ? __("Restore {0} series", [state.count]) : __("Dry run"), () => cc_undo_step(dialog, state, listview));
	});
}
