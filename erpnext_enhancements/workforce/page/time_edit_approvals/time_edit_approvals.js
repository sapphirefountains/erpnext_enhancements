// Time Edit Approvals: the hand edits to time that wait for the session user's decision
// (workforce/time_edits.py, v1.832.0).
//
// When an employee changes the start or end of their own Job Interval, adds time by hand or
// starts work backdated, the entry waits and is left out of payroll until somebody else
// approves it. Who decides (Nikolas, 2026-11-19): the employee's reports_to supervisor, or an
// HR Manager or System Manager, never the employee or the person who made the edit. This page
// is where they do it. The server picks what is listed (time_edits.get_waiting_edits): a
// supervisor sees their direct reports' entries, an HR Manager or System Manager everyone's.
// A supervisor needs no read permission on Job Interval for it, and the page sends no list of
// employees: the endpoint decides.
//
// One card per entry, stacked, so it reads the same on a phone: who, which day, which project,
// the times before and now (or "Added by hand"), the reason given, and who edited it and when.
// Approve and Send Back call the two POST endpoints the Job Interval form uses
// (approve_time_edit, send_back_time_edit, which check the decider again), then the list
// reloads. Send Back asks for an optional note to the employee. The list reloads every time the
// page is shown, since another approver may have decided an entry since. One address, no views,
// so Back and Forward are the Desk's own. The stylesheet is time_edit_approvals.css beside this
// file (tea- prefix, --sf-* tokens).

frappe.pages["time-edit-approvals"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Time Edit Approvals"),
		single_column: true,
	});
	wrapper.time_edit_approvals = new TimeEditApprovals(page);
};

frappe.pages["time-edit-approvals"].on_page_show = function (wrapper) {
	if (wrapper.time_edit_approvals) {
		wrapper.time_edit_approvals.load();
	}
};

const TEA_API = "erpnext_enhancements.workforce.time_edits.";

function tea_esc(value) {
	return frappe.utils.escape_html(value == null ? "" : String(value));
}

class TimeEditApprovals {
	constructor(page) {
		this.page = page;
		this.busy = false;
		this.$body = $('<div class="tea-page"></div>').appendTo(page.body);
		page.set_secondary_action(__("Refresh"), () => this.load());
		this.$body.on("click", "[data-tea-action]", (event) => {
			const $button = $(event.currentTarget);
			this.decide($button.attr("data-tea-action"), $button.closest("[data-interval]").attr("data-interval"));
		});
	}

	load() {
		this.$body.html(`<p class="tea-muted">${__("Loading...")}</p>`);
		frappe.call({
			method: TEA_API + "get_waiting_edits",
			type: "GET",
			callback: (r) => this.render((r && r.message) || {}),
			error: () =>
				this.$body.html(`<p class="tea-empty">${__("The entries could not be loaded. Try Refresh.")}</p>`),
		});
	}

	// A time on the entry's own day reads as a time alone; any other day carries its date.
	when(value, day) {
		if (!value) return __("open");
		const same_day = day && String(value).slice(0, 10) === day;
		return frappe.datetime.str_to_user(value, same_day);
	}

	span(start, end, day) {
		return `${this.when(start, day)} - ${this.when(end, day)}`;
	}

	render(data) {
		const entries = data.entries || [];
		const intro =
			data.scope === "all"
				? __("Hand edits to time waiting for approval, for every employee.")
				: __("Hand edits to time waiting for approval, for the people who report to you.");
		const head = `
			<p class="tea-intro">${tea_esc(intro)} ${tea_esc(
				__("An entry is not counted toward payroll until it is approved.")
			)}</p>`;
		if (!entries.length) {
			this.$body.html(`${head}
				<div class="tea-empty">
					<p class="tea-empty-title">${tea_esc(__("Nothing is waiting for your approval."))}</p>
					<p class="tea-muted">${tea_esc(
						__("When a change to someone's time waits for your decision, it appears here.")
					)}</p>
				</div>`);
			return;
		}
		const cards = entries.map((entry) => this.card(entry)).join("");
		this.$body.html(`${head}
			<p class="tea-count">${tea_esc(__("{0} waiting", [entries.length]))}</p>
			<div class="tea-list">${cards}</div>`);
	}

	card(entry) {
		const day = entry.day || "";
		const fact = (label, value) =>
			`<div class="tea-fact"><dt>${tea_esc(label)}</dt><dd>${tea_esc(value)}</dd></div>`;
		const times = entry.added
			? fact(__("Added by hand"), this.span(entry.start, entry.end, day))
			: fact(__("Before"), this.span(entry.before_start, entry.before_end, day)) +
			  fact(__("Now"), this.span(entry.start, entry.end, day));
		const edited = entry.edited_on
			? __("{0}, {1}", [entry.edited_by, frappe.datetime.str_to_user(entry.edited_on)])
			: entry.edited_by;
		return `
			<article class="tea-card" data-interval="${tea_esc(entry.interval)}">
				<header class="tea-card-head">
					<h3 class="tea-name">${tea_esc(entry.employee_name)}</h3>
					<span class="tea-day">${tea_esc(day ? frappe.datetime.str_to_user(day) : "")}</span>
				</header>
				<p class="tea-project">${tea_esc(entry.project_name || entry.project)}</p>
				<dl class="tea-facts">
					${times}
					${fact(__("Reason given"), entry.reason || __("None given"))}
					${fact(__("Edited by"), edited)}
				</dl>
				<div class="tea-actions">
					<button type="button" class="btn btn-primary btn-sm tea-button" data-tea-action="approve">
						${tea_esc(__("Approve"))}
					</button>
					<button type="button" class="btn btn-default btn-sm tea-button" data-tea-action="send_back">
						${tea_esc(__("Send Back"))}
					</button>
				</div>
			</article>`;
	}

	decide(action, interval) {
		if (this.busy || !interval) return;
		if (action === "send_back") {
			frappe.prompt(
				[{ fieldname: "note", fieldtype: "Small Text", label: __("Note to the employee (optional)") }],
				(values) =>
					this.send("send_back_time_edit", interval, values.note || "", __("Sent back. The employee has been told.")),
				__("Send Back"),
				__("Send Back")
			);
		} else if (action === "approve") {
			this.send("approve_time_edit", interval, "", __("Approved. The entry counts toward payroll."));
		}
	}

	send(method, interval, note, message) {
		this.busy = true;
		frappe.call({
			method: TEA_API + method,
			args: { interval, note },
			freeze: true,
			callback: () => frappe.show_alert({ message, indicator: "green" }),
			always: () => {
				this.busy = false;
				this.load();
			},
		});
	}
}
