// Visit Review: every maintenance visit waiting for review, and the two decisions on it.
//
// A desk Page for Maintenance Reviewers (Lisa, James, Nik, Clegg), Maintenance Supervisors and
// System Managers (v1.595.0, TASK-2026-02473). Two screens, each its own history entry, so Back
// and Forward move between them:
//
//   /desk/visit-review           the queue, oldest first, then the missed and unsent visits
//   /desk/visit-review/<visit>   one visit: every answer, Approve & Submit, Send back
//
// The answers are the review email's own context (review_digest.build_context), so the page
// and the email cannot disagree; each reading carries its value at the site's previous
// approved visit ("7.4 (was 7.9)", visit_compare.py). Both decisions go through the workflow
// as the reviewer (api/maintenance_review.py), so the self-approval rule holds: a reviewer who
// started a visit is told another reviewer approves it. A decision carries the version of the
// visit on screen, and one made on a visit changed since is refused with a reload.
//
// After a decision the page moves to the next visit waiting, replacing the decided visit's
// history entry (it has nothing left to decide). Styling uses Frappe CSS variables, so the
// light and dark themes both work.

frappe.pages["visit-review"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Visit Review"),
		single_column: true,
	});
	wrapper.visit_review = new VisitReview(page);
};

frappe.pages["visit-review"].on_page_show = function (wrapper) {
	if (wrapper.visit_review) {
		wrapper.visit_review.handle_route();
	}
};

const VR_API = "erpnext_enhancements.api.maintenance_review.";

const VR_STYLE = `
.vr-wrap{max-width:900px;margin:0 auto;padding-bottom:96px;color:var(--text-color);}
.vr-head{display:flex;justify-content:space-between;align-items:baseline;gap:8px;margin:18px 0 8px;flex-wrap:wrap;}
.vr-head h4{margin:0;font-size:16px;font-weight:600;}
.vr-muted{color:var(--text-muted);font-size:13px;}
.vr-card{display:block;width:100%;text-align:left;background:var(--card-bg);border:1px solid var(--border-color);border-radius:10px;padding:12px 14px;margin-bottom:8px;color:var(--text-color);cursor:pointer;}
.vr-card:hover,.vr-card:focus{border-color:var(--primary,#2490ef);outline:none;}
.vr-title{font-weight:600;font-size:15px;}
.vr-sub{font-size:13px;color:var(--text-muted);margin-top:2px;}
.vr-chips{margin-top:6px;display:flex;flex-wrap:wrap;gap:4px;}
.vr-chip{display:inline-block;font-size:12px;border-radius:10px;padding:1px 9px;background:var(--control-bg);color:var(--text-color);}
.vr-red{background:#fde8e8;color:#b91c1c;}
.vr-orange{background:#fef3e2;color:#b45309;}
.vr-green{background:#e7f7ed;color:#15803d;}
.vr-blue{background:#e0efff;color:#1d4ed8;}
.vr-empty{padding:14px;border:1px dashed var(--border-color);border-radius:10px;color:var(--text-muted);font-size:14px;}
.vr-missed{width:100%;border-collapse:collapse;font-size:13px;}
.vr-missed th,.vr-missed td{padding:6px 8px;border-bottom:1px solid var(--border-color);text-align:left;vertical-align:top;}
.vr-missed th{color:var(--text-muted);font-weight:500;}
.vr-nav{display:flex;justify-content:space-between;align-items:center;gap:8px;margin:12px 0;flex-wrap:wrap;}
.vr-nav .btn{margin-right:4px;}
.vr-callout{border-left:4px solid #d97706;background:#fef3e2;color:#7c2d12;padding:10px 12px;border-radius:0 8px 8px 0;margin:8px 0;font-size:14px;}
.vr-state{border-left:4px solid var(--primary,#2490ef);background:var(--control-bg);padding:10px 12px;border-radius:0 8px 8px 0;margin:8px 0;font-size:14px;}
.vr-kv{width:100%;border-collapse:collapse;font-size:14px;margin:8px 0 4px;}
.vr-kv td{padding:5px 8px;border-bottom:1px solid var(--border-color);vertical-align:top;}
.vr-kv td:first-child{color:var(--text-muted);width:38%;}
.vr-block h5{margin:18px 0 6px;font-size:14px;font-weight:600;}
.vr-table{width:100%;border-collapse:collapse;font-size:14px;}
.vr-table th,.vr-table td{padding:6px 8px;border-bottom:1px solid var(--border-color);text-align:left;vertical-align:top;}
.vr-table th{color:var(--text-muted);font-weight:500;font-size:13px;}
.vr-scroll{overflow-x:auto;}
.vr-photos{margin-top:6px;font-size:13px;}
.vr-photos a{margin-right:12px;}
.vr-notes{white-space:pre-wrap;font-size:14px;background:var(--control-bg);padding:10px 12px;border-radius:8px;}
.vr-actions{position:sticky;bottom:0;background:var(--bg-color);border-top:1px solid var(--border-color);padding:10px 0;margin-top:16px;display:flex;gap:8px;flex-wrap:wrap;align-items:center;z-index:2;}
@media (max-width:640px){.vr-missed .vr-hide-sm{display:none;}.vr-kv td:first-child{width:45%;}}
`;

