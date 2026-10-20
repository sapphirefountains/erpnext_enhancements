/**
 * My Work, everywhere else on the Desk (WI-081 Phase 1): a "My Work" row with its count in the
 * sidebar, and the "Assigned to you" banner at the top of any record you are assigned on.
 *
 * Targets: every Desk page (the sidebar) and every form (the banner).
 * Loaded via: erpnext_enhancements.bundle.js (global).
 * Server: erpnext_enhancements.api.my_work. The page itself is task_enhancements/page/my_work.
 *
 * The sidebar row. Frappe v16.50 draws Search and the bell as rows in `.standard-items-band`, the
 * strip under the sidebar header (sidebar.js add_standard_items). My Work goes in the same strip,
 * after the bell, built with the sidebar's own row markup (sidebar_item.html) so it takes the
 * sidebar's styles. Its count is `.ee-my-work-count`, deliberately NOT `.notification-count`:
 * Notifications.update_count_badge writes the unread number into every element with that class.
 * The number is your open assignments that are overdue, due today or have no due date
 * (assignments.ACTIONABLE): snoozing one takes it off. It is fetched once on load, again when a
 * bell notification arrives (a new assignment makes one), when the My Work page acts, and at
 * most once a minute on navigation. The band is drawn once per sidebar, so injecting is
 * idempotent and re-checked on every route change.
 *
 * The banner. A form's docinfo already lists its open assignments (assign_to.get, as
 * `assignments: [{owner, name}]`), so only a form that lists YOU asks the server anything. The
 * banner says who assigned it, when it is due and what they asked, with Done (and an optional
 * note for the timeline), Snooze, Reassign and Reply, in a strip at the top of the form layout.
 * On a ToDo's own form it says which record the to-do is for, with a button to open it: the link
 * that the ToDo form otherwise keeps at the bottom.
 *
 * After an action the form reloads, so its Assign To avatars match, unless it has unsaved
 * changes, in which case only the assignments in its docinfo are updated.
 */
