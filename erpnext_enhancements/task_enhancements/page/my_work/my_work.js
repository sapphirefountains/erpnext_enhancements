// My Work -- what you have been assigned, what you have handed out, and what nobody has picked up
// yet (WI-081 Phase 1). It replaces the ToDo list as the place to start the day: every row leads
// with the RECORD ("Smith Residence rebuild · Opportunity · $48,000") instead of "Assignment for
// Opportunity CRM-OPP-2026-00187", and every row can be finished, snoozed, handed on or answered
// where it is. Underneath it is Frappe's own assignments: a row is a ToDo, and the server
// (erpnext_enhancements.api.my_work) closes one through the ToDo controller, so the record's
// assignee avatars and Assign To sidebar stay in step.
//
// Address: /desk/my-work is "Assigned to me", /desk/my-work/by-me "Assigned by me",
// /desk/my-work/unclaimed the unclaimed list and /desk/my-work/tidy the backlog triage (System
// Managers). Each is a route segment, so Back and Forward step between them (the router re-shows
// this page and on_page_show reads the segment). ?todo=<name> -- the link an email or a bell
// entry for an assignment with no record carries -- scrolls to that row and marks it.
//
// Buckets: Overdue, Today, No due date, This week, Later. "No due date" exists because a ToDo's
// date is usually the day it was made, not a due date (task_enhancements/assignments.py); the
// server decides the bucket, so this page, the sidebar count and the banner cannot disagree.
// Drag a row onto Today, No due date, This week or Later to move it there (desktop); the Snooze
// menu does the same anywhere, phones included.
//
// The forms (Done with a note, Reassign, Reply, Take back) open inside the row, not in a dialog:
// no panel to lose to Back, and nothing covering the list on a phone. States, toasts, chips and the
// server calls come from sf_kit; the stylesheet is my_work.css beside this file (myw- prefix).

frappe.pages["my-work"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({ parent: wrapper, title: __("My Work"), single_column: true });
	wrapper.myw = make_my_work(wrapper, page);
};

frappe.pages["my-work"].on_page_show = function (wrapper) {
	if (wrapper.myw) wrapper.myw.show();
};