class VisitReview {
	constructor(page) {
		this.page = page;
		this.$body = $('<div class="vr-wrap"></div>').appendTo(page.body);
		$("<style>").text(VR_STYLE).appendTo(page.body);
		this.current = null; // the route rendered last: "" for the queue, else a visit name
		this.came_from_queue = false;
		page.set_secondary_action(__("Refresh"), () => this.handle_route(true));
		page.add_menu_item(__("Missed Maintenance Visits report"), () =>
			frappe.set_route("query-report", "Missed Maintenance Visits")
		);
	}

	// ------------------------------------------------------------------ routing

	handle_route(force) {
		const route = frappe.get_route() || [];
		if (route[0] !== "visit-review") return;
		const name = route[1] ? String(route[1]) : "";
		if (!force && this.current === name && this.loaded) return;
		this.current = name;
		this.loaded = false;
		if (name) {
			this.show_visit(name);
		} else {
			this.came_from_queue = false;
			this.show_queue();
		}
	}

	// A new history entry, or in place of this one when `replace` (after a decision).
	go(name, replace) {
		const route = name ? ["visit-review", name] : ["visit-review"];
		if (replace) frappe.route_flags.replace_route = true;
		frappe.set_route(route);
		// frappe clears the flag on a timer; left set, the next click would replace too.
		delete frappe.route_flags.replace_route;
	}

	back_to_queue() {
		// Back when the queue is the entry behind this one, so Back/Forward stay a clean pair.
		if (this.came_from_queue) {
			window.history.back();
		} else {
			this.go("");
		}
	}

	loading() {
		this.$body.html(`<div class="vr-empty">${__("Loading…")}</div>`);
	}

	// ------------------------------------------------------------------ the queue

	show_queue() {
		this.page.set_title(__("Visit Review"));
		this.loading();
		frappe
			.call({ method: VR_API + "get_review_queue" })
			.then((r) => {
				if (this.current !== "") return; // moved on while this was loading
				this.loaded = true;
				this.render_queue(r.message || {});
			})
			.catch(() => {
				this.$body.html(`<div class="vr-empty">${__("The review queue could not be loaded.")}</div>`);
			});
	}

	render_queue(data) {
		const esc = (v) => frappe.utils.escape_html(v == null ? "" : String(v));
		const waiting = data.waiting || [];
		let html = `<div class="vr-head"><h4>${__("Waiting for review")} (${waiting.length})</h4>`;
		if (!data.can_review) {
			html += `<span class="vr-muted">${__("You can see the queue. Approving needs the Maintenance Reviewer role.")}</span>`;
		}
		html += "</div>";
		if (!waiting.length) {
			html += `<div class="vr-empty">${__("Nothing is waiting for review.")}</div>`;
		}
		waiting.forEach((row) => {
			const chips = [];
			const days = row.days_waiting || 0;
			const wait_cls = days >= 5 ? "vr-red" : days >= 2 ? "vr-orange" : "";
			chips.push(
				`<span class="vr-chip ${wait_cls}">${
					days ? __("Waiting {0} days", [days]) : __("Waiting since today")
				}</span>`
			);
			const pct = row.completion || 0;
			chips.push(
				`<span class="vr-chip ${pct >= 100 ? "vr-green" : "vr-orange"}">${__("{0}% filled in", [pct])}</span>`
			);
			if ((row.out_of_range || []).length) {
				chips.push(
					`<span class="vr-chip vr-red">${__("Out of range: {0}", [esc(row.out_of_range.join(", "))])}</span>`
				);
			}
			if ((row.unanswered || []).length) {
				chips.push(
					`<span class="vr-chip vr-orange">${__("Required, blank: {0}", [esc(row.unanswered.join(", "))])}</span>`
				);
			}
			if (row.photo_count) {
				chips.push(`<span class="vr-chip">${__("{0} photos", [row.photo_count])}</span>`);
			}
			if (row.first_visit) {
				chips.push(`<span class="vr-chip vr-blue">${__("First recorded visit")}</span>`);
			}
			const who = [row.technician_name, row.crew].filter(Boolean).join(", ");
			html += `
				<button type="button" class="vr-card" data-visit="${esc(row.name)}">
					<div class="vr-title">${esc(row.site)}${row.visit_label ? " · " + esc(row.visit_label) : ""}</div>
					<div class="vr-sub">${esc(row.visit_date)}${who ? " · " + esc(who) : ""} · ${esc(row.name)}</div>
					<div class="vr-chips">${chips.join("")}</div>
				</button>`;
		});

		html += this.missed_html(data, esc);
		this.$body.html(html);
		this.$body.find("[data-visit]").on("click", (e) => {
			this.came_from_queue = true;
			this.go($(e.currentTarget).attr("data-visit"));
		});
	}

