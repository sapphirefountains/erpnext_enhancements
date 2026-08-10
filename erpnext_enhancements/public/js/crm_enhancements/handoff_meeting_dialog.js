/**
 * @file Shared "schedule a hand-off meeting" dialog (PRO-0204).
 * @description
 * One dialog, used by two steps that are the same act at different points in the
 * process: step 2 (Hold Hand-Off Meeting, on the Opportunity) and step 7 (Hold
 * Project Launch Meeting, on the Project).
 *
 * The 2026-08-06 process meeting asked for buttons that *do the step* rather than
 * record that somebody did it elsewhere. So this dialog does the booking: it
 * prefills the three functions who need to be in the room, proposes a slot, and
 * on submit the server creates the calendar Event and emails the invite.
 * Everything prefilled is editable — the suggestion is meant to make the common
 * case one click, not to take the decision away.
 *
 * Attendee fields are `MultiSelect`: an autocomplete over every desk user and
 * every configured hand-off address, in a field that still accepts anything you
 * type. Both halves matter — picking stops
 * `firstname.lastname@sapphirefountains.com` being mistyped into a silent
 * non-delivery, and typing is how a one-off attendee (a subcontractor, a
 * customer's PM) gets into the room at all.
 *
 * Loaded globally via erpnext_enhancements.bundle.js rather than as a
 * doctype_js: the Closed-Won prompt in create_project_prompt.js opens this on the
 * Kanban board and the list view too, where no form script is loaded.
 */
frappe.provide("erpnext_enhancements");

erpnext_enhancements.handoff_meeting_dialog = (function () {
	/** Server sends `{Sales: [...], Production: [...], Billing: [...]}`. */
	function join(list) {
		return (list || []).join(", ");
	}

	/**
	 * Next business-day 09:00 — the *launch* meeting's default.
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
	 * Now, rounded up to the next quarter hour — the *hand-off* meeting's default.
	 *
	 * Deliberately different from `default_slot()`. The hand-off is a 15-minute
	 * handover between people who are frequently already talking when the deal
	 * closes, so defaulting to tomorrow made the commonest answer ("in ten
	 * minutes", "right after this call") a correction. Nothing bounds the field
	 * either way: the picker starts at the present and goes wherever the meeting
	 * actually is.
	 */
	function now_slot() {
		const dt = frappe.datetime.str_to_obj(frappe.datetime.now_datetime());
		dt.setSeconds(0, 0);
		dt.setMinutes(Math.ceil((dt.getMinutes() + 1) / 15) * 15); // 60 rolls the hour
		// Formatted by hand rather than through frappe.datetime.obj_to_str, which
		// returns moment's ISO-8601 default — a shape the Datetime control does
		// not round-trip through its own "YYYY-MM-DD HH:mm:ss" parser.
		const pad = (n) => String(n).padStart(2, "0");
		return (
			`${dt.getFullYear()}-${pad(dt.getMonth() + 1)}-${pad(dt.getDate())} ` +
			`${pad(dt.getHours())}:${pad(dt.getMinutes())}:00`
		);
	}

	/**
	 * Addresses to autocomplete over. Cached for the page: it is a user list, it
	 * does not change between two clicks, and the dialog should open now.
	 */
	let options_cache = null;
	function attendee_options() {
		if (options_cache) return Promise.resolve(options_cache);
		return frappe
			.xcall("erpnext_enhancements.crm_enhancements.handoff.attendee_options")
			.then((options) => {
				options_cache = options || [];
				return options_cache;
			})
			// Suggestions are a convenience; the fields are free text and work
			// perfectly well without them. Never block booking on this.
			.catch(() => []);
	}

	function attendee_field(fieldname, label, options, value, description) {
		return {
			fieldname: fieldname,
			label: label,
			fieldtype: "MultiSelect",
			options: options,
			default: value,
			description: description,
		};
	}

	/**
	 * @param {Object} opts
	 * @param {string} opts.title             Dialog title.
	 * @param {Object} opts.attendees         `{Sales, Production, Billing}` arrays.
	 * @param {Function} opts.on_submit       Receives `{starts_on, attendees, duration_minutes}`.
	 * @param {string} [opts.description]     Intro line above the fields.
	 * @param {string} [opts.default_start]   Datetime string; defaults to next business day 09:00.
	 * @returns {Promise<Object>} the dialog, once the attendee suggestions are in.
	 */
	function open(opts) {
		return attendee_options().then(function (options) {
			const pick = __("Pick from the list or type any address.");
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
						default: opts.default_start || default_slot(),
						description: __("When the meeting will be held — any time from now on."),
					},
					{
						fieldname: "duration_minutes",
						label: __("Duration (minutes)"),
						fieldtype: "Int",
						default: 15,
					},
					{ fieldtype: "Section Break", label: __("Attendees") },
					attendee_field(
						"sales",
						__("Sales"),
						options,
						join(opts.attendees && opts.attendees.Sales),
						__("Prefilled with the deal owner and the Sales list.") + " " + pick
					),
					attendee_field(
						"production",
						__("Production"),
						options,
						join(opts.attendees && opts.attendees.Production),
						pick
					),
					attendee_field(
						"billing",
						__("Billing"),
						options,
						join(opts.attendees && opts.attendees.Billing),
						pick
					),
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
		});
	}

	/**
	 * Book step 2 for an Opportunity — the whole act, from anywhere.
	 *
	 * Lives here rather than in the Opportunity form script because three callers
	 * need it and only one of them is a form: the Hand-Off Process tab, the
	 * Closed-Won prompt on the Kanban board, and the same prompt on the list view.
	 *
	 * @param {string} opportunity
	 * @param {Object} [opts]
	 * @param {string} [opts.title]        Dialog title (e.g. "Re-schedule Meeting").
	 * @param {Function} [opts.on_success] Called with the server result after booking.
	 */
	function schedule_for_opportunity(opportunity, opts) {
		opts = opts || {};
		return frappe
			.xcall("erpnext_enhancements.crm_enhancements.handoff.resolve_attendees", {
				opportunity: opportunity,
			})
			.then(function (attendees) {
				return open({
					title: opts.title || __("Schedule Hand-Off Meeting"),
					description: __(
						"Sales, Production and Billing are invited by email and the meeting is added to the calendar. Booking it is what unblocks project creation; recording that it happened is a separate step afterwards."
					),
					default_start: now_slot(),
					attendees: attendees,
					on_submit(values) {
						return frappe
							.xcall(
								"erpnext_enhancements.crm_enhancements.handoff.schedule_handoff_meeting",
								{
									opportunity: opportunity,
									starts_on: values.starts_on,
									duration_minutes: values.duration_minutes,
									attendees: values.attendees,
								}
							)
							.then(function (result) {
								frappe.show_alert({
									message: result.invited
										? __("Meeting created and invites sent.")
										: __("Meeting created, but the invite email failed — check the Error Log."),
									indicator: result.invited ? "green" : "orange",
								});
								if (typeof opts.on_success === "function") opts.on_success(result);
								return result;
							});
					},
				});
			});
	}

	return {
		open: open,
		default_slot: default_slot,
		now_slot: now_slot,
		schedule_for_opportunity: schedule_for_opportunity,
	};
})();
