/**
 * @file Shared "schedule a hand-off meeting" dialog (PRO-0204).
 * @description
 * One dialog, used by two steps that are the same act at different points in the
 * process: step 2 (Hold Hand-Off Meeting, on the Opportunity) and step 7 (Hold
 * Project Launch Meeting, on the Project).
 *
 * The 2026-08-06 process meeting asked for buttons that *do the step* rather than
 * record that somebody did it elsewhere. So this dialog does the booking: it
 * prefills the three functions who need to be in the room, proposes the next
 * business-day slot, and on submit the server creates the calendar Event and
 * emails the invite. Everything prefilled is editable — the suggestion is meant
 * to make the common case one click, not to take the decision away.
 *
 * Exposed as `erpnext_enhancements.handoff_meeting_dialog` because it is loaded
 * on both the Opportunity and the Project form; each form script calls `open()`.
 */
frappe.provide("erpnext_enhancements");

erpnext_enhancements.handoff_meeting_dialog = (function () {
	/** Server sends `{Sales: [...], Production: [...], Billing: [...]}`. */
	function join(list) {
		return (list || []).join(", ");
	}

	/**
	 * Next business-day 09:00, as a client-side default while the dialog opens.
	 * The server computes the authoritative default (it honours the configured
	 * Holiday List); this is only so the field is never blank on first paint.
	 */
	function default_slot() {
		const dt = frappe.datetime.add_days(frappe.datetime.now_date(), 1);
		const day = frappe.datetime.str_to_obj(dt).getDay();
		const bump = day === 6 ? 2 : day === 0 ? 1 : 0; // Sat -> Mon, Sun -> Mon
		return `${frappe.datetime.add_days(dt, bump)} 09:00:00`;
	}

	/**
	 * @param {Object} opts
	 * @param {string} opts.title           Dialog + button label.
	 * @param {Object} opts.attendees       `{Sales, Production, Billing}` arrays.
	 * @param {Function} opts.on_submit     Receives `{starts_on, attendees, duration_minutes}`.
	 * @param {string} [opts.description]   Intro line above the fields.
	 */
	function open(opts) {
		const dialog = new frappe.ui.Dialog({
			title: opts.title || __("Schedule Hand-Off Meeting"),
			fields: [
				{
					fieldtype: "HTML",
					fieldname: "intro",
					options: `<p class="text-muted">${frappe.utils.escape_html(
						opts.description ||
							__("Everyone below is invited by email and the meeting is added to the calendar.")
					)}</p>`,
				},
				{
					fieldname: "starts_on",
					label: __("Date / Time"),
					fieldtype: "Datetime",
					reqd: 1,
					default: default_slot(),
				},
				{
					fieldname: "duration_minutes",
					label: __("Duration (minutes)"),
					fieldtype: "Int",
					default: 15,
				},
				{ fieldtype: "Section Break", label: __("Attendees") },
				{
					fieldname: "sales",
					label: __("Sales"),
					fieldtype: "Small Text",
					description: __("Comma-separated. Prefilled with the deal owner and the Sales list."),
					default: join(opts.attendees && opts.attendees.Sales),
				},
				{
					fieldname: "production",
					label: __("Production"),
					fieldtype: "Small Text",
					description: __("Comma-separated."),
					default: join(opts.attendees && opts.attendees.Production),
				},
				{
					fieldname: "billing",
					label: __("Billing"),
					fieldtype: "Small Text",
					description: __("Comma-separated."),
					default: join(opts.attendees && opts.attendees.Billing),
				},
			],
			primary_action_label: __("Create Meeting & Send Invites"),
			primary_action(values) {
				const attendees = [values.sales, values.production, values.billing]
					.filter(Boolean)
					.join(",")
					.split(",")
					.map((address) => address.trim())
					.filter(Boolean);

				if (!attendees.length) {
					frappe.msgprint({
						title: __("No attendees"),
						message: __("Add at least one attendee before creating the meeting."),
						indicator: "orange",
					});
					return;
				}

				dialog.disable_primary_action();
				opts.on_submit({
					starts_on: values.starts_on,
					duration_minutes: values.duration_minutes,
					attendees: attendees,
				})
					.then(() => dialog.hide())
					// Re-enable rather than leaving a dead button: a failed send is
					// usually a fixable address, and the user should get to retry
					// without rebuilding the whole dialog.
					.catch(() => dialog.enable_primary_action());
			},
		});
		dialog.show();
		return dialog;
	}

	return { open: open, default_slot: default_slot };
})();