	missed_html(data, esc) {
		const missed = data.missed || [];
		const report = `<a href="/desk/query-report/Missed%20Maintenance%20Visits">${__("Open the report")}</a>`;
		let html = `<div class="vr-head"><h4>${__("Missed and not sent")} (${missed.length})</h4><span class="vr-muted">${report}</span></div>`;
		if (data.missed_failed) {
			return html + `<div class="vr-empty">${__("The missed visits could not be listed. The error is in the Error Log.")}</div>`;
		}
		if (!missed.length) {
			return html + `<div class="vr-empty">${__("No visit is missed or waiting to be sent.")}</div>`;
		}
		const colours = { "Not drafted": "vr-red", "Not started": "vr-orange", "Started, not sent": "vr-blue" };
		html += `<div class="vr-scroll"><table class="vr-missed"><thead><tr>
			<th>${__("Kind")}</th><th>${__("Site")}</th><th class="vr-hide-sm">${__("Water feature")}</th>
			<th>${__("Due")}</th><th>${__("Late")}</th><th class="vr-hide-sm">${__("Technician")}</th><th></th>
		</tr></thead><tbody>`;
		missed.forEach((row) => {
			const what = [row.fountain, row.visit_label].filter(Boolean).join(" · ");
			const link = row.record
				? `<a href="/desk/visit-wizard/${encodeURIComponent(row.record)}">${__("Visit Wizard")}</a>`
				: row.contract
				? `<a href="/desk/sapphire-maintenance-contract/${encodeURIComponent(row.contract)}">${__("Contract")}</a>`
				: "";
			html += `<tr>
				<td><span class="vr-chip ${colours[row.kind] || ""}">${esc(row.kind)}</span></td>
				<td>${esc(row.site)}</td>
				<td class="vr-hide-sm">${esc(what)}</td>
				<td>${esc(frappe.datetime.str_to_user(row.due_date))}</td>
				<td>${__("{0} d", [row.days_late])}</td>
				<td class="vr-hide-sm">${esc(row.technician_name)}</td>
				<td>${link}</td>
			</tr>`;
		});
		return html + "</tbody></table></div>";
	}

	// ------------------------------------------------------------------ one visit

	show_visit(name) {
		this.page.set_title(__("Visit Review"));
		this.loading();
		frappe
			.call({ method: VR_API + "get_review_visit", args: { record: name } })
			.then((r) => {
				if (this.current !== name) return;
				this.loaded = true;
				this.visit = r.message;
				this.render_visit(r.message);
			})
			.catch(() => {
				this.$body.html(`<div class="vr-empty">${__("This visit could not be loaded.")}</div>`);
			});
	}

