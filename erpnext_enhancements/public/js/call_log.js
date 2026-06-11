/**
 * Call Log form enhancements (Call Intelligence).
 *
 * Targets: the Call Log form.
 * Loaded via: hooks.py `doctype_js["Call Log"]`.
 *
 * The stock ERPNext client script already renders the audio player from
 * `recording_url` (which the ingest webhook rewrites to the private File URL)
 * and a Callback button via `frappe.phone_call.handler`. This script adds the
 * Triton-specific pieces:
 *   - "View Transcript": fetches the linked Communication's content and shows
 *     it in a dialog (the transcript deliberately lives on the Communication,
 *     not the Call Log — see api/call_intelligence.py).
 *   - "Open Communication": jump to the timeline record.
 *   - "Call Back (Triton)": click-to-call through the Triton gateway
 *     (`trigger_outbound_call`), same flow as the Contact/Customer buttons.
 */
frappe.ui.form.on("Call Log", {
	refresh(frm) {
		if (frm.doc.custom_communication) {
			frm.add_custom_button(__("View Transcript"), () => show_transcript(frm));
			frm.add_custom_button(__("Open Communication"), () => {
				frappe.set_route("Form", "Communication", frm.doc.custom_communication);
			});
		}

		const number = frm.doc.type === "Incoming" ? frm.doc.from : frm.doc.to;
		if (number) {
			frm.add_custom_button(__("Call Back (Triton)"), () => {
				frappe.confirm(
					__("Call {0} from your cell phone via Triton?", [number]),
					() => {
						frappe.call({
							method: "erpnext_enhancements.api.telephony.trigger_outbound_call",
							args: {
								doctype: "Call Log",
								docname: frm.doc.name,
								target_number: number,
							},
							freeze: true,
							freeze_message: __("Initiating call..."),
							callback: () => {
								frappe.show_alert({
									message: __("Call initiated — your phone will ring first."),
									indicator: "green",
								});
							},
						});
					}
				);
			});
		}
	},
});

function show_transcript(frm) {
	frappe.db
		.get_value("Communication", frm.doc.custom_communication, "content")
		.then((r) => {
			const content = (r.message && r.message.content) || "";
			const d = new frappe.ui.Dialog({
				title: __("Call Transcript — {0}", [
					frm.doc.custom_caller_name || frm.doc.from || frm.doc.name,
				]),
				size: "large",
			});
			// Communication content is HTML the app itself wrote (transcript in a
			// <pre>); render it inside a scrollable, theme-safe container.
			$(d.body).append(
				$("<div>")
					.css({
						"max-height": "60vh",
						overflow: "auto",
						"white-space": "pre-wrap",
						"font-size": "var(--text-md)",
						color: "var(--text-color)",
					})
					.html(content || `<em>${__("No transcript stored.")}</em>`)
			);
			d.show();
		});
}