function make_my_work(wrapper, page) {
	const API = "erpnext_enhancements.api.my_work.";
	// The roles that see the Team board (api.my_work.LEAD_ROLES).
	const LEAD_ROLES = ["System Manager", "President", "Executive Team", "Operations Manager", "Production Manager"];
	const is_lead = LEAD_ROLES.some((role) => frappe.user.has_role(role));
	const TABS = [
		{ view: "mine", segment: "", label: __("Assigned to me") },
		{ view: "delegated", segment: "by-me", label: __("Assigned by me") },
		{ view: "unclaimed", segment: "unclaimed", label: __("Unclaimed & queues") },
		{ view: "calendar", segment: "calendar", label: __("Calendar") },
	].concat(is_lead ? [{ view: "team", segment: "team", label: __("Team") }] : []);
	// ToDo.custom_repeat's options (assignments.REPEATS on the server).
	const REPEATS = ["Daily", "Weekly", "Every 2 weeks", "Monthly", "Quarterly", "Yearly"];
	const BUCKETS = [
		{ key: "overdue", label: __("Overdue"), drop: false },
		{ key: "today", label: __("Today"), drop: true },
		{ key: "undated", label: __("No due date"), drop: true },
		{ key: "week", label: __("This week"), drop: true },
		{ key: "later", label: __("Later"), drop: true },
	];
	const PAGE_SIZE = 40;
	const is_manager = frappe.user.has_role("System Manager");

	const state = {
		view: "mine",
		data: null,
		open: null, // { name, kind } -- the one inline form open, if any
		more: {}, // bucket -> rows shown beyond PAGE_SIZE
		highlight: null,
		loading: 0,
		today: frappe.datetime.get_today(),
		tab_counts: {},
		board: null, // the Team view's answer
		team: "", // the Team view's filter
		cal: null, // the Calendar view's answer
		month: null, // the Calendar view's month, "YYYY-MM-01"
	};

	const $root = $('<div class="myw-page"></div>').appendTo(page.body);
	const $tabs = $('<div class="myw-tabs" role="tablist"></div>').appendTo($root);
	const $body = $('<div class="myw-body"></div>').appendTo($root);

	if (is_manager) {
		page.add_inner_button(__("Tidy the backlog"), () => frappe.set_route("my-work", "tidy"));
	}

	// sf_kit from the shared loader (public/js/sf_kit_loader.js). A Desk opened before the deploy
	// that added the loader asks frappe.require itself; one whose kit never arrives gets bare_kit.
	let kit_promise = null;
	function kit() {
		if (!kit_promise) {
			kit_promise =
				typeof window.sf_kit_ready === "function"
					? window.sf_kit_ready()
					: new Promise((resolve) =>
							frappe.require(["sf_kit.bundle.js", "sf_kit.bundle.css"], () =>
								resolve(window.sf_kit && window.sf_kit.state ? window.sf_kit : bare_kit())
							)
					  );
		}
		return kit_promise;
	}

	function bare_kit() {
		const esc = (v) => frappe.utils.escape_html(v == null ? "" : String(v));
		const failed = (x) => ({ status: (x && x.status) || 0, message: "", exc_type: "", session_expired: false });
		return {
			esc,
			call: (method, args) =>
				Promise.resolve(frappe.call({ method, args: args || {} })).then(
					(r) => r && r.message,
					(x) => Promise.reject(failed(x))
				),
			state: {
				render: (el, kind, o) => {
					const $el = $(el).empty().append($("<div></div>").text((o && o.title) || ""));
					if (o && o.on_action) {
						$('<button type="button" class="btn btn-default btn-sm"></button>')
							.text(o.action_label || __("Try again"))
							.on("click", o.on_action)
							.appendTo($el);
					}
				},
			},
			chip: (label) => (label ? `<span>${esc(label)}</span>` : ""),
			toast: (message) => frappe.show_alert({ message, indicator: "blue" }),
		};
	}

	// ------------------------------------------------------------------ routing

	function show() {
		const route = frappe.get_route() || [];
		const segment = route[1] || "";
		const options = frappe.route_options || {};
		if (options.todo) {
			state.highlight = String(options.todo);
			delete options.todo;
			if (!Object.keys(options).length) frappe.route_options = null;
		}
		if (segment === "tidy") {
			if (!is_manager) {
				frappe.set_route("my-work");
				return;
			}
			$tabs.hide();
			show_tidy();
			return;
		}
		$tabs.show();
		const tab = TABS.find((t) => t.segment === segment) || TABS[0];
		if (tab.view !== state.view) {
			state.view = tab.view;
			state.data = null;
			state.more = {};
		}
		state.open = null;
		load();
	}

	function go_tab(view) {
		const tab = TABS.find((t) => t.view === view) || TABS[0];
		if (tab.segment) frappe.set_route("my-work", tab.segment);
		else frappe.set_route("my-work");
	}

	// ------------------------------------------------------------------ loading

	function load() {
		if (state.view === "team") return load_team();
		if (state.view === "calendar") return load_calendar();
		const ticket = ++state.loading;
		return kit().then((sf) => {
			if (!state.data) sf.state.render($body[0], "loading");
			draw_tabs(sf);
			return sf.call(API + "get_items", { view: state.view }, { quiet: true, method: "GET" }).then(
				(data) => {
					if (ticket !== state.loading) return;
					state.data = data;
					state.today = data.today;
					state.tab_counts = data.tabs || {};
					draw(sf);
				},
				(err) => {
					if (ticket !== state.loading) return;
					sf.state.render($body[0], "error", {
						title: __("Your work could not be loaded."),
						body: (err && err.message) || "",
						on_action: load,
					});
				}
			);
		});
	}

	function refresh_count() {
		const mw = window.erpnext_enhancements && window.erpnext_enhancements.my_work;
		if (mw && typeof mw.refresh_count === "function") mw.refresh_count();
	}

	// ------------------------------------------------------------------ drawing

	function draw_tabs(sf) {
		const counts = state.tab_counts || {};
		$tabs.html(
			TABS.map((t) => {
				const selected = t.view === state.view;
				const n = counts[t.view];
				return (
					`<button type="button" class="myw-tab" role="tab" aria-selected="${selected}" data-view="${t.view}">` +
					`${sf.esc(t.label)}${n != null ? ` <span class="myw-tab-count">${sf.esc(n)}</span>` : ""}</button>`
				);
			}).join("")
		);
	}

	function draw(sf) {
		draw_tabs(sf);
		const data = state.data;
		if (!data.items.length) {
			const empty = {
				mine: __("Nothing assigned to you. Enjoy it."),
				delegated: __("You have not handed anything out that is still open."),
				unclaimed: __("Nothing is waiting for an owner."),
			}[state.view];
			sf.state.render($body[0], "empty", { title: empty });
			return;
		}
		const by_bucket = {};
		data.items.forEach((item) => (by_bucket[item.bucket] = by_bucket[item.bucket] || []).push(item));
		const html = BUCKETS.map((b) => bucket_html(sf, b, by_bucket[b.key] || [])).join("");
		$body.html(`<div class="myw-buckets">${html}</div>`);
		if (state.open) open_form(state.open.name, state.open.kind);
		if (state.highlight) {
			const $row = $body.find(`.myw-item[data-name="${css_escape(state.highlight)}"]`);
			if ($row.length) {
				$row.addClass("myw-item--marked");
				$row[0].scrollIntoView({ block: "center" });
			}
			state.highlight = null;
		}
	}

	function bucket_html(sf, bucket, items) {
		const empty = !items.length;
		if (empty && !bucket.drop) return "";
		const shown = PAGE_SIZE + (state.more[bucket.key] || 0);
		const rows = items.slice(0, shown).map((item) => item_html(sf, item)).join("");
		const rest = items.length - shown;
		const count_tone = bucket.key === "overdue" ? "danger" : "neutral";
		return (
			`<section class="myw-bucket${empty ? " myw-bucket--empty" : ""}" data-bucket="${bucket.key}"` +
			`${bucket.drop ? ' data-drop="1"' : ""}>` +
			`<h3 class="myw-bucket-head">${sf.esc(bucket.label)} ${empty ? "" : sf.chip(String(items.length), count_tone)}</h3>` +
			(empty
				? `<div class="myw-bucket-drop">${sf.esc(__("Drop here"))}</div>`
				: `<ul class="myw-list">${rows}</ul>`) +
			(rest > 0
				? `<button type="button" class="sf-btn sf-btn--quiet myw-more" data-bucket="${bucket.key}">` +
				  `${sf.esc(__("Show {0} more", [Math.min(rest, PAGE_SIZE)]))}</button>`
				: "") +
			`</section>`
		);
	}

	function item_html(sf, item) {
		const link = item.reference_type && item.reference_name && !item.record_missing
			? frappe.utils.get_form_link(item.reference_type, item.reference_name)
			: null;
		const title = link
			? `<a class="myw-title" href="${sf.esc(link)}">${sf.esc(item.title)}</a>`
			: `<span class="myw-title myw-title--plain">${sf.esc(item.title)}</span>`;
		const chips = chips_html(sf, item);
		const ask = item.ask && item.ask !== item.title ? `<div class="myw-ask">${sf.esc(item.ask)}</div>` : "";
		const people = who_line(item);
		const facts = (item.facts || []).map((f) => `${f[0]}: ${f[1]}`);
		if (people) facts.push(people);
		const draggable = item.can.reschedule ? ' draggable="true"' : "";
		const details = !link
			? ` <a class="myw-details" href="${sf.esc(frappe.utils.get_form_link("ToDo", item.name))}">${sf.esc(__("Details"))}</a>`
			: "";
		return (
			`<li class="myw-item" data-name="${sf.esc(item.name)}"${draggable}>` +
			`<div class="myw-item-main">` +
			`<div class="myw-item-head">${title}<span class="myw-chips">${chips}</span></div>` +
			ask +
			`<div class="myw-facts">${sf.esc(facts.join(" · "))}${details}</div>` +
			`</div>` +
			`<div class="myw-actions">${actions_html(sf, item)}</div>` +
			`<div class="myw-inline"></div>` +
			`</li>`
		);
	}

	function chips_html(sf, item) {
		return [
			item.doctype_label ? sf.chip(item.doctype_label, "neutral") : "",
			item.priority === "High" ? sf.chip(__("High"), "warn") : "",
			item.is_new ? sf.chip(__("New"), "good") : "",
			item.record_missing ? sf.chip(__("Record deleted"), "danger") : "",
			when_chip(sf, item),
			item.team && !item.allocated_to ? sf.chip(__("{0} team", [item.team]), "build") : "",
			item.repeat ? sf.chip(__("Repeats: {0}", [__(item.repeat)]), "neutral") : "",
			checklist_chip(sf, item),
		].join(" ");
	}

	function checklist_chip(sf, item) {
		const steps = item.checklist || [];
		if (!steps.length) return "";
		const done = steps.filter((st) => st.done).length;
		return sf.chip(__("{0}/{1} steps", [done, steps.length]), done === steps.length ? "good" : "neutral");
	}

	function when_chip(sf, item) {
		const today = state.today;
		if (item.bucket === "overdue") {
			const late = frappe.datetime.get_day_diff(today, item.due);
			return sf.chip(late === 1 ? __("1 day late") : __("{0} days late", [late]), "danger");
		}
		if (item.bucket === "today") return sf.chip(__("Due today"), "warn");
		if (item.due) return sf.chip(__("Due {0}", [day_label(item.due)]), "neutral");
		return "";
	}

	function who_line(item) {
		const ago = assigned_ago(item.assigned_on);
		if (state.view === "delegated") {
			return __("With {0}", [item.allocated_to_name || __("nobody")]) + " · " + ago;
		}
		if (state.view === "unclaimed") return ago;
		if (item.assigned_by && item.assigned_by !== frappe.session.user) {
			return __("From {0}", [item.assigned_by_name]) + " · " + ago;
		}
		return ago;
	}

	function assigned_ago(day) {
		if (!day) return "";
		const n = frappe.datetime.get_day_diff(state.today, day);
		if (n <= 0) return __("Assigned today");
		if (n === 1) return __("Assigned yesterday");
		return __("Assigned {0} days ago", [n]);
	}

	function day_label(day) {
		const weekday = typeof moment === "function" ? moment(day).format("ddd") + " " : "";
		return weekday + frappe.datetime.str_to_user(day);
	}

	function actions_html(sf, item) {
		const can = item.can || {};
		const b = (act, label, kind) =>
			`<button type="button" class="sf-btn sf-btn--${kind || "secondary"} myw-btn" data-act="${act}">${sf.esc(label)}</button>`;
		const out = [];
		if (can.claim) out.push(b("claim", __("Take it"), "primary"));
		if (can.complete) out.push(b("complete", __("Done"), "primary"));
		if (can.reschedule) out.push(snooze_html(sf));
		if (can.reply) out.push(b("reply", __("Reply"), "quiet"));
		if (can.reassign) out.push(b("reassign", __("Reassign"), "quiet"));
		if (can.nudge) out.push(b("nudge", __("Nudge"), "secondary"));
		if (can.withdraw) out.push(b("withdraw", __("Take back"), "quiet"));
		if (can.checklist) {
			const steps = item.checklist || [];
			out.push(b("checklist", steps.length ? __("Steps") : __("Add steps"), "quiet"));
			out.push(repeat_html(sf, item));
		}
		return out.join("");
	}

	function repeat_html(sf, item) {
		const opt = (value, label) =>
			`<button type="button" class="dropdown-item" data-act="repeat" data-repeat="${sf.esc(value)}"` +
			`${(item.repeat || "") === value ? ' aria-current="true"' : ""}>${sf.esc(label)}</button>`;
		return (
			`<div class="dropdown myw-snooze">` +
			`<button type="button" class="sf-btn sf-btn--quiet myw-btn dropdown-toggle" data-toggle="dropdown" aria-haspopup="true" aria-expanded="false">${sf.esc(__("Repeat"))}</button>` +
			`<div class="dropdown-menu dropdown-menu-right">` +
			REPEATS.map((r) => opt(r, __(r))).join("") +
			opt("", __("Does not repeat")) +
			`</div></div>`
		);
	}

	function snooze_html(sf) {
		const opt = (value, label) =>
			`<button type="button" class="dropdown-item" data-act="snooze" data-when="${value}">${sf.esc(label)}</button>`;
		return (
			`<div class="dropdown myw-snooze">` +
			`<button type="button" class="sf-btn sf-btn--secondary myw-btn dropdown-toggle" data-toggle="dropdown" aria-haspopup="true" aria-expanded="false">${sf.esc(__("Snooze"))}</button>` +
			`<div class="dropdown-menu dropdown-menu-right">` +
			opt("tomorrow", __("Tomorrow")) +
			opt("monday", __("Next Monday")) +
			opt("week", __("In a week")) +
			opt("pick", __("Pick a day…")) +
			opt("none", __("No due date")) +
			`</div></div>`
		);
	}

	// ------------------------------------------------------------------ dates

	function next_monday(today) {
		const dow = frappe.datetime.str_to_obj(today).getDay();
		return frappe.datetime.add_days(today, ((8 - dow) % 7) || 7);
	}

	function snooze_date(when) {
		const today = state.today;
		if (when === "tomorrow") return frappe.datetime.add_days(today, 1);
		if (when === "monday") return next_monday(today);
		if (when === "week") return frappe.datetime.add_days(today, 7);
		if (when === "none") return "";
		return null;
	}

	// The day a row dropped on a bucket gets. This week means soon (tomorrow); Later is the Monday
	// after next, which is always past the seven days "This week" covers.
	function drop_date(bucket) {
		const today = state.today;
		if (bucket === "today") return today;
		if (bucket === "undated") return "";
		if (bucket === "week") return frappe.datetime.add_days(today, 1);
		if (bucket === "later") return frappe.datetime.add_days(next_monday(today), 7);
		return null;
	}

	// ------------------------------------------------------------------ acting

	function find(name) {
		const pools = [
			(state.data && state.data.items) || [],
			(state.cal && state.cal.items) || [],
			...((state.board && state.board.people) || []).map((p) => p.items),
			...((state.board && state.board.queues) || []).map((q) => q.items),
		];
		for (const pool of pools) {
			const hit = pool.find((i) => i.name === name);
			if (hit) return hit;
		}
		return null;
	}

	function act(method, args, done_message, keep_open) {
		return kit().then((sf) =>
			sf.call(API + method, args, { quiet: true }).then(
				() => {
					if (!keep_open) state.open = null;
					if (done_message) sf.toast(done_message, { tone: "success" });
					refresh_count();
					return load();
				},
				(err) => {
					sf.toast((err && err.message) || __("That did not work. Try again."), { tone: "warning" });
					throw err;
				}
			)
		);
	}

	function open_form(name, kind) {
		const item = find(name);
		const $row = $body.find(`.myw-item[data-name="${css_escape(name)}"]`);
		$body.find(".myw-inline").empty();
		if (!item || !$row.length) {
			state.open = null;
			return;
		}
		state.open = { name, kind };
		kit().then((sf) => {
			const $inline = $row.find(".myw-inline");
			const form = FORMS[kind];
			if (!form) return;
			$inline.html(
				`<form class="myw-form" data-kind="${kind}">` +
				form.html(sf, item) +
				`<div class="myw-form-row">` +
				`<button type="submit" class="sf-btn sf-btn--primary">${sf.esc(form.submit(item))}</button>` +
				`<button type="button" class="sf-btn sf-btn--quiet" data-act="close-form">${sf.esc(__("Cancel"))}</button>` +
				`</div></form>`
			);
			if (form.mount) form.mount($inline, item);
			const $first = $inline.find("textarea, input").first();
			if ($first.length) $first.trigger("focus");
		});
	}

	const FORMS = {
		complete: {
			html: (sf, item) =>
				`<label class="myw-form-label" for="myw-note">${sf.esc(
					item.assigned_by && item.assigned_by !== frappe.session.user
						? __("Note for {0} (optional)", [item.assigned_by_name])
						: __("Note (optional)")
				)}</label>` +
				`<textarea id="myw-note" name="note" rows="2" class="myw-input"></textarea>` +
				`<div class="myw-hint">${sf.esc(__("It goes on the record's timeline."))}</div>`,
			submit: () => __("Mark done"),
			send: (item, $form) =>
				act("complete", { todo: item.name, note: $form.find("[name=note]").val() }, __("Done: {0}", [item.title])),
		},
		reply: {
			html: (sf, item) =>
				`<label class="myw-form-label" for="myw-reply">${sf.esc(__("Reply to {0}", [item.assigned_by_name]))}</label>` +
				`<textarea id="myw-reply" name="message" rows="3" class="myw-input"></textarea>` +
				`<div class="myw-hint">${sf.esc(__("Posted on the record with an @mention, so they are told."))}</div>`,
			submit: () => __("Send"),
			send: (item, $form) => {
				const message = ($form.find("[name=message]").val() || "").trim();
				if (!message) return Promise.resolve();
				return act("reply", { todo: item.name, message }, __("Reply sent"));
			},
		},
		reassign: {
			html: (sf) =>
				`<div class="myw-user"></div>` +
				`<label class="myw-form-label" for="myw-handover">${sf.esc(__("Note (optional)"))}</label>` +
				`<textarea id="myw-handover" name="note" rows="2" class="myw-input"></textarea>`,
			mount: ($inline) => {
				const control = frappe.ui.form.make_control({
					parent: $inline.find(".myw-user"),
					df: {
						fieldtype: "Link",
						options: "User",
						fieldname: "user",
						label: __("Give it to"),
						get_query: () => ({ filters: { enabled: 1, user_type: "System User" } }),
					},
					render_input: true,
				});
				control.refresh();
				$inline.data("user_control", control);
			},
			submit: () => __("Reassign"),
			send: (item, $form) => {
				const control = $form.closest(".myw-inline").data("user_control");
				const user = control && control.get_value();
				if (!user) {
					return kit().then((sf) => sf.toast(__("Pick who it goes to."), { tone: "warning" }));
				}
				return act(
					"reassign",
					{ todo: item.name, user, note: $form.find("[name=note]").val() },
					__("Reassigned: {0}", [item.title])
				);
			},
		},
		withdraw: {
			html: (sf, item) =>
				`<div class="myw-form-label">${sf.esc(
					__("Take this back from {0}? They will be told.", [item.allocated_to_name])
				)}</div>`,
			submit: () => __("Take it back"),
			send: (item) => act("withdraw", { todo: item.name }, __("Taken back: {0}", [item.title])),
		},
		// Phase 3: the checklist, in the row. A tick saves at once; the box adds a step.
		checklist: {
			html: (sf, item) =>
				`<ul class="myw-steps">` +
				(item.checklist || [])
					.map(
						(st) =>
							`<li class="myw-step"><label><input type="checkbox" data-step="${sf.esc(st.name)}"${st.done ? " checked" : ""}> ` +
							`<span>${sf.esc(st.item)}</span></label>` +
							`<button type="button" class="sf-btn sf-btn--quiet myw-step-remove" data-step-remove="${sf.esc(st.name)}" aria-label="${sf.esc(
								__("Remove step")
							)}">×</button></li>`
					)
					.join("") +
				`</ul>` +
				`<label class="myw-form-label" for="myw-step">${sf.esc(__("Add a step"))}</label>` +
				`<input id="myw-step" type="text" name="step" class="myw-input" maxlength="200">`,
			submit: () => __("Add"),
			send: (item, $form) => {
				const step = ($form.find("[name=step]").val() || "").trim();
				if (!step) return Promise.resolve();
				return act("checklist_add", { todo: item.name, item: step }, null, true);
			},
		},
		pick: {
			html: (sf, item) =>
				`<label class="myw-form-label" for="myw-day">${sf.esc(__("Due on"))}</label>` +
				`<input id="myw-day" type="date" name="day" class="myw-input" min="${sf.esc(state.today)}" value="${sf.esc(item.due || "")}">`,
			submit: () => __("Set the day"),
			send: (item, $form) => {
				const day = $form.find("[name=day]").val();
				if (!day) return Promise.resolve();
				return act("reschedule", { todo: item.name, date: day }, __("Moved to {0}", [day_label(day)]));
			},
		},
	};

	function snooze(item, when) {
		if (when === "pick") {
			open_form(item.name, "pick");
			return;
		}
		const day = snooze_date(when);
		if (day === null) return;
		act(
			"reschedule",
			{ todo: item.name, date: day },
			day ? __("Snoozed to {0}", [day_label(day)]) : __("Due date taken off")
		);
	}

	// ------------------------------------------------------------------ events

	$tabs.on("click", ".myw-tab", function () {
		go_tab($(this).attr("data-view"));
	});

	$body.on("click", "[data-act]", function (e) {
		const action = $(this).attr("data-act");
		if (action === "close-form") {
			state.open = null;
			$(this).closest(".myw-inline").empty();
			return;
		}
		const name = $(this).closest(".myw-item").attr("data-name");
		const item = find(name);
		if (!item) return;
		e.preventDefault();
		if (action === "snooze") return snooze(item, $(this).attr("data-when"));
		if (action === "claim") return act("claim", { todo: name }, __("It is yours: {0}", [item.title]));
		if (action === "nudge") return act("nudge", { todo: name }, __("Nudged {0}", [item.allocated_to_name]));
		if (action === "repeat") {
			const repeat = $(this).attr("data-repeat") || "";
			return act(
				"set_repeat",
				{ todo: name, repeat },
				repeat ? __("Repeats: {0}", [__(repeat)]) : __("It does not repeat now")
			);
		}
		if (FORMS[action]) {
			if (state.open && state.open.name === name && state.open.kind === action) {
				state.open = null;
				$(this).closest(".myw-item").find(".myw-inline").empty();
				return;
			}
			open_form(name, action);
		}
	});

	$body.on("submit", ".myw-form", function (e) {
		e.preventDefault();
		const $form = $(this);
		const name = $form.closest(".myw-item").attr("data-name");
		const item = find(name);
		const form = FORMS[$form.attr("data-kind")];
		if (!item || !form) return;
		const $submit = $form.find("[type=submit]").prop("disabled", true);
		Promise.resolve(form.send(item, $form)).catch(() => {}).finally(() => $submit.prop("disabled", false));
	});

	$body.on("change", ".myw-steps [data-step]", function () {
		const $box = $(this);
		const name = $box.closest(".myw-item").attr("data-name");
		const item = find(name);
		const step = (item && item.checklist || []).find((st) => st.name === $box.attr("data-step"));
		$box.prop("disabled", true);
		kit()
			.then((sf) =>
				sf.call(API + "checklist_toggle", { todo: name, row: $box.attr("data-step"), done: $box.is(":checked") ? 1 : 0 }, { quiet: true })
			)
			.then(() => {
				if (step) step.done = $box.is(":checked") ? 1 : 0;
				return kit().then((sf) => $box.closest(".myw-item").find(".myw-chips").html(chips_html(sf, item)));
			})
			.catch(() => $box.prop("checked", !$box.is(":checked")))
			.finally(() => $box.prop("disabled", false));
	});

	$body.on("click", "[data-step-remove]", function (e) {
		e.preventDefault();
		const name = $(this).closest(".myw-item").attr("data-name");
		act("checklist_remove", { todo: name, row: $(this).attr("data-step-remove") }, null, true);
	});

	// Enter sends a note or reply; Shift+Enter is a new line. Escape closes the form.
	$body.on("keydown", ".myw-form textarea", function (e) {
		if (e.key === "Enter" && !e.shiftKey) {
			e.preventDefault();
			$(this).closest("form").trigger("submit");
		}
	});
	$body.on("keydown", ".myw-form", function (e) {
		if (e.key === "Escape") {
			state.open = null;
			$(this).closest(".myw-inline").empty();
		}
	});

	$body.on("click", ".myw-more", function () {
		const bucket = $(this).attr("data-bucket");
		state.more[bucket] = (state.more[bucket] || 0) + PAGE_SIZE;
		kit().then(draw);
	});

	// Drag a row onto another bucket to move it there. HTML5 drag and drop: desktop only, which is
	// fine, because the Snooze menu does the same on a phone.
	let dragging = null;
	$body.on("dragstart", ".myw-item[draggable=true]", function (e) {
		dragging = $(this).attr("data-name");
		$root.addClass("myw-page--dragging");
		const dt = e.originalEvent && e.originalEvent.dataTransfer;
		if (dt) {
			dt.effectAllowed = "move";
			dt.setData("text/plain", dragging);
		}
	});
	$body.on("dragend", ".myw-item", function () {
		dragging = null;
		$root.removeClass("myw-page--dragging");
		$body.find(".myw-bucket--over").removeClass("myw-bucket--over");
	});
	$body.on("dragover", ".myw-bucket[data-drop]", function (e) {
		if (!dragging) return;
		e.preventDefault();
		$body.find(".myw-bucket--over").not(this).removeClass("myw-bucket--over");
		$(this).addClass("myw-bucket--over");
	});
	$body.on("drop", ".myw-bucket[data-drop]", function (e) {
		e.preventDefault();
		const name = dragging;
		const bucket = $(this).attr("data-bucket");
		dragging = null;
		$root.removeClass("myw-page--dragging");
		$(this).removeClass("myw-bucket--over");
		const item = find(name);
		if (!item || item.bucket === bucket) return;
		const day = drop_date(bucket);
		if (day === null) return;
		act(
			"reschedule",
			{ todo: name, date: day },
			day ? __("Moved to {0}", [day_label(day)]) : __("Due date taken off")
		);
	});

	// ------------------------------------------------------------------ the Team board (Phase 3)
	//
	// Leads only (api.my_work.LEAD_ROLES). A column per person -- overdue, today and open counts,
	// then their assignments -- and a column per team queue. Drag a card onto another person to hand
	// it over (api.my_work.reassign, which is Frappe's own assign_to.add for the new person). The
	// team filter narrows it to one team's members and queue, members with nothing open included.

	function load_team() {
		const ticket = ++state.loading;
		return kit().then((sf) => {
			if (!state.board) sf.state.render($body[0], "loading");
			draw_tabs(sf);
			return sf.call(API + "get_team_board", { team: state.team || "" }, { quiet: true, method: "GET" }).then(
				(board) => {
					if (ticket !== state.loading) return;
					state.board = board;
					state.today = board.today;
					draw_team(sf);
				},
				(err) => {
					if (ticket !== state.loading) return;
					sf.state.render($body[0], "error", {
						title: __("The team board could not be loaded."),
						body: (err && err.message) || "",
						on_action: load_team,
					});
				}
			);
		});
	}

	function card_html(sf, item) {
		const link =
			item.reference_type && item.reference_name && !item.record_missing
				? frappe.utils.get_form_link(item.reference_type, item.reference_name)
				: frappe.utils.get_form_link("ToDo", item.name);
		const ask = item.ask && item.ask !== item.title ? `<div class="myw-card-ask">${sf.esc(item.ask)}</div>` : "";
		return (
			`<li class="myw-card" data-name="${sf.esc(item.name)}"${item.can.reassign ? ' draggable="true"' : ""}>` +
			`<a class="myw-card-title" href="${sf.esc(link)}">${sf.esc(item.title)}</a>` +
			`<span class="myw-chips">${when_chip(sf, item)} ${checklist_chip(sf, item)}</span>` +
			ask +
			`</li>`
		);
	}

	function draw_team(sf) {
		const board = state.board;
		const teams = board.teams || [];
		const options =
			`<option value="">${sf.esc(__("Everyone"))}</option>` +
			teams
				.map(
					(t) =>
						`<option value="${sf.esc(t.role)}"${t.role === state.team ? " selected" : ""}>${sf.esc(t.label)}</option>`
				)
				.join("");
		const people = (board.people || [])
			.map((p) => {
				const counts = p.counts || {};
				const chips = [
					counts.overdue ? sf.chip(__("{0} overdue", [counts.overdue]), "danger") : "",
					counts.today ? sf.chip(__("{0} today", [counts.today]), "warn") : "",
					sf.chip(__("{0} open", [p.actionable]), "neutral"),
				].join(" ");
				const cards = p.items.map((item) => card_html(sf, item)).join("");
				return (
					`<section class="myw-col" data-user="${sf.esc(p.user)}" data-drop="1">` +
					`<h3 class="myw-col-head"><span class="myw-col-name">${sf.esc(p.name)}</span><span class="myw-chips">${chips}</span></h3>` +
					`<ul class="myw-cards">${cards || `<li class="myw-hint">${sf.esc(__("Nothing open"))}</li>`}</ul>` +
					(p.more ? `<div class="myw-hint">${sf.esc(__("and {0} more", [p.more]))}</div>` : "") +
					`</section>`
				);
			})
			.join("");
		const queues = (board.queues || [])
			.map(
				(q) =>
					`<section class="myw-col myw-col--queue">` +
					`<h3 class="myw-col-head"><span class="myw-col-name">${sf.esc(__("{0} queue", [q.label]))}</span>` +
					`<span class="myw-chips">${sf.chip(String(q.items.length), "build")}</span></h3>` +
					`<ul class="myw-cards">${q.items.map((item) => card_html(sf, item)).join("")}</ul>` +
					`</section>`
			)
			.join("");
		$body.html(
			`<div class="myw-team">` +
				`<div class="myw-team-bar"><label class="myw-form-label" for="myw-team">${sf.esc(__("Team"))}</label>` +
				`<select id="myw-team" class="myw-input myw-team-select">${options}</select>` +
				`<span class="myw-hint">${sf.esc(__("Drag a card onto someone to hand it over."))}</span></div>` +
				`<div class="myw-board">${people}${queues}</div>` +
				`</div>`
		);
	}

	$body.on("change", ".myw-team-select", function () {
		state.team = $(this).val() || "";
		state.board = null;
		load_team();
	});

	let carrying = null;
	$body.on("dragstart", ".myw-card[draggable=true]", function (e) {
		carrying = $(this).attr("data-name");
		const dt = e.originalEvent && e.originalEvent.dataTransfer;
		if (dt) {
			dt.effectAllowed = "move";
			dt.setData("text/plain", carrying);
		}
	});
	$body.on("dragend", ".myw-card", function () {
		carrying = null;
		$body.find(".myw-col--over").removeClass("myw-col--over");
	});
	$body.on("dragover", ".myw-col[data-drop]", function (e) {
		if (!carrying) return;
		e.preventDefault();
		$body.find(".myw-col--over").not(this).removeClass("myw-col--over");
		$(this).addClass("myw-col--over");
	});
	$body.on("drop", ".myw-col[data-drop]", function (e) {
		e.preventDefault();
		const name = carrying;
		const user = $(this).attr("data-user");
		carrying = null;
		$(this).removeClass("myw-col--over");
		const item = find(name);
		if (!item || !user || item.allocated_to === user) return;
		const person = ((state.board && state.board.people) || []).find((p) => p.user === user);
		act("reassign", { todo: name, user }, __("Handed to {0}", [(person && person.name) || user]));
	});

	// ------------------------------------------------------------------ the Calendar (Phase 3)
	//
	// Your assignments with a real due date, on a month grid that starts on Monday. Undated ones are
	// not on it: a calendar of creation-day stamps is the ToDo calendar this replaces. Drag one to
	// another day to move it (today or later). On a phone the grid becomes a list of the days that
	// have something on them.

	function month_start(day) {
		return day.slice(0, 8) + "01";
	}

	function grid_range(month) {
		const first = frappe.datetime.str_to_obj(month);
		const back = (first.getDay() + 6) % 7; // days since Monday
		const start = frappe.datetime.add_days(month, -back);
		return { start, end: frappe.datetime.add_days(start, 41) };
	}

	function load_calendar() {
		const ticket = ++state.loading;
		if (!state.month) state.month = month_start(state.today || frappe.datetime.get_today());
		const range = grid_range(state.month);
		return kit().then((sf) => {
			if (!state.cal) sf.state.render($body[0], "loading");
			draw_tabs(sf);
			return sf.call(API + "get_calendar", range, { quiet: true, method: "GET" }).then(
				(cal) => {
					if (ticket !== state.loading) return;
					state.cal = cal;
					state.today = cal.today;
					draw_calendar(sf, range);
				},
				(err) => {
					if (ticket !== state.loading) return;
					sf.state.render($body[0], "error", {
						title: __("The calendar could not be loaded."),
						body: (err && err.message) || "",
						on_action: load_calendar,
					});
				}
			);
		});
	}

	function draw_calendar(sf, range) {
		const by_day = {};
		(state.cal.items || []).forEach((item) => (by_day[item.due] = by_day[item.due] || []).push(item));
		const label = typeof moment === "function" ? moment(state.month).format("MMMM YYYY") : state.month.slice(0, 7);
		const heads = [__("Mon"), __("Tue"), __("Wed"), __("Thu"), __("Fri"), __("Sat"), __("Sun")]
			.map((d) => `<div class="myw-cal-head">${sf.esc(d)}</div>`)
			.join("");
		const cells = [];
		for (let i = 0; i < 42; i++) {
			const day = frappe.datetime.add_days(range.start, i);
			const items = by_day[day] || [];
			const classes = ["myw-cal-day"];
			if (day.slice(0, 7) !== state.month.slice(0, 7)) classes.push("myw-cal-day--other");
			if (day === state.today) classes.push("myw-cal-day--today");
			if (!items.length) classes.push("myw-cal-day--empty");
			const entries = items
				.map((item) => {
					const link =
						item.reference_type && item.reference_name && !item.record_missing
							? frappe.utils.get_form_link(item.reference_type, item.reference_name)
							: frappe.utils.get_form_link("ToDo", item.name);
					return (
						`<li class="myw-cal-item${item.bucket === "overdue" ? " myw-cal-item--late" : ""}" data-name="${sf.esc(item.name)}"` +
						`${item.can.reschedule ? ' draggable="true"' : ""}>` +
						`<a href="${sf.esc(link)}">${sf.esc(item.title)}</a>` +
						(item.bucket === "overdue" ? ` ${sf.chip(__("Late"), "danger")}` : "") +
						`</li>`
					);
				})
				.join("");
			const date_label = typeof moment === "function" ? moment(day).format("ddd D MMM") : day;
			cells.push(
				`<div class="${classes.join(" ")}" data-day="${day}">` +
					`<div class="myw-cal-date"><span class="myw-cal-num">${sf.esc(String(Number(day.slice(8))))}</span>` +
					`<span class="myw-cal-long">${sf.esc(date_label)}</span></div>` +
					`<ul class="myw-cal-items">${entries}</ul>` +
					`</div>`
			);
		}
		const undated = __("Assignments with no due date are on the other tabs.");
		$body.html(
			`<div class="myw-cal">` +
				`<div class="myw-cal-bar">` +
				`<button type="button" class="sf-btn sf-btn--secondary" data-cal="prev" aria-label="${sf.esc(__("Previous month"))}">‹</button>` +
				`<h3 class="myw-bucket-head myw-cal-title">${sf.esc(label)}</h3>` +
				`<button type="button" class="sf-btn sf-btn--secondary" data-cal="next" aria-label="${sf.esc(__("Next month"))}">›</button>` +
				`<button type="button" class="sf-btn sf-btn--quiet" data-cal="today">${sf.esc(__("Today"))}</button>` +
				`</div>` +
				`<p class="myw-hint">${sf.esc(undated)}</p>` +
				`<div class="myw-cal-grid">${heads}${cells.join("")}</div>` +
				`</div>`
		);
	}

	$body.on("click", "[data-cal]", function () {
		const what = $(this).attr("data-cal");
		const month = state.month || month_start(state.today);
		if (what === "today") state.month = month_start(state.today);
		else if (typeof moment === "function") {
			state.month = moment(month)
				.add(what === "next" ? 1 : -1, "months")
				.format("YYYY-MM-01");
		}
		load_calendar();
	});

	let moving = null;
	$body.on("dragstart", ".myw-cal-item[draggable=true]", function (e) {
		moving = $(this).attr("data-name");
		const dt = e.originalEvent && e.originalEvent.dataTransfer;
		if (dt) {
			dt.effectAllowed = "move";
			dt.setData("text/plain", moving);
		}
	});
	$body.on("dragend", ".myw-cal-item", function () {
		moving = null;
		$body.find(".myw-cal-day--over").removeClass("myw-cal-day--over");
	});
	$body.on("dragover", ".myw-cal-day", function (e) {
		if (!moving) return;
		e.preventDefault();
		$body.find(".myw-cal-day--over").not(this).removeClass("myw-cal-day--over");
		$(this).addClass("myw-cal-day--over");
	});
	$body.on("drop", ".myw-cal-day", function (e) {
		e.preventDefault();
		const name = moving;
		const day = $(this).attr("data-day");
		moving = null;
		$(this).removeClass("myw-cal-day--over");
		const item = find(name);
		if (!item || !day || item.due === day) return;
		if (day < state.today) {
			kit().then((sf) => sf.toast(__("Pick today or a later day."), { tone: "warning" }));
			return;
		}
		act("reschedule", { todo: name, date: day }, __("Moved to {0}", [day_label(day)]));
	});

	// ------------------------------------------------------------------ backlog triage

	function show_tidy() {
		kit().then((sf) => {
			sf.state.render($body[0], "loading", { title: __("Counting the backlog…") });
			Promise.all([
				sf.call(API + "triage_preview", {}, { quiet: true, method: "GET" }),
				sf.call(API + "triage_batches", {}, { quiet: true, method: "GET" }),
			]).then(
				([preview, batches]) => draw_tidy(sf, preview, batches),
				(err) =>
					sf.state.render($body[0], "error", {
						title: __("The backlog could not be counted."),
						body: (err && err.message) || "",
						on_action: show_tidy,
					})
			);
		});
	}

	function draw_tidy(sf, preview, batches) {
		const rules = Object.keys(preview.rules)
			.map((key) => Object.assign({ key }, preview.rules[key]))
			.filter((r) => r.count);
		const intro =
			`<p class="myw-tidy-lead">${sf.esc(
				__("{0} open to-dos. This closes {1} and leaves {2} open.", [preview.open, preview.closing, preview.kept])
			)}</p>` +
			(preview.kept_unassigned
				? `<p class="myw-hint">${sf.esc(
						__(
							"{0} of those have nobody assigned (mostly Triton's call follow-ups). They stay open and are on the Unclaimed tab.",
							[preview.kept_unassigned]
						)
				  )}</p>`
				: "");
		const cards = rules
			.map((r) => {
				const people = r.people
					.slice(0, 6)
					.map((p) => `${p[0]} (${p[1]})`)
					.join(", ");
				const sample = r.sample
					.map(
						(s) =>
							`<li>${sf.esc(s.what || s.reference || s.name)}` +
							`<span class="myw-hint"> · ${sf.esc(s.who)} · ${sf.esc(s.created)}</span></li>`
					)
					.join("");
				return (
					`<div class="sf-card myw-tidy-rule">` +
					`<div class="myw-tidy-head">${sf.chip(String(r.count), "neutral")} <b>${sf.esc(r.label)}</b> ` +
					`${sf.chip(r.status === "Closed" ? __("Closes as done") : __("Cancelled"), r.status === "Closed" ? "good" : "neutral")}</div>` +
					`<div class="myw-hint">${sf.esc(people)}</div>` +
					`<ul class="myw-tidy-sample">${sample}</ul>` +
					`</div>`
				);
			})
			.join("");
		const ready = preview.ready
			? ""
			: `<p class="myw-hint">${sf.esc(__("The site has not been migrated yet, so nothing can be closed."))}</p>`;
		const action =
			preview.closing && preview.ready
				? `<div class="myw-form-row myw-tidy-actions">` +
				  `<button type="button" class="sf-btn sf-btn--primary" data-tidy="ask">${sf.esc(
						__("Close these {0}", [preview.closing])
				  )}</button>` +
				  `<button type="button" class="sf-btn sf-btn--quiet" data-tidy="back">${sf.esc(__("Back to My Work"))}</button>` +
				  `</div>`
				: `<div class="myw-form-row"><button type="button" class="sf-btn sf-btn--secondary" data-tidy="back">${sf.esc(
						__("Back to My Work")
				  )}</button></div>`;
		const past = (batches || []).length
			? `<h3 class="myw-bucket-head">${sf.esc(__("Earlier tidy-ups"))}</h3><ul class="myw-tidy-batches">` +
			  batches
					.map(
						(b) =>
							`<li><span>${sf.esc(b.batch)} · ${sf.esc(__("{0} still closed", [b.count]))}</span> ` +
							`<button type="button" class="sf-btn sf-btn--quiet" data-tidy="undo" data-batch="${sf.esc(b.batch)}">${sf.esc(
								__("Undo")
							)}</button></li>`
					)
					.join("") +
			  `</ul>`
			: "";
		$body.html(
			`<div class="myw-tidy"><h3 class="myw-bucket-head">${sf.esc(__("Tidy the backlog"))}</h3>` +
				intro +
				ready +
				(cards ? `<div class="myw-tidy-rules">${cards}</div>` : `<p>${sf.esc(__("Nothing to tidy."))}</p>`) +
				action +
				past +
				`</div>`
		);
		$body.data("tidy_closing", preview.closing);
	}

	$body.on("click", "[data-tidy]", function () {
		const what = $(this).attr("data-tidy");
		if (what === "back") return frappe.set_route("my-work");
		if (what === "ask") {
			$(this)
				.attr("data-tidy", "apply")
				.text(__("Yes, close {0}", [$body.data("tidy_closing")]));
			return;
		}
		kit().then((sf) => {
			if (what === "apply") {
				$(this).prop("disabled", true);
				sf.call(API + "triage_apply", {}, { quiet: true }).then(
					() => {
						sf.toast(__("Tidying in the background. This page updates when it is done."), { tone: "info" });
					},
					(err) => {
						$(this).prop("disabled", false);
						sf.toast((err && err.message) || __("That did not work."), { tone: "warning" });
					}
				);
			} else if (what === "undo") {
				const batch = $(this).attr("data-batch");
				$(this).prop("disabled", true);
				sf.call(API + "triage_undo", { batch }, { quiet: true }).then(
					(r) => {
						sf.toast(__("Reopened {0}", [r.reopened]), { tone: "success" });
						refresh_count();
						show_tidy();
					},
					(err) => sf.toast((err && err.message) || __("That did not work."), { tone: "warning" })
				);
			}
		});
	});

	frappe.realtime.on("ee_my_work_triage_done", (data) => {
		kit().then((sf) => {
			const total = Object.values((data && data.counts) || {}).reduce((a, b) => a + b, 0);
			sf.toast(__("Tidied: {0} to-dos closed.", [total]), { tone: "success" });
		});
		refresh_count();
		if ((frappe.get_route() || [])[0] === "my-work") {
			if ((frappe.get_route() || [])[1] === "tidy") show_tidy();
			else load();
		}
	});

	function css_escape(value) {
		return window.CSS && CSS.escape ? CSS.escape(value) : String(value).replace(/["\\]/g, "\\$&");
	}

	return { show };
}
