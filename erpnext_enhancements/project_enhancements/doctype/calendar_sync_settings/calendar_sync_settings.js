// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// Calendar Sync Settings (C2): sign-in, the calendar picker, the health panel, the preview and
// Re-sync everything. Every button calls api/calendar_sync.py, which is System Manager only. Nothing
// here ever sees a token: the refresh token is a Password field the server writes.

const CS_API = "erpnext_enhancements.api.calendar_sync.";
const CS_ROLES = [
	["projects_calendar_id", __("Projects")],
	["maintenance_calendar_id", __("Maintenance")],
	["holidays_calendar_id", __("Company Holidays & Vacations")],
];

frappe.ui.form.on("Calendar Sync Settings", {
	onload(frm) {
		const params = new URLSearchParams(window.location.search);
		const outcome = params.get("calendar_sync");
		if (outcome === "connected") {
			frappe.show_alert({ message: __("Google account connected."), indicator: "green" });
		} else if (outcome === "refused") {
			frappe.show_alert({ message: __("Google sign-in was cancelled or refused."), indicator: "orange" });
		}
		if (outcome) {
			// Drop the query parameter without adding a history entry (Back must not re-show it).
			params.delete("calendar_sync");
			const query = params.toString();
			window.history.replaceState(window.history.state, "", window.location.pathname + (query ? "?" + query : ""));
		}
	},

	refresh(frm) {
		cs_render_health(frm, false);
		const connected = !!frm.doc.google_account || !!frm.doc.authorized_on;
		frm.add_custom_button(connected ? __("Reconnect Google") : __("Authorize with Google"), () => cs_authorize(frm), __("Google"));
		if (connected) {
			frm.add_custom_button(__("Test connection"), () => cs_render_health(frm, true), __("Google"));
			frm.add_custom_button(__("Pick calendars"), () => cs_pick_calendars(frm), __("Google"));
			frm.add_custom_button(__("Disconnect"), () => cs_disconnect(frm), __("Google"));
		}
		frm.add_custom_button(__("Preview"), () => cs_preview(frm), __("Sync"));
		frm.add_custom_button(__("Re-sync everything"), () => cs_resync(frm), __("Sync"));
	},
});

function cs_esc(value) {
	return frappe.utils.escape_html(value === undefined || value === null ? "" : String(value));
}

function cs_authorize(frm) {
	const go = () =>
		frappe.call({ method: CS_API + "authorize_url", type: "POST" }).then((r) => {
			if (r.message && r.message.url) window.location.href = r.message.url;
		});
	if (frm.is_dirty()) {
		frm.save().then(go);
	} else {
		go();
	}
}

function cs_disconnect(frm) {
	frappe.confirm(__("Switch sync off and forget this Google account? Events already on the calendars stay."), () =>
		frappe.call({ method: CS_API + "disconnect", type: "POST" }).then(() => frm.reload_doc())
	);
}

function cs_resync(frm) {
	frappe.call({ method: CS_API + "resync_all", type: "POST" }).then(() =>
		frappe.show_alert({ message: __("Re-sync queued. The health panel shows when it finished."), indicator: "blue" })
	);
}

function cs_line(label, value) {
	return `<tr><th style="text-align:left;padding:2px 12px 2px 0;font-weight:600;white-space:nowrap">${cs_esc(label)}</th><td style="padding:2px 0">${value}</td></tr>`;
}

function cs_yes_no(flag, yes, no) {
	return flag ? `<span class="indicator-pill green">${cs_esc(yes)}</span>` : `<span class="indicator-pill red">${cs_esc(no)}</span>`;
}