(function () {
	if (typeof window === "undefined" || typeof frappe === "undefined") return;

	const API = "erpnext_enhancements.api.my_work.";
	const ITEM_CLASS = "ee-my-work-item";
	const COUNT_MIN_GAP = 60 * 1000;

	let last_count_at = 0;
	let count_inflight = null;
	let last_count = null;

	function esc(value) {
		return frappe.utils.escape_html(value == null ? "" : String(value));
	}

	// A read that never shows a dialog: the count and the banner run on their own, and a failed
	// background read must not put "Server Error" in front of someone who asked for nothing. The
	// same request frappe.call's GET makes, without its error handlers.
	function quiet_get(method, args) {
		const query = new URLSearchParams(args || {}).toString();
		return fetch(`/api/method/${method}${query ? "?" + query : ""}`, {
			method: "GET",
			credentials: "same-origin",
			cache: "no-store",
			headers: { Accept: "application/json", "X-Requested-With": "XMLHttpRequest" },
		})
			.then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
			.then((body) => body && body.message);
	}

	function signed_in() {
		return frappe.session && frappe.session.user && frappe.session.user !== "Guest";
	}

	// ------------------------------------------------------------------ sidebar row

	function inject_sidebar_item() {
		const $bands = $(".standard-items-band");
		$bands.each(function () {
			const $band = $(this);
			if ($band.find("." + ITEM_CLASS).length) return;
			const icon = frappe.utils.icon("list-todo", "sm", "", "", "current-color", true);
			const $item = $(
				`<div class="sidebar-item-container ${ITEM_CLASS}" item-name="My Work" data-id="My Work">` +
					`<div class="standard-sidebar-item">` +
					`<a href="/desk/my-work" class="item-anchor" data-link-type="">` +
					`<span class="sidebar-item-icon" item-icon="list-todo">${icon}</span>` +
					`<span class="sidebar-item-label">${esc(__("My Work"))}</span>` +
					`<span class="sidebar-item-suffix"><span class="ee-my-work-count hidden" aria-live="polite"></span></span>` +
					`<div class="sidebar-item-control"></div>` +
					`</a></div>` +
					`<div class="sidebar-child-item nested-container"></div>` +
					`</div>`
			);
			const $bell = $band.find(".sidebar-notification").closest(".sidebar-item-container");
			if ($bell.length) $item.insertAfter($bell);
			else $band.append($item);
		});
		highlight_sidebar_item();
		if (last_count) paint_count(last_count);
	}

	function highlight_sidebar_item() {
		const route = frappe.get_route ? frappe.get_route() || [] : [];
		$("." + ITEM_CLASS + " .standard-sidebar-item").toggleClass("selected", route[0] === "my-work");
	}

	function paint_count(data) {
		last_count = data;
		const $count = $(".ee-my-work-count");
		if (!$count.length) return;
		const n = (data && data.count) || 0;
		const overdue = (data && data.overdue) || 0;
		if (!n) {
			$count.addClass("hidden").removeAttr("aria-label").removeAttr("title").text("");
			return;
		}
		const label = overdue
			? __("{0} to do, {1} overdue", [n, overdue])
			: __("{0} to do", [n]);
		$count
			.text(n > 99 ? "99+" : n)
			.attr("aria-label", label)
			.attr("title", label)
			.toggleClass("ee-my-work-count--overdue", overdue > 0)
			.removeClass("hidden");
	}

	function refresh_count(force) {
		if (!signed_in()) return Promise.resolve(null);
		const now = Date.now();
		if (!force && now - last_count_at < COUNT_MIN_GAP) return count_inflight || Promise.resolve(last_count);
		last_count_at = now;
		count_inflight = quiet_get(API + "get_count")
			.then((data) => {
				paint_count(data);
				return data;
			})
			.catch(() => null)
			.finally(() => {
				count_inflight = null;
			});
		return count_inflight;
	}

	// ------------------------------------------------------------------ the banner

	function banner_slot(frm) {
		const layout = frm.layout && frm.layout.wrapper;
		if (!layout) return null;
		const $layout = $(layout);
		let $slot = $layout.children(".ee-mw-banners");
		if (!$slot.length) $slot = $('<div class="ee-mw-banners"></div>').prependTo($layout);
		return $slot;
	}

	function on_form_refresh(frm) {
		if (!frm || !frm.doc || !signed_in()) return;
		const $slot = banner_slot(frm);
		if (!$slot) return;
		if (frm.is_new()) {
			$slot.empty();
			return;
		}
		if (frm.doctype === "ToDo") {
			draw_todo_pointer(frm, $slot);
			return;
		}
		const info = frm.get_docinfo ? frm.get_docinfo() : null;
		const mine = ((info && info.assignments) || []).some((a) => a.owner === frappe.session.user);
		if (!mine) {
			$slot.empty();
			return;
		}
		const ticket = (frm.__ee_mw_ticket = (frm.__ee_mw_ticket || 0) + 1);
		quiet_get(API + "get_record_assignments", { doctype: frm.doctype, name: frm.doc.name })
			.then((items) => {
				if (ticket !== frm.__ee_mw_ticket) return;
				$slot.html((items || []).map((item) => banner_html(item)).join(""));
				$slot.data("items", items || []);
			})
			.catch(() => $slot.empty());
	}

	function banner_html(item) {
		const parts = [];
		if (item.assigned_by && item.assigned_by !== frappe.session.user) {
			parts.push(__("Assigned to you by {0}", [esc(item.assigned_by_name)]));
		} else {
			parts.push(__("Assigned to you"));
		}
		if (item.bucket === "overdue") parts.push(`<b>${esc(__("overdue since {0}", [frappe.datetime.str_to_user(item.due)]))}</b>`);
		else if (item.due) parts.push(esc(__("due {0}", [frappe.datetime.str_to_user(item.due)])));
		if (item.priority === "High") parts.push(`<b>${esc(__("High priority"))}</b>`);
		const ask = item.ask ? `<div class="ee-mw-banner-ask">${esc(item.ask)}</div>` : "";
		const can = item.can || {};
		const btn = (act, label, cls) =>
			`<button type="button" class="btn btn-sm ${cls || "btn-default"}" data-ee-mw="${act}">${esc(label)}</button>`;
		const snooze =
			`<div class="dropdown ee-mw-snooze">` +
			`<button type="button" class="btn btn-sm btn-default dropdown-toggle" data-toggle="dropdown" aria-haspopup="true" aria-expanded="false">${esc(__("Snooze"))}</button>` +
			`<div class="dropdown-menu">` +
			[
				["tomorrow", __("Tomorrow")],
				["monday", __("Next Monday")],
				["week", __("In a week")],
				["pick", __("Pick a day…")],
				["none", __("No due date")],
			]
				.map((o) => `<button type="button" class="dropdown-item" data-ee-mw="snooze" data-when="${o[0]}">${esc(o[1])}</button>`)
				.join("") +
			`</div></div>`;
		return (
			`<div class="ee-mw-banner" data-todo="${esc(item.name)}" role="region" aria-label="${esc(__("Your assignment"))}">` +
			`<div class="ee-mw-banner-text"><div class="ee-mw-banner-who">${parts.join(" · ")}</div>${ask}</div>` +
			`<div class="ee-mw-banner-actions">` +
			(can.complete ? btn("complete", __("Done"), "btn-primary") : "") +
			(can.reschedule ? snooze : "") +
			(can.reassign ? btn("reassign", __("Reassign")) : "") +
			(can.reply ? btn("reply", __("Reply to {0}", [item.assigned_by_name])) : "") +
			`<a class="btn btn-sm btn-link" href="/desk/my-work?todo=${encodeURIComponent(item.name)}">${esc(__("My Work"))}</a>` +
			`</div>` +
			`<div class="ee-mw-banner-form"></div>` +
			`</div>`
		);
	}

	function draw_todo_pointer(frm, $slot) {
		const doc = frm.doc;
		if (!doc.reference_type || !doc.reference_name) {
			$slot.empty();
			return;
		}
		const link = frappe.utils.get_form_link(doc.reference_type, doc.reference_name);
		$slot.html(
			`<div class="ee-mw-banner ee-mw-banner--pointer">` +
				`<div class="ee-mw-banner-text"><div class="ee-mw-banner-who">${esc(
					__("This to-do is for {0} {1}", [__(doc.reference_type), doc.reference_name])
				)}</div></div>` +
				`<div class="ee-mw-banner-actions">` +
				`<a class="btn btn-sm btn-primary" href="${esc(link)}">${esc(__("Open {0}", [__(doc.reference_type)]))}</a>` +
				`<a class="btn btn-sm btn-link" href="/desk/my-work?todo=${encodeURIComponent(doc.name)}">${esc(__("My Work"))}</a>` +
				`</div></div>`
		);
	}

	function next_monday(today) {
		const dow = frappe.datetime.str_to_obj(today).getDay();
		return frappe.datetime.add_days(today, ((8 - dow) % 7) || 7);
	}

	function snooze_day(when) {
		const today = frappe.datetime.get_today();
		if (when === "tomorrow") return frappe.datetime.add_days(today, 1);
		if (when === "monday") return next_monday(today);
		if (when === "week") return frappe.datetime.add_days(today, 7);
		if (when === "none") return "";
		return null;
	}

	function after_action(frm, message) {
		if (message) frappe.show_alert({ message, indicator: "green" });
		refresh_count(true);
		if (!frm) return;
		if (!frm.is_dirty()) {
			frm.reload_doc();
			return;
		}
		// Unsaved edits: keep them, and refresh only the docinfo (assignments, timeline).
		frappe.call({
			method: "frappe.desk.form.load.get_docinfo",
			args: { doctype: frm.doctype, name: frm.docname },
			callback: (r) => {
				if (r && r.docinfo) {
					frappe.model.docinfo[frm.doctype] = frappe.model.docinfo[frm.doctype] || {};
					frappe.model.docinfo[frm.doctype][frm.docname] = r.docinfo;
				}
				if (frm.assign_to && frm.assign_to.refresh) frm.assign_to.refresh();
				if (frm.timeline && frm.timeline.refresh) frm.timeline.refresh();
				on_form_refresh(frm);
			},
		});
	}

	function send(frm, method, args, message) {
		return frappe
			.xcall(API + method, args)
			.then(() => after_action(frm, message))
			.catch(() => {
				/* frappe.xcall has already shown the server's message */
			});
	}

	function open_banner_form($banner, kind) {
		const $form = $banner.find(".ee-mw-banner-form");
		if ($form.attr("data-kind") === kind) {
			$form.empty().removeAttr("data-kind");
			return;
		}
		$form.attr("data-kind", kind);
		const label = {
			complete: __("Note (optional, goes on the timeline)"),
			reply: __("Your reply"),
			reassign: __("Note (optional)"),
			pick: __("Due on"),
		}[kind];
		const submit = { complete: __("Mark done"), reply: __("Send"), reassign: __("Reassign"), pick: __("Set the day") }[kind];
		const field =
			kind === "pick"
				? `<input type="date" class="form-control ee-mw-input" min="${esc(frappe.datetime.get_today())}">`
				: `<textarea class="form-control ee-mw-input" rows="2"></textarea>`;
		$form.html(
			`<form class="ee-mw-form">` +
				(kind === "reassign" ? `<div class="ee-mw-user"></div>` : "") +
				`<label class="ee-mw-label">${esc(label)}</label>${field}` +
				`<div class="ee-mw-form-row"><button type="submit" class="btn btn-sm btn-primary">${esc(submit)}</button>` +
				`<button type="button" class="btn btn-sm btn-default" data-ee-mw="close-form">${esc(__("Cancel"))}</button></div>` +
				`</form>`
		);
		if (kind === "reassign") {
			const control = frappe.ui.form.make_control({
				parent: $form.find(".ee-mw-user"),
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
			$form.data("user_control", control);
			$(control.input).trigger("focus");
		} else {
			$form.find(".ee-mw-input").trigger("focus");
		}
	}

	$(document).on("click", ".ee-mw-banner [data-ee-mw]", function (e) {
		const frm = window.cur_frm;
		const $banner = $(this).closest(".ee-mw-banner");
		const todo = $banner.attr("data-todo");
		const what = $(this).attr("data-ee-mw");
		e.preventDefault();
		if (what === "close-form") {
			$banner.find(".ee-mw-banner-form").empty().removeAttr("data-kind");
			return;
		}
		if (what === "snooze") {
			const when = $(this).attr("data-when");
			if (when === "pick") return open_banner_form($banner, "pick");
			const day = snooze_day(when);
			if (day === null) return;
			send(frm, "reschedule", { todo, date: day }, day ? __("Snoozed to {0}", [frappe.datetime.str_to_user(day)]) : __("Due date taken off"));
			return;
		}
		open_banner_form($banner, what);
	});

	$(document).on("submit", ".ee-mw-form", function (e) {
		e.preventDefault();
		const frm = window.cur_frm;
		const $form = $(this);
		const $box = $form.closest(".ee-mw-banner-form");
		const todo = $form.closest(".ee-mw-banner").attr("data-todo");
		const kind = $box.attr("data-kind");
		const value = ($form.find(".ee-mw-input").val() || "").trim();
		if (kind === "complete") return send(frm, "complete", { todo, note: value }, __("Marked done"));
		if (kind === "reply") {
			if (!value) return;
			return send(frm, "reply", { todo, message: value }, __("Reply sent"));
		}
		if (kind === "pick") {
			if (!value) return;
			return send(frm, "reschedule", { todo, date: value }, __("Moved to {0}", [frappe.datetime.str_to_user(value)]));
		}
		if (kind === "reassign") {
			const control = $box.data("user_control");
			const user = control && control.get_value();
			if (!user) {
				frappe.show_alert({ message: __("Pick who it goes to."), indicator: "orange" });
				return;
			}
			return send(frm, "reassign", { todo, user, note: value }, __("Reassigned"));
		}
	});

	// ------------------------------------------------------------------ wiring

	$(document).on("form-refresh", (e, frm) => on_form_refresh(frm));

	function on_route() {
		inject_sidebar_item();
		refresh_count(false);
	}

	$(document).ready(() => {
		if (!signed_in()) return;
		// The sidebar draws after the first route; try a few times, then rely on route changes.
		let tries = 0;
		const timer = setInterval(() => {
			inject_sidebar_item();
			if ($("." + ITEM_CLASS).length || ++tries > 20) clearInterval(timer);
		}, 500);
		refresh_count(true);
		if (frappe.router && frappe.router.on) frappe.router.on("change", on_route);
		if (frappe.realtime && frappe.realtime.on) frappe.realtime.on("notification", () => refresh_count(true));
	});

	// ------------------------------------------------------------------ Assign To (WI-081 Phase 2)
	//
	// Frappe's Assign To dialog (form/sidebar/assign_to.js, AssignToDialog) is the one every
	// assignment starts from: the form sidebar, and the list view's bulk "Assign To". It is
	// subclassed here, not replaced, so both get:
	//   - "What do you need?" first, as one plain line, instead of a "Comment" editor at the bottom
	//     that Frappe pre-fills with the record's own title (set_description_from_doc) -- which is
	//     why so many production ToDos "asked" for nothing but the project name. It now starts empty;
	//     left empty, assign_to.add writes "Assignment for X", which My Work reads as no ask.
	//   - quick dates under "Complete By": Today, Tomorrow, Friday, Next Monday, In a week.
	//   - each picked person's load (open, overdue, due that day) and, for staff, whether they are
	//     off that day (api.my_work.get_workload). Counts only, never what someone is working on.
	//   - Priority defaulting to the record's priority, else Medium. Frappe's own default is the
	//     result of an `includes(...)` -- true or false -- which no option matches.
	// AssignToDialog is in form.bundle.js, which Frappe loads before this app's bundle; the guard is
	// for a Desk where it is somehow missing, and for a second evaluation of this file.

	const WORKLOAD_DEBOUNCE = 250;

	function friday_of(today) {
		const dow = frappe.datetime.str_to_obj(today).getDay();
		return frappe.datetime.add_days(today, (5 - dow + 7) % 7 || 7);
	}

	function quick_dates(today) {
		return [
			[__("Today"), today],
			[__("Tomorrow"), frappe.datetime.add_days(today, 1)],
			[__("Friday"), friday_of(today)],
			[__("Next Monday"), next_monday(today)],
			[__("In a week"), frappe.datetime.add_days(today, 7)],
		];
	}

	function workload_html(rows) {
		if (!rows || !rows.length) return "";
		return (
			`<div class="ee-mw-load">` +
			rows
				.map((r) => {
					// Every piece is escaped here, once; only the overdue count is wrapped in <b>.
					const bits = [esc(__("{0} open", [r.open]))];
					if (r.overdue) bits.push(`<b>${esc(__("{0} overdue", [r.overdue]))}</b>`);
					if (r.due_that_day) bits.push(esc(__("{0} due that day", [r.due_that_day])));
					const away = (r.away || []).length
						? `<div class="ee-mw-load-away">${esc(__("Away"))}: ${esc(r.away.join("; "))}</div>`
						: "";
					return `<div class="ee-mw-load-row"><span class="ee-mw-load-name">${esc(r.name)}</span> · ${bits.join(
						" · "
					)}${away}</div>`;
				})
				.join("") +
			`</div>`
		);
	}

	function install_assign_dialog() {
		const form = frappe.ui && frappe.ui.form;
		const Base = form && form.AssignToDialog;
		if (!Base || Base.__ee_my_work) return;

		class MyWorkAssignToDialog extends Base {
			get_fields() {
				const fields = super.get_fields();
				const take = (name) => {
					const i = fields.findIndex((f) => f.fieldname === name);
					return i >= 0 ? fields.splice(i, 1)[0] : null;
				};
				const description = take("description");
				const date = take("date");
				const priority = take("priority");
				// What is left: Assign to me, Assign To, User Group, and Frappe's section/column breaks.
				const head = fields.filter((f) => !["Section Break", "Column Break"].includes(f.fieldtype));
				const assign = head.find((f) => f.fieldname === "assign_to");
				if (assign) {
					const before = assign.onchange;
					assign.onchange = () => {
						if (before) before();
						this.ee_refresh_workload();
					};
				}
				const out = [];
				head.forEach((f) => {
					out.push(f);
					if (f.fieldname === "assign_to") out.push({ fieldtype: "HTML", fieldname: "ee_workload" });
				});
				if (description) {
					out.push({ fieldtype: "Section Break" });
					out.push(
						Object.assign(description, {
							label: __("What do you need?"),
							fieldtype: "Small Text",
							description: __("The one line they see first: in the email, on My Work and on the record."),
						})
					);
				}
				out.push({ fieldtype: "Section Break" });
				if (date) {
					const before = date.onchange;
					out.push(
						Object.assign(date, {
							onchange: () => {
								if (before) before();
								this.ee_refresh_workload();
							},
						})
					);
					out.push({ fieldtype: "HTML", fieldname: "ee_quick_dates" });
				}
				out.push({ fieldtype: "Column Break" });
				if (priority) {
					const own = this.frm && this.frm.doc && this.frm.doc.priority;
					priority.default = ["Low", "Medium", "High"].includes(own) ? own : "Medium";
					out.push(priority);
				}
				return out;
			}

			make() {
				super.make();
				try {
					this.ee_draw_quick_dates();
				} catch (e) {
					/* the dialog still works without the shortcuts */
				}
			}

			// Deliberately empty: see the comment above.
			set_description_from_doc() {}

			ee_draw_quick_dates() {
				const field = this.dialog.fields_dict.ee_quick_dates;
				if (!field) return;
				const today = frappe.datetime.get_today();
				const $wrap = $(field.wrapper).empty().addClass("ee-mw-quick-dates");
				quick_dates(today).forEach(([label, day]) => {
					$(`<button type="button" class="btn btn-xs btn-default"></button>`)
						.text(label)
						.attr("title", frappe.datetime.str_to_user(day))
						.on("click", () => this.dialog.set_value("date", day))
						.appendTo($wrap);
				});
			}

			ee_refresh_workload() {
				clearTimeout(this.__ee_load_timer);
				this.__ee_load_timer = setTimeout(() => {
					const field = this.dialog && this.dialog.fields_dict.ee_workload;
					if (!field) return;
					const users = this.dialog.get_value("assign_to") || [];
					if (!users.length) {
						$(field.wrapper).empty();
						return;
					}
					const ticket = (this.__ee_load_ticket = (this.__ee_load_ticket || 0) + 1);
					quiet_get(API + "get_workload", {
						users: JSON.stringify(users),
						date: this.dialog.get_value("date") || "",
					})
						.then((rows) => {
							if (ticket === this.__ee_load_ticket) $(field.wrapper).html(workload_html(rows));
						})
						.catch(() => $(field.wrapper).empty());
				}, WORKLOAD_DEBOUNCE);
			}
		}
		MyWorkAssignToDialog.__ee_my_work = true;
		form.AssignToDialog = MyWorkAssignToDialog;
	}

	// ------------------------------------------------------------------ record sidebar (WI-081 Phase 2)
	//
	// The form sidebar's "Assigned To" shows avatars and nothing else. Under them, each open
	// assignment's person, what was asked and when it is due (api.my_work.get_record_overview,
	// one GET, made only when the record has assignments at all). Frappe's AssignTo.render empties
	// its wrapper each time, so the list is drawn again after every render.

	function sidebar_html(items) {
		if (!items || !items.length) return "";
		return (
			`<ul class="ee-mw-sidebar-list">` +
			items
				.map((item) => {
					let when = "";
					if (item.bucket === "overdue") when = __("overdue since {0}", [frappe.datetime.str_to_user(item.due)]);
					else if (item.bucket === "today") when = __("due today");
					else if (item.due) when = __("due {0}", [frappe.datetime.str_to_user(item.due)]);
					const ask = item.ask ? `<div class="ee-mw-sidebar-ask">${esc(item.ask)}</div>` : "";
					const meta = [when, item.assigned_by_name ? __("from {0}", [item.assigned_by_name]) : ""]
						.filter(Boolean)
						.join(" · ");
					return (
						`<li class="ee-mw-sidebar-item${item.bucket === "overdue" ? " ee-mw-sidebar-item--late" : ""}">` +
						`<div class="ee-mw-sidebar-who">${esc(item.allocated_to_name)}</div>${ask}` +
						(meta ? `<div class="ee-mw-sidebar-meta">${esc(meta)}</div>` : "") +
						`</li>`
					);
				})
				.join("") +
			`</ul>`
		);
	}

	function install_sidebar_detail() {
		const AssignTo = frappe.ui && frappe.ui.form && frappe.ui.form.AssignTo;
		if (!AssignTo || AssignTo.prototype.__ee_my_work) return;
		const render = AssignTo.prototype.render;
		AssignTo.prototype.render = function (assignments) {
			const out = render.apply(this, arguments);
			try {
				const frm = this.frm;
				const $wrap = this.parent && this.parent.find(".assignments");
				if (!frm || !$wrap || !$wrap.length || frm.is_new() || !(assignments || []).length) return out;
				const ticket = (this.__ee_mw_ticket = (this.__ee_mw_ticket || 0) + 1);
				quiet_get(API + "get_record_overview", { doctype: frm.doctype, name: frm.docname })
					.then((items) => {
						if (ticket !== this.__ee_mw_ticket) return;
						$wrap.find(".ee-mw-sidebar-list").remove();
						$wrap.append(sidebar_html(items));
					})
					.catch(() => null);
			} catch (e) {
				/* the avatars are already drawn */
			}
			return out;
		};
		AssignTo.prototype.__ee_my_work = true;
	}

	install_assign_dialog();
	install_sidebar_detail();

	window.erpnext_enhancements = window.erpnext_enhancements || {};
	window.erpnext_enhancements.my_work = { refresh_count: () => refresh_count(true) };
})();
