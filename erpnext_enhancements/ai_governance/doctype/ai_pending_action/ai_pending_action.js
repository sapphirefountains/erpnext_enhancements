/**
 * AI Pending Action form — Confirm / Cancel buttons.
 *
 * Calls the whitelisted endpoints in assistant_tools/gating_api.py by dotted
 * path (frappe.call resolves them at request time — no Python import, which
 * keeps the FAC-optional tripwire green). Buttons only show on Pending rows
 * for the requester or a System Manager; the server re-checks identity and
 * expiry regardless.
 */
const REDACTED = "***REDACTED***";

// Where the card shows the placeholder, as `data.rows[0].author`. Those are the values the
// sealed field holds and confirming will run.
function hidden_paths(frm) {
	let args;
	try {
		args = JSON.parse(frm.doc.arguments || "{}");
	} catch (e) {
		return [];
	}
	const found = [];
	const walk = (value, path) => {
		if (Array.isArray(value)) {
			value.forEach((item, i) => walk(item, `${path}[${i}]`));
		} else if (value && typeof value === "object") {
			Object.keys(value).forEach((key) => {
				const next = path ? `${path}.${key}` : key;
				if (value[key] === REDACTED) found.push(next);
				else walk(value[key], next);
			});
		}
	};
	walk(args, "");
	return found;
}

function show_hidden_values(frm) {
	frappe.call({
		method: "erpnext_enhancements.assistant_tools.gating_api.reveal_sealed",
		args: { name: frm.doc.name },
		callback: (r) => {
			const rows = (r.message || [])
				.map(
					(row) =>
						`<tr><td><code>${frappe.utils.escape_html(row.path)}</code></td>` +
						`<td><code>${frappe.utils.escape_html(
							typeof row.value === "string" ? row.value : JSON.stringify(row.value)
						)}</code></td></tr>`
				)
				.join("");
			frappe.msgprint({
				title: __("Hidden values"),
				message: rows
					? `<table class="table table-bordered"><tbody>${rows}</tbody></table>`
					: __("Nothing is hidden on this card."),
			});
		},
	});
}

frappe.ui.form.on("AI Pending Action", {
	refresh(frm) {
		if (frm.is_new() || frm.doc.status !== "Pending") return;

		const me = frappe.session.user;
		const may_decide =
			me === frm.doc.requested_by || frappe.user_roles.includes("System Manager");
		if (!may_decide) return;

		// A Password field reaches the form as asterisks, so this only says that values were
		// sealed. reveal_sealed returns them to the two people allowed to decide.
		const sealed = Boolean(frm.doc.sealed_arguments);
		if (sealed) {
			frm.add_custom_button(__("Show Hidden Values"), () => show_hidden_values(frm));
		}

		frm.add_custom_button(__("Confirm & Execute"), () => {
			const paths = sealed ? hidden_paths(frm) : [];
			const hidden = sealed
				? "<br><br>" +
				  __(
						"Hidden on this card because the names look like credentials: {0}. These values will be used exactly as the assistant proposed them. Use Show Hidden Values to check them first.",
						[paths.map((p) => `<code>${frappe.utils.escape_html(p)}</code>`).join(", ") || "?"]
				  )
				: "";
			frappe.confirm(
				__("Execute this AI action now?<br><br><b>{0}</b> (risk: {1})", [
					frappe.utils.escape_html(frm.doc.summary || frm.doc.tool_name),
					frm.doc.risk || "?",
				]) + hidden,
				() => {
					frappe.call({
						method: "erpnext_enhancements.assistant_tools.gating_api.confirm_action",
						args: { name: frm.doc.name },
						freeze: true,
						freeze_message: __("Executing..."),
						callback: () => {
							frappe.show_alert({ message: __("Executed."), indicator: "green" });
							frm.reload_doc();
						},
						error: () => frm.reload_doc(),
					});
				}
			);
		}).addClass("btn-primary");

		frm.add_custom_button(__("Cancel Action"), () => {
			frappe.call({
				method: "erpnext_enhancements.assistant_tools.gating_api.cancel_action",
				args: { name: frm.doc.name },
				freeze: true,
				callback: () => {
					frappe.show_alert({ message: __("Cancelled."), indicator: "orange" });
					frm.reload_doc();
				},
			});
		});
	},
});
