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
 * customer's PM) gets into the room at all. Typed text that is not an address
 * is flagged before submit, because the server's _dedupe() drops it. That check
 * uses the browser's email regex, which lets a few odd shapes through that the
 * server then rejects (an accented or quoted local part, an IP literal, a
 * hyphen at the edge of a domain label), so the server also returns what it
 * dropped as `not_invited` and the dialog says so after booking (1.534.1).
 *
 * Why typing works without `ignore_validation` (checked against v16, 1.529.0):
 * MultiSelect.validate does return "" for a value it doesn't recognise, but
 * set_formatted_input("") returns early, so only the control's internal value
 * is blanked. The text stays in the box, and Dialog.get_values() reads the box.
 *
 * Loaded globally via erpnext_enhancements.bundle.js rather than as a
 * doctype_js: the Closed-Won prompt in create_project_prompt.js opens this on the
 * Kanban board and the list view too, where no form script is loaded.
 *
 * **Google Calendar (1.733.0).** The server now books the meeting as a real Google
 * Calendar event, organized by the clicker's own connected calendar or the shared
 * fallback calendar, and only emails an invite when neither works
 * (crm_enhancements/handoff_calendar.py). The dialog says which way the invite will
 * go and offers "Connect my Google Calendar" when it is not the clicker's own.
 * `meeting_html()` / `bind_meeting()` put the Meet link and the guests' RSVPs on the
 * step cards; the RSVPs are fetched on a click, never on a form load.
 *
 * **A time that has passed is refused** (1.733.0), here and on the server. The
 * picker is a 24-hour clock, and EV00456 was booked at 09:21 for 01:30 that
 * morning: "1:30" meant 1:30 PM.
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
	let status_cache = null;

	/**
	 * Which way the next invite goes: the clicker's own Google Calendar, the shared
	 * fallback calendar, or email. Cached for the page; connecting a calendar
	 * leaves the page for Google anyway, so it is fresh when they come back.
	 */
	function calendar_status() {
		if (status_cache) return Promise.resolve(status_cache);
		return frappe
			.xcall("erpnext_enhancements.crm_enhancements.handoff_calendar.calendar_status")
			.then((status) => {
				status_cache = status || {};
				return status_cache;
			})
			// A note, not a precondition: the server decides the path on booking.
			.catch(() => ({}));
	}

	function calendar_note(status) {
		const esc = frappe.utils.escape_html;
		let text;
		if (status.calendar_path === "own_calendar") {
			text = __(
				"Google Calendar sends the invites from your calendar, with a Google Meet link. Guests see each other and can RSVP."
			);
		} else if (status.calendar_path === "fallback_calendar") {
			text = __(
				"Google Calendar sends the invites from the shared organizer calendar, with a Google Meet link. Connect your own calendar to be the organizer yourself."
			);
		} else if (status.calendar_path === "email") {
			text = __(
				"Invites go by email with a calendar file, and without a Meet link. Connect your Google Calendar to send real Google invites."
			);
		} else {
			return "";
		}
		const connect =
			status.calendar_path !== "own_calendar" && status.google_enabled
				? ` <button type="button" class="btn btn-xs btn-default ee-connect-gcal">${esc(
						__("Connect my Google Calendar")
					)}</button>`
				: "";
		return `<p class="text-muted small">${esc(text)}${connect}</p>`;
	}

	/**
	 * Send the user to Google to allow access, through Frappe's own authorize flow.
	 * The server creates their Google Calendar record (push only, never importing
	 * their own events) and returns Google's consent URL.
	 */
	function connect_google_calendar() {
		calendar_status().then((status) => {
			const esc = frappe.utils.escape_html;
			frappe.confirm(
				`<p>${esc(
					__(
						"You'll go to Google to let ERPNext add meetings to your calendar. ERPNext only adds the meetings you schedule; it never imports your own events. Google then brings you back to your Google Calendar settings in ERPNext; come back to this page and schedule the meeting."
					)
				)}</p><p class="text-muted small">${esc(
					__(
						'If Google says "redirect_uri_mismatch", the site\'s Google sign-in is missing this redirect URI. Send it to a System Manager: {0}',
						[status.redirect_uri || ""]
					)
				)}</p>`,
				() => {
					frappe
						.xcall("erpnext_enhancements.crm_enhancements.handoff_calendar.connect_google_calendar")
						.then((result) => {
							if (result && result.url) window.location.assign(result.url);
						});
				}
			);
		});
	}

	/** True when `value` (the Datetime field's string) is earlier than now, less some slack. */
	function has_passed(value) {
		const start = frappe.datetime.str_to_obj(value);
		const now = frappe.datetime.str_to_obj(frappe.datetime.now_datetime());
		return !!(start && now && start.getTime() < now.getTime() - PAST_GRACE_MINUTES * 60 * 1000);
	}

	/** Matches PAST_GRACE_MINUTES in handoff.py, which refuses the same thing. */
	const PAST_GRACE_MINUTES = 30;
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
		return Promise.all([attendee_options(), calendar_status()]).then(function ([options, status]) {
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
						fieldtype: "HTML",
						fieldname: "calendar_note",
						options: calendar_note(status),
					},
					{
						fieldname: "starts_on",
						label: __("Date / Time"),
						fieldtype: "Datetime",
						reqd: 1,
						default: opts.default_start || default_slot(),
						description: __(
							"When the meeting will be held — any time from now on. 24-hour clock: 1:30 PM is 13:30."
						),
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

					if (has_passed(values.starts_on)) {
						frappe.msgprint({
							title: __("That time has passed"),
							message: __(
								"{0} is in the past. The clock is 24-hour, so 1:30 PM is 13:30.",
								[frappe.datetime.str_to_user(values.starts_on)]
							),
							indicator: "orange",
						});
						return;
					}

					if (!attendees.length) {
						frappe.msgprint({
							title: __("No attendees"),
							message: __("Add at least one attendee before creating the meeting."),
							indicator: "orange",
						});
						return;
					}

					// The server's _dedupe() drops anything that is not an address,
					// so a typo would simply not be invited. Catch what the browser
					// regex can here, while it can still be fixed; the server's
					// `not_invited` covers the rest after booking.
					const invalid = attendees.filter(function (address) {
						return !frappe.utils.validate_type(address, "email");
					});
					if (invalid.length) {
						frappe.msgprint({
							title: __("Not an email address"),
							message: __("Fix or remove: {0}", [
								frappe.utils.escape_html(invalid.join(", ")),
							]),
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
						.then((result) => {
							dialog.hide();
							// What the server dropped as not an address. The meeting is
							// booked either way; this is so nobody assumes they were invited.
							const not_invited = (result && result.not_invited) || [];
							if (not_invited.length) {
								frappe.msgprint({
									title: __("Not invited"),
									message: __(
										"The meeting was booked, but these are not valid email addresses and were left off the invite: {0}",
										[frappe.utils.escape_html(not_invited.join(", "))]
									),
									indicator: "orange",
								});
							}
						})
						// Re-enable rather than leaving a dead button: a failed send is
						// usually a fixable address, and the user should get to retry
						// without rebuilding the whole dialog.
						.catch(() => dialog.enable_primary_action());
				},
			});
			dialog.show();
			dialog.$wrapper.find(".ee-connect-gcal").on("click", connect_google_calendar);
			return dialog;
		});
	}

	/**
	 * The alert after booking, worded for the way the invite actually went, plus
	 * every Google failure the server reported (it fell back past them).
	 *
	 * @param {Object} result  The schedule endpoint's response.
	 * @param {string} what    "Hand-off meeting" / "Launch meeting".
	 */
	function report_booking(result, what) {
		const esc = frappe.utils.escape_html;
		result = result || {};
		const google = !!result.calendar_path && result.calendar_path !== "email";
		let message;
		let indicator = "green";
		if (google) {
			message = result.rescheduled
				? __("{0} moved. Google Calendar sent the change to every guest.", [what])
				: __("{0} booked. Google Calendar sent the invites, with a Meet link.", [what]);
		} else if (result.invited) {
			message = result.rescheduled
				? __("{0} moved. The updated invite went by email.", [what])
				: __("{0} booked. The invites went by email.", [what]);
		} else {
			message = __("{0} booked, but the invite email failed — check the Error Log.", [what]);
			indicator = "orange";
		}
		frappe.show_alert({ message: message, indicator: indicator });
		const warnings = result.warnings || [];
		if (warnings.length) {
			frappe.msgprint({
				title: __("Google Calendar"),
				indicator: "orange",
				message:
					`<p>${esc(__("The meeting is booked. Google Calendar had trouble on the way:"))}</p><ul>` +
					warnings.map((line) => `<li>${esc(line)}</li>`).join("") +
					"</ul>",
			});
		}
	}

	const RESPONSES = {
		accepted: { label: "Accepted", color: "#16a34a" },
		declined: { label: "Declined", color: "#dc2626" },
		tentative: { label: "Maybe", color: "#d97706" },
		needsAction: { label: "No reply yet", color: "var(--text-muted)" },
	};

	function safe_link(url) {
		return typeof url === "string" && url.indexOf("https://") === 0 ? url : "";
	}

	/**
	 * The booked meeting on a step card: Meet link, and a "Guests & RSVPs" toggle.
	 * Built from what the form already loaded (the Event row), so it costs no
	 * Google call until someone clicks the toggle.
	 *
	 * @param {Object} meeting  `{event, google, meet_link, status}` from the server.
	 */
	function meeting_html(meeting) {
		if (!meeting || !meeting.event || meeting.status === "Cancelled") return "";
		const esc = frappe.utils.escape_html;
		const parts = [];
		const meet = safe_link(meeting.meet_link);
		if (meet) {
			parts.push(
				`<a href="${esc(meet)}" target="_blank" rel="noopener noreferrer">${esc(__("Join Google Meet"))}</a>`
			);
		}
		if (meeting.google) {
			parts.push(
				`<a href="#" class="ee-meeting-guests" data-event="${esc(meeting.event)}">${esc(
					__("Guests & RSVPs")
				)}</a>`
			);
		} else {
			parts.push(`<span class="text-muted">${esc(__("Invited by email"))}</span>`);
		}
		return `<div class="ee-meeting" style="font-size:11px;">${parts.join(
			" · "
		)}<div class="ee-meeting-guest-list"></div></div>`;
	}

	function guest_list_html(data) {
		const esc = frappe.utils.escape_html;
		if (!data || !data.google) return "";
		if (data.error) return `<div class="text-muted">${esc(data.error)}</div>`;
		let html = "";
		if (data.status === "cancelled") {
			html += `<div class="text-muted">${esc(__("This meeting was cancelled in Google Calendar."))}</div>`;
		}
		html += (data.guests || [])
			.map((guest) => {
				const response = RESPONSES[guest.response] || RESPONSES.needsAction;
				const who = guest.name ? `${guest.name} (${guest.email})` : guest.email;
				return `<div>${esc(who)}${guest.organizer ? ` · ${esc(__("organizer"))}` : ""} — <span style="color:${
					response.color
				};">${esc(__(response.label))}</span></div>`;
			})
			.join("");
		const google_link = safe_link(data.html_link);
		if (google_link) {
			html += `<div><a href="${esc(google_link)}" target="_blank" rel="noopener noreferrer">${esc(
				__("Open in Google Calendar")
			)}</a></div>`;
		}
		return html;
	}

	/** Wire the "Guests & RSVPs" toggles inside `$wrapper` (rendered by meeting_html). */
	function bind_meeting($wrapper) {
		$wrapper.find(".ee-meeting-guests").on("click", function (e) {
			e.preventDefault();
			const $link = $(this);
			const $list = $link.closest(".ee-meeting").find(".ee-meeting-guest-list");
			if ($list.children().length) {
				$list.empty();
				return;
			}
			$list.html(`<div class="text-muted">${frappe.utils.escape_html(__("Loading…"))}</div>`);
			frappe
				.xcall("erpnext_enhancements.crm_enhancements.handoff_calendar.meeting_guests", {
					event: $link.attr("data-event"),
				})
				.then((data) => $list.html(guest_list_html(data)))
				.catch(() => $list.empty());
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
								report_booking(result, __("Hand-off meeting"));
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
		report_booking: report_booking,
		meeting_html: meeting_html,
		bind_meeting: bind_meeting,
		connect_google_calendar: connect_google_calendar,
	};
})();