function cs_render_health(frm, test) {
	const field = frm.get_field("health_html");
	if (!field) return;
	field.$wrapper.html(`<div class="text-muted">${cs_esc(__("Loading…"))}</div>`);
	frappe.call({ method: CS_API + "health", args: { test: test ? 1 : 0 } }).then((r) => {
		const h = r.message || {};
		const rows = [];
		rows.push(cs_line(__("Sync"), cs_yes_no(h.active, __("On"), h.enabled ? __("On, but no account") : __("Off"))));
		rows.push(cs_line(__("Google Settings"), cs_yes_no(h.google_settings_ready, __("Ready"), __("Not set up"))));
		rows.push(cs_line(__("Account"), h.connected ? cs_esc(h.account || __("connected")) : cs_esc(__("Not connected"))));
		if (h.token_ok !== null && h.token_ok !== undefined) {
			rows.push(cs_line(__("Token"), cs_yes_no(h.token_ok, __("Alive"), __("Refused")) + (h.token_error ? " " + cs_esc(h.token_error) : "")));
		}
		rows.push(cs_line(__("Redirect URI"), `<code>${cs_esc(h.redirect_uri)}</code>`));
		rows.push(cs_line(__("Window"), cs_esc((h.window || []).join(" → "))));
		rows.push(cs_line(__("Last snap-back check"), cs_esc(h.last_incremental_sync || __("never"))));
		rows.push(cs_line(__("Last full repair"), cs_esc(h.last_full_repair || __("never")) + (h.last_full_repair_summary ? "<br>" + cs_esc(h.last_full_repair_summary) : "")));
		const counts = h.counts || {};
		const count_text = Object.keys(counts)
			.sort()
			.map((status) => `${cs_esc(status)}: ` + Object.keys(counts[status]).sort().map((kind) => `${cs_esc(kind)} ${cs_esc(counts[status][kind])}`).join(", "))
			.join("<br>");
		rows.push(cs_line(__("Events"), count_text || cs_esc(__("none yet"))));
		let errors = "";
		if ((h.errors || []).length) {
			errors =
				`<div style="margin-top:8px;font-weight:600">${cs_esc(__("Latest errors"))}</div><ul style="margin:4px 0 0 18px">` +
				h.errors.map((e) => `<li>${cs_esc(e.title || e.source_name)} (${cs_esc(e.source_doctype)} ${cs_esc(e.source_name)}): ${cs_esc(e.last_error)}</li>`).join("") +
				"</ul>";
		}
		field.$wrapper.html(`<table>${rows.join("")}</table>${errors}`);
	});
}

function cs_pick_calendars(frm) {
	frappe.call({ method: CS_API + "list_calendars" }).then((r) => {
		const calendars = (r.message || []).filter((c) => c.writable);
		if (!calendars.length) {
			frappe.msgprint(__("This account cannot make changes to any calendar."));
			return;
		}
		const options = [""].concat(calendars.map((c) => c.id));
		const label = {};
		calendars.forEach((c) => (label[c.id] = c.summary + (c.time_zone ? ` (${c.time_zone})` : "")));
		const fields = [];
		CS_ROLES.forEach(([fieldname, title]) => {
			fields.push({
				fieldname,
				fieldtype: "Select",
				label: title,
				options: options.map((id) => ({ value: id, label: id ? label[id] : __("(none)") })),
				default: frm.doc[fieldname] || "",
			});
		});
		const dialog = new frappe.ui.Dialog({
			title: __("Pick calendars"),
			fields,
			primary_action_label: __("Use these"),
			primary_action(values) {
				CS_ROLES.forEach(([fieldname]) => frm.set_value(fieldname, values[fieldname] || ""));
				dialog.hide();
				frm.save();
			},
		});
		dialog.show();
	});
}

function cs_preview(frm) {
	const dialog = new frappe.ui.Dialog({
		title: __("Preview: what a full re-sync would do"),
		size: "extra-large",
		fields: [
			{ fieldname: "start", fieldtype: "Date", label: __("From") },
			{ fieldname: "column", fieldtype: "Column Break" },
			{ fieldname: "end", fieldtype: "Date", label: __("To") },
			{ fieldname: "section", fieldtype: "Section Break" },
			{ fieldname: "result", fieldtype: "HTML" },
		],
		primary_action_label: __("Preview"),
		primary_action(values) {
			const out = dialog.get_field("result").$wrapper;
			out.html(`<div class="text-muted">${cs_esc(__("Working it out… nothing is sent to Google."))}</div>`);
			frappe.call({ method: CS_API + "preview", args: { start: values.start, end: values.end } }).then((r) => {
				const p = r.message || {};
				const counts = Object.keys(p.counts || {})
					.sort()
					.map((a) => `${cs_esc(a)}: ${cs_esc(p.counts[a])}`)
					.join(" · ");
				const rows = (p.rows || [])
					.map(
						(row) =>
							`<tr><td>${cs_esc(row.date)}</td><td>${cs_esc(row.action)}</td><td>${cs_esc(row.kind)}</td><td>${cs_esc(row.title)}</td><td class="text-muted">${cs_esc(row.source)}</td></tr>`
					)
					.join("");
				out.html(
					`<p>${cs_esc(p.start)} → ${cs_esc(p.end)}. ${counts}${p.enabled ? "" : " " + cs_esc(__("(sync is off: nothing will be sent until it is on)"))}</p>` +
						(rows
							? `<div style="max-height:60vh;overflow:auto"><table class="table table-bordered table-condensed"><thead><tr><th>${cs_esc(__("Date"))}</th><th>${cs_esc(__("Action"))}</th><th>${cs_esc(__("Kind"))}</th><th>${cs_esc(__("Event"))}</th><th>${cs_esc(__("Source"))}</th></tr></thead><tbody>${rows}</tbody></table></div>`
							: `<p>${cs_esc(__("Nothing would change."))}</p>`)
				);
			});
		},
	});
	dialog.show();
}