	render_visit(data) {
		const esc = (v) => frappe.utils.escape_html(v == null ? "" : String(v));
		const ctx = data.context || {};
		this.page.set_title(ctx.site || __("Visit Review"));

		let html = `<div class="vr-nav"><div>
			<button type="button" class="btn btn-default btn-sm" data-act="queue">${__("Back to the queue")}</button>
			${data.previous ? `<button type="button" class="btn btn-default btn-sm" data-act="previous">${__("Previous")}</button>` : ""}
			${data.next ? `<button type="button" class="btn btn-default btn-sm" data-act="next">${__("Next waiting")}</button>` : ""}
			</div>
			<span class="vr-muted">${
				data.position ? __("{0} of {1} waiting", [data.position, data.total]) : esc(data.name)
			}</span></div>`;

		html += `<div class="vr-title" style="font-size:18px">${esc(ctx.site)}</div>
			<div class="vr-sub">${esc(ctx.visit_date)} · ${esc(ctx.technician_name)} · ${esc(data.name)}</div>`;

		if (!data.waiting) {
			html += `<div class="vr-state">${__("This visit is not waiting for review. It is {0}.", [
				esc(data.state || __("a draft")),
			])}</div>`;
		}
		(ctx.warnings || []).forEach((w) => {
			html += `<div class="vr-callout">${esc(w)}</div>`;
		});

		html += '<table class="vr-kv">';
		(ctx.summary || []).forEach((pair) => {
			html += `<tr><td>${esc(pair[0])}</td><td>${esc(pair[1] == null ? "" : pair[1])}</td></tr>`;
		});
		html += "</table>";

		(ctx.blocks || []).forEach((block) => {
			html += `<div class="vr-block"><h5>${esc(block.heading)}</h5>`;
			if ((block.rows || []).length) {
				html += '<div class="vr-scroll"><table class="vr-table"><thead><tr>';
				(block.headers || []).forEach((h) => (html += `<th>${esc(h)}</th>`));
				html += "</tr></thead><tbody>";
				block.rows.forEach((cells) => {
					html += "<tr>" + cells.map((c) => `<td>${esc(c)}</td>`).join("") + "</tr>";
				});
				html += "</tbody></table></div>";
			} else {
				html += `<div class="vr-muted">${esc(block.empty || "")}</div>`;
			}
			if ((block.photos || []).length) {
				html +=
					'<div class="vr-photos">' +
					block.photos
						.map((p) => `<a href="${esc(p[0])}" target="_blank" rel="noopener noreferrer">${esc(p[1])}</a>`)
						.join("") +
					"</div>";
			}
			html += "</div>";
		});

		if (ctx.notes) {
			html += `<div class="vr-block"><h5>${esc(ctx.notes_heading)}</h5><div class="vr-notes">${esc(ctx.notes)}</div></div>`;
		}

		html += '<div class="vr-actions">';
		if (data.can_approve) {
			html += `<button type="button" class="btn btn-primary btn-sm" data-act="approve">${__("Approve & Submit")}</button>`;
		}
		if (data.can_send_back) {
			html += `<button type="button" class="btn btn-default btn-sm" data-act="send_back">${__("Send back…")}</button>`;
		}
		if (data.approve_blocked) {
			html += `<span class="vr-muted">${esc(data.approve_blocked)}</span>`;
		}
		if (ctx.url) {
			html += `<a class="btn btn-link btn-sm" href="${esc(ctx.url)}">${__("Open the form")}</a>`;
		}
		html += "</div>";

		this.$body.html(html);
		this.$body.find("[data-act]").on("click", (e) => this.act($(e.currentTarget).attr("data-act")));
	}

	act(action) {
		const data = this.visit || {};
		if (action === "queue") return this.back_to_queue();
		// The entry behind the next screen is this visit, not the queue.
		if (action === "previous" && data.previous) {
			this.came_from_queue = false;
			return this.go(data.previous);
		}
		if (action === "next" && data.next) {
			this.came_from_queue = false;
			return this.go(data.next);
		}
		if (action === "approve") return this.approve(data);
		if (action === "send_back") return this.send_back(data);
	}

	approve(data) {
		frappe.confirm(
			__("Approve and submit this visit? It cannot be edited after it is submitted."),
			() => {
				frappe
					.call({
						method: VR_API + "approve_visit",
						args: { record: data.name, modified: data.modified },
						freeze: true,
						freeze_message: __("Approving…"),
					})
					.then((r) => this.decided(r.message || {}, __("Approved {0}.", [data.name])))
					.catch(() => this.handle_route(true));
			}
		);
	}

	send_back(data) {
		const dialog = new frappe.ui.Dialog({
			title: __("Send this visit back"),
			fields: [
				{
					fieldname: "note",
					fieldtype: "Small Text",
					label: __("What needs fixing?"),
					reqd: 1,
					description: __("The technician gets this note by email, with their answers, and it goes on the visit's timeline."),
				},
			],
			primary_action_label: __("Send back"),
			primary_action: (values) => {
				dialog.hide();
				frappe
					.call({
						method: VR_API + "send_back_visit",
						args: { record: data.name, note: values.note, modified: data.modified },
						freeze: true,
						freeze_message: __("Sending back…"),
					})
					.then((r) => {
						const result = r.message || {};
						const told = (result.emailed || []).length
							? __("Sent back {0}. The technician was emailed.", [data.name])
							: __("Sent back {0}. The technician has no email address, so tell them.", [data.name]);
						this.decided(result, told);
					})
					.catch(() => this.handle_route(true));
			},
		});
		dialog.show();
	}

	// After a decision: say so, then show the next visit waiting (or the queue) in place of
	// this one, which has nothing left to decide.
	decided(result, message) {
		frappe.show_alert({ message, indicator: "green" }, 6);
		this.loaded = false;
		if (result.next) {
			this.go(result.next, true);
		} else {
			this.came_from_queue = false;
			this.go("", true);
		}
	}
}
