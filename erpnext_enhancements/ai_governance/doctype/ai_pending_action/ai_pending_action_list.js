// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

/**
 * AI Pending Action list: decide your own queue in one go (v1.528.0).
 *
 * Auto-loaded by Frappe as the doctype's list script (same folder, no hooks.py entry). Frappe
 * runs it inside `new Function(...)`, so the names below stay local to this file.
 *
 * - "Review My Pending (N)" opens a dialog of your own Pending, unexpired actions
 *   (gating_api.my_pending_actions). It appears only when N > 0. It recounts on every full
 *   list refresh, and on the list's realtime updates too: v16 sends those through
 *   `listview.debounced_refresh`, which never calls this file's `refresh`, so
 *   `watch_realtime_updates` wraps it. The same count and visibility go to the entry v16 puts
 *   in the page menu, the only way in on a phone or tablet.
 * - The actions menu gets "Confirm & Execute Selected" and "Cancel Selected" for ticked rows.
 *
 * A row starts unticked when the server gives a `review_reason` (high risk, hidden values, a
 * submit or cancel, a write to a Task, the gate's own records or the knowledge base), and never
 * more than 50 start ticked: one review stays a bounded set. Every batch asks a second time and
 * says what will run. The
 * names go to the server at most 10 per request, one request after another, so no request runs
 * long enough to be killed mid-tool, and the results come back as one table. If a request
 * fails, no further one is sent, and the table says which actions may or may not have run and
 * which were not sent. The server only decides your own actions, System Managers included, and
 * the results say which ones it skipped and why. Every string that came from the server goes
 * through frappe.utils.escape_html before it is put into HTML.
 *
 * The helpers in the first half are run by scripts/test_ai_pending_batch.mjs.
 */

const GATING_API = "erpnext_enhancements.assistant_tools.gating_api";
const REVIEW_LABEL = "Review My Pending";
const MAX_BATCH = 50; // gating_api.MAX_BATCH, and the most one review ticks or sends
const MY_PENDING_LIMIT = 100; // gating_api.MY_PENDING_LIMIT
// Names per request: one. Each request is then the same request the form's Confirm button
// makes, with the whole server timeout to itself, and the order below holds exactly: a
// chunk of several would give the last action in it only what the earlier ones left, and
// gating_api's time budget could skip older actions while a later request ran newer ones.
const CHUNK_SIZE = 1;
const REALTIME_RECOUNT_MS = 2000;

const RISK_COLOR = { High: "red", Medium: "orange", Low: "green" };
const STATUS_COLOR = {
	Executed: "green",
	Cancelled: "gray",
	Failed: "red",
	Skipped: "orange",
	Unknown: "red",
	"Not sent": "gray",
};

// ------------------------------------------------------------------ pure helpers

function esc(value) {
	return frappe.utils.escape_html(value === null || value === undefined ? "" : String(value));
}

// Ticked when the server says so, and never for High risk or hidden values whatever it says.
function starts_ticked(row) {
	return Boolean(row && row.batch_default) && row.risk !== "High" && !row.has_hidden;
}

// The oldest MAX_BATCH of the rows that start ticked. Rows come oldest first.
function initial_selection(rows) {
	return new Set(
		(rows || [])
			.filter(starts_ticked)
			.slice(0, MAX_BATCH)
			.map((row) => row.name)
	);
}

// "Select all" ticks the oldest MAX_BATCH, whatever their risk: ticking is still a choice.
function select_all(rows) {
	return new Set((rows || []).slice(0, MAX_BATCH).map((row) => row.name));
}

function count_label(count) {
	return count >= MY_PENDING_LIMIT ? `${MY_PENDING_LIMIT}+` : String(count);
}

function batch_limit_problem(count) {
	return count > MAX_BATCH
		? __(
				"At most {0} actions can be decided at once, and {1} are ticked. Untick some, then try again.",
				[MAX_BATCH, count]
		  )
		: "";
}

function selection_count_text(count, total) {
	const text = __("{0} of {1} selected", [count, count_label(total)]);
	return count > MAX_BATCH ? `${text} ${__("(at most {0} at once)", [MAX_BATCH])}` : text;
}

function review_notes(rows) {
	const count = (rows || []).length;
	const notes = [];
	if (count >= MY_PENDING_LIMIT) {
		notes.push(
			__(
				"These are your oldest {0} pending actions, and there are more. Decide these, then open this again for the rest.",
				[MY_PENDING_LIMIT]
			)
		);
	}
	if (count > MAX_BATCH) {
		notes.push(
			__(
				"At most {0} can be decided at once, so no more than {0} start ticked and Select all ticks the oldest {0}.",
				[MAX_BATCH]
			)
		);
	}
	return notes;
}

function untick_reason(row) {
	if (row.review_reason) {
		return __("Review individually recommended: {0}.", [row.review_reason]);
	}
	const reasons = [];
	if (row.risk === "High") reasons.push(__("high risk"));
	if (row.has_hidden) reasons.push(__("hidden values to check first"));
	return reasons.length ? __("Review individually recommended: {0}.", [reasons.join(", ")]) : "";
}

function short_duration(seconds) {
	if (seconds === null || seconds === undefined || seconds === "") return "";
	const s = Math.max(0, Math.round(Number(seconds)));
	if (!Number.isFinite(s)) return "";
	if (s < 60) return __("{0}s", [s]);
	const m = Math.floor(s / 60);
	if (m < 60) return __("{0}m", [m]);
	const h = Math.floor(m / 60);
	if (h < 48) return __("{0}h", [h]);
	return __("{0}d", [Math.floor(h / 24)]);
}

function selection_summary(rows, selected) {
	const chosen = (rows || []).filter((row) => selected.has(row.name));
	return {
		count: chosen.length,
		high: chosen.filter((row) => row.risk === "High").length,
		hidden: chosen.filter((row) => row.has_hidden).length,
	};
}

function how_many(count) {
	return count === 1 ? __("1 action") : __("{0} actions", [count]);
}

function execute_question(summary) {
	let question = summary.high
		? __("Run {0} as you, including {1} high-risk? This executes them now.", [
				how_many(summary.count),
				summary.high,
		  ])
		: __("Run {0} as you? This executes them now.", [how_many(summary.count)]);
	if (summary.hidden) {
		question +=
			" " +
			__(
				"{0} of them use hidden values, exactly as the assistant proposed them. Show Hidden Values on each action's form lists them.",
				[summary.hidden]
			);
	}
	return question;
}

function cancel_question(summary) {
	return __(
		"Cancel {0}? Nothing runs. They close as Cancelled, and the assistant would have to propose them again.",
		[how_many(summary.count)]
	);
}

// Which ticked list rows the server will decide. my_pending_actions is the authority when it
// answered (`pending` is its rows): it leaves out another person's, decided and expired actions.
// When it did not answer (`pending` is null), or for a row newer than the oldest
// MY_PENDING_LIMIT it returned, the list's own columns decide.
function runnable_rows(rows, pending, me) {
	const flags = pending ? new Map(pending.map((row) => [row.name, row])) : null;
	const truncated = Boolean(pending) && pending.length >= MY_PENDING_LIMIT;
	return (rows || [])
		.filter((row) => {
			if (flags && flags.has(row.name)) return true;
			if (flags && !truncated) return false;
			return row.requested_by === me && row.status === "Pending";
		})
		.map((row) => Object.assign({}, row, (flags && flags.get(row.name)) || {}));
}

function chunk_names(names, size) {
	const chunks = [];
	for (let i = 0; i < names.length; i += size) chunks.push(names.slice(i, i + size));
	return chunks;
}

// Sends `names` `size` at a time, one request after another. `send(chunk)` returns a promise of
// `{ ok, payload }`. The first failed request stops it: its names are `failed` (they may or may
// not have run) and the rest are `not_sent`. `on_progress(done, total)` hears each step.
function run_in_chunks(names, size, send, on_progress) {
	const chunks = chunk_names(names, size);
	const results = [];
	let done = 0;
	const step = (index) => {
		if (on_progress) on_progress(done, names.length);
		if (index >= chunks.length) return Promise.resolve({ results, failed: [], not_sent: [] });
		return Promise.resolve()
			.then(() => send(chunks[index]))
			.catch(() => ({ ok: false }))
			.then((outcome) => {
				if (!outcome || !outcome.ok) {
					return {
						results,
						failed: chunks[index],
						not_sent: [].concat(...chunks.slice(index + 1)),
					};
				}
				results.push(...((outcome.payload && outcome.payload.results) || []));
				done += chunks[index].length;
				// Actions the server skipped for time stay Pending. Sending the next chunk would
				// run newer actions ahead of them, so the rest are left unsent too.
				if (outcome.payload && outcome.payload.budget_reached) {
					return { results, failed: [], not_sent: [].concat(...chunks.slice(index + 1)) };
				}
				return step(index + 1);
			});
	};
	return step(0);
}

function progress_text(done, total) {
	return __("{0} of {1} done", [done, total]);
}

// One results payload from what run_in_chunks returned. `labels` maps a name to the tool and
// summary to show for a row the server never answered for.
function batch_payload(outcome, labels) {
	const row_for = (name, status, message) => {
		const known = (labels && labels.get(name)) || {};
		return {
			name,
			tool_name: known.tool_name || "",
			summary: known.summary || "",
			status,
			message,
		};
	};
	const unknown = (outcome.failed || []).map((name) =>
		row_for(
			name,
			"Unknown",
			__("The request carrying this one failed, so it may or may not have run. Open it to see.")
		)
	);
	const not_sent = (outcome.not_sent || []).map((name) =>
		row_for(name, "Not sent", __("Not sent, because an earlier request failed or ran out of time. It is unchanged."))
	);
	return {
		results: [...(outcome.results || []), ...unknown, ...not_sent],
		stopped: unknown.length > 0,
	};
}

// Oldest first, the order gating_api runs a batch in, so a proposal that depends on an earlier
// one runs after it. The list itself is sorted newest first.
function oldest_first(rows) {
	return (rows || [])
		.slice()
		.sort(
			(a, b) =>
				String(a.creation || "").localeCompare(String(b.creation || "")) ||
				String(a.name || "").localeCompare(String(b.name || ""))
		);
}

function labels_for(rows) {
	return new Map((rows || []).map((row) => [row.name, { tool_name: row.tool_name, summary: row.summary }]));
}

function tally(results) {
	const counts = { Executed: 0, Cancelled: 0, Failed: 0, Skipped: 0, Unknown: 0, "Not sent": 0 };
	(results || []).forEach((item) => {
		counts[item.status] = (counts[item.status] || 0) + 1;
	});
	return counts;
}

function results_headline(kind, counts) {
	const done =
		kind === "confirm"
			? __("Executed {0}", [counts.Executed])
			: __("Cancelled {0}", [counts.Cancelled]);
	let line = `${done}, ${__("failed {0}", [counts.Failed])}, ${__("skipped {0}", [counts.Skipped])}`;
	if (counts.Unknown) line += `, ${__("unknown {0}", [counts.Unknown])}`;
	if (counts["Not sent"]) line += `, ${__("not sent {0}", [counts["Not sent"]])}`;
	return `${line}.`;
}

function form_link(name, text) {
	const url = frappe.utils.get_form_link("AI Pending Action", name);
	return `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(text)}</a>`;
}

function pill(color, text) {
	return `<span class="indicator-pill ${color}">${esc(text)}</span>`;
}

function risk_pill(risk) {
	return pill(RISK_COLOR[risk] || "gray", risk ? __(risk) : "?");
}

function render_review_table(rows, selected) {
	const body = (rows || [])
		.map((row) => {
			const name = esc(row.name);
			const note = starts_ticked(row) ? "" : untick_reason(row);
			const expires =
				row.expires_in_seconds === null || row.expires_in_seconds === undefined
					? __("never")
					: short_duration(row.expires_in_seconds);
			const hidden = row.has_hidden ? pill("orange", __("Hidden values")) + " " : "";
			return (
				`<tr data-name="${name}">` +
				`<td><input type="checkbox" class="ee-apa-check" data-name="${name}"${
					selected.has(row.name) ? " checked" : ""
				}></td>` +
				`<td><code>${esc(row.tool_name)}</code></td>` +
				`<td>${esc(row.summary || row.tool_name)}` +
				(note ? `<div class="text-muted small">${esc(note)}</div>` : "") +
				`</td>` +
				`<td>${risk_pill(row.risk)}</td>` +
				`<td>${esc(short_duration(row.age_seconds))}</td>` +
				`<td>${esc(expires)}</td>` +
				`<td class="text-nowrap">${hidden}${form_link(row.name, __("Open"))}</td>` +
				`</tr>`
			);
		})
		.join("");
	const head = [__("Tool"), __("Summary"), __("Risk"), __("Age"), __("Expires in"), __("Hidden values")]
		.map((label) => `<th>${esc(label)}</th>`)
		.join("");
	const notes = review_notes(rows)
		.map((note) => `<p class="text-muted small">${esc(note)}</p>`)
		.join("");
	return (
		`<div class="ee-apa-batch">` +
		notes +
		`<div class="flex align-center" style="gap: 8px; margin-bottom: 8px;">` +
		`<button type="button" class="btn btn-xs btn-default" data-ee-select="all">${esc(
			__("Select all")
		)}</button>` +
		`<button type="button" class="btn btn-xs btn-default" data-ee-select="none">${esc(
			__("Select none")
		)}</button>` +
		`<span class="text-muted small ee-apa-count"></span>` +
		`</div>` +
		`<div class="table-responsive"><table class="table table-sm table-bordered">` +
		`<thead><tr><th></th>${head}</tr></thead><tbody>${body}</tbody></table></div>` +
		`</div>`
	);
}

function render_selected_confirmation(kind, runnable, skipped) {
	const summary = selection_summary(runnable, new Set(runnable.map((row) => row.name)));
	const question = kind === "confirm" ? execute_question(summary) : cancel_question(summary);
	const items = runnable
		.map(
			(row) =>
				`<li>${esc(row.summary || row.tool_name || row.name)} ${risk_pill(row.risk)}` +
				(row.has_hidden ? " " + pill("orange", __("Hidden values")) : "") +
				`</li>`
		)
		.join("");
	const skipped_line = skipped
		? `<p class="text-muted">${esc(
				__(
					"{0} of the selected are not yours, not Pending or expired, and will be skipped. A batch only decides your own actions.",
					[skipped]
				)
		  )}</p>`
		: "";
	return `<p>${esc(question)}</p><ul>${items}</ul>${skipped_line}`;
}

function render_results(kind, payload) {
	const results = (payload && payload.results) || [];
	const counts = tally(results);
	const body = results
		.map(
			(item) =>
				`<tr>` +
				`<td class="text-nowrap">${form_link(item.name, item.name)}</td>` +
				`<td>${esc(item.summary || item.tool_name || "")}</td>` +
				`<td>${pill(STATUS_COLOR[item.status] || "gray", item.status ? __(item.status) : "?")}</td>` +
				`<td>${esc(item.message)}</td>` +
				`</tr>`
		)
		.join("");
	const head = [__("Action"), __("Summary"), __("Status"), __("Details")]
		.map((label) => `<th>${esc(label)}</th>`)
		.join("");
	const stopped_note =
		payload && payload.stopped
			? `<p class="text-danger small">${esc(
					__(
						"A request failed, so the batch stopped there. Check the actions marked Unknown before deciding them again; the ones marked Not sent are unchanged."
					)
			  )}</p>`
			: "";
	const skipped_note = counts.Skipped
		? `<p class="text-muted small">${esc(
				__(
					"Skipped actions were left exactly as they were. A batch only decides your own Pending, unexpired actions; open any other one to decide it on its own."
				)
		  )}</p>`
		: "";
	return (
		`<p>${esc(results_headline(kind, counts))}</p>${stopped_note}${skipped_note}` +
		`<div class="table-responsive"><table class="table table-sm table-bordered">` +
		`<thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`
	);
}

// ------------------------------------------------------------------ desk wiring

function call(method, args, options) {
	return frappe.call(
		Object.assign({ method: `${GATING_API}.${method}`, type: "POST", args: args || {} }, options || {})
	);
}

// One batch request, as a promise of `{ ok, payload }` that never rejects.
function send_chunk(method, names) {
	return new Promise((resolve) => {
		let settled = false;
		const settle = (outcome) => {
			if (settled) return;
			settled = true;
			resolve(outcome);
		};
		call(
			method,
			{ names },
			{
				callback: (r) => settle({ ok: true, payload: (r && r.message) || {} }),
				error: () => settle({ ok: false }),
				// v16 runs `always` before `error` on a failed request, and some failures (an
				// expired session, an exception with its own handler) call neither. One tick
				// lets `error` land first, and anything still unsettled is a failure.
				always: () => setTimeout(() => settle({ ok: false }), 0),
			}
		);
	});
}

// v16 puts an entry for every inner button in the page menu, labelled the same (page.js
// add_inner_button). It is the only way in below the xl breakpoint, where the inner toolbar is
// hidden, so it has to follow the button's count and visibility.
function find_menu_entry(listview, label) {
	const menu = listview.page && listview.page.menu;
	if (!menu || !menu.find) return null;
	const $entry = menu
		.find("a.grey-link")
		.filter(function () {
			return $(this).find(".menu-item-label").text().trim() === label;
		})
		.last();
	return $entry.length ? $entry : null;
}

function update_review_button(listview, count) {
	let $button = listview.ee_apa_review_button;
	let $entry = listview.ee_apa_review_entry;
	if (!count) {
		if ($button) $button.addClass("hide");
		if ($entry) $entry.parent().addClass("hide");
		return;
	}
	// The label passed to add_inner_button stays fixed, and the count goes into the text. Every
	// new label would leave another entry in the page menu, whose de-duplication never matches.
	if (!$button || !document.body.contains($button[0])) {
		if ($entry) $entry.parent().remove();
		$button = listview.page.add_inner_button(
			__(REVIEW_LABEL),
			() => open_review_dialog(listview),
			null,
			"primary"
		);
		listview.ee_apa_review_button = $button;
		$entry = find_menu_entry(listview, __(REVIEW_LABEL));
		listview.ee_apa_review_entry = $entry;
	}
	const text = __("Review My Pending ({0})", [count_label(count)]);
	$button.removeClass("hide").text(text);
	if ($entry) {
		$entry.parent().removeClass("hide");
		$entry.find(".menu-item-label").text(text);
	}
}

function refresh_review_button(listview) {
	if (listview.ee_apa_counting) {
		// Count once more when this one lands, so the button is never older than the last change.
		listview.ee_apa_recount = true;
		return;
	}
	listview.ee_apa_counting = true;
	listview.ee_apa_recount = false;
	call(
		"my_pending_actions",
		{},
		{
			silent: true,
			callback: (r) => update_review_button(listview, ((r && r.message) || []).length),
			// `always` runs on every outcome, including the ones that call neither callback nor
			// error (an expired session), so the count can never stay stuck mid-flight.
			always: () => {
				listview.ee_apa_counting = false;
				if (listview.ee_apa_recount) refresh_review_button(listview);
			},
		}
	);
}

// v16's realtime list updates (a new proposal, the hourly expiry sweep, a decision made
// elsewhere) go list_update -> listview.debounced_refresh -> process_document_refreshes ->
// render_list, and never reach listview_settings.refresh. The realtime handler looks up
// `this.debounced_refresh` on every event, so wrapping it, original kept, hears each one.
// A separate frappe.realtime.on("list_update") would not survive: setup_realtime_updates calls
// frappe.realtime.off("list_update") with no handler, which removes every listener.
function watch_realtime_updates(listview, on_update, wait) {
	const original = listview && listview.debounced_refresh;
	if (typeof original !== "function") return false;
	if (original.ee_apa_watching) return true;
	const recount = frappe.utils.debounce(() => on_update(listview), wait);
	const watching = function (...args) {
		recount();
		return original.apply(this, args);
	};
	watching.ee_apa_watching = true;
	listview.debounced_refresh = watching;
	return true;
}

function after_batch(listview) {
	if (listview.clear_checked_items) listview.clear_checked_items();
	listview.refresh();
	refresh_review_button(listview);
}

// A yes/no question whose message is block HTML. frappe.confirm wraps its message in a <p>,
// which a <p> or <ul> cannot sit inside.
function ask(title, html, on_yes) {
	const dialog = new frappe.ui.Dialog({
		title,
		fields: [{ fieldtype: "HTML", fieldname: "message" }],
		primary_action_label: __("Yes"),
		primary_action: () => {
			dialog.hide();
			on_yes();
		},
		secondary_action_label: __("No"),
		secondary_action: () => dialog.hide(),
	});
	dialog.fields_dict.message.$wrapper.html(html);
	dialog.show();
	return dialog;
}

function show_results(kind, payload) {
	const dialog = new frappe.ui.Dialog({
		title: kind === "confirm" ? __("Batch Confirmation Results") : __("Batch Cancellation Results"),
		size: "extra-large",
		fields: [{ fieldtype: "HTML", fieldname: "results" }],
		primary_action_label: __("Close"),
		primary_action: () => dialog.hide(),
	});
	dialog.fields_dict.results.$wrapper.html(render_results(kind, payload));
	dialog.show();
}

function run_batch(listview, kind, names, labels) {
	const confirming = kind === "confirm";
	const title = confirming
		? __("Executing {0}", [how_many(names.length)])
		: __("Cancelling {0}", [how_many(names.length)]);
	return run_in_chunks(
		names,
		CHUNK_SIZE,
		(chunk) => send_chunk(confirming ? "confirm_actions" : "cancel_actions", chunk),
		(done, total) => frappe.show_progress(title, done, total, progress_text(done, total))
	).then((outcome) => {
		frappe.hide_progress();
		show_results(kind, batch_payload(outcome, labels));
		after_batch(listview);
	});
}

function show_review_dialog(listview, rows) {
	const selected = initial_selection(rows);
	let dialog = null;

	const decide = (kind) => {
		const chosen = rows.filter((row) => selected.has(row.name));
		if (!chosen.length) {
			frappe.show_alert({ message: __("Tick at least one action."), indicator: "orange" });
			return;
		}
		const problem = batch_limit_problem(chosen.length);
		if (problem) {
			// The review dialog stays open, with the selection as it was.
			frappe.msgprint(problem);
			return;
		}
		const summary = selection_summary(rows, selected);
		const question = kind === "confirm" ? execute_question(summary) : cancel_question(summary);
		ask(__("Confirm"), `<p>${esc(question)}</p>`, () => {
			dialog.hide();
			run_batch(
				listview,
				kind,
				chosen.map((row) => row.name),
				labels_for(chosen)
			);
		});
	};

	dialog = new frappe.ui.Dialog({
		title: __("Review My Pending AI Actions"),
		size: "extra-large",
		fields: [{ fieldtype: "HTML", fieldname: "review" }],
		primary_action_label: __("Confirm & Execute ({0})", [selected.size]),
		primary_action: () => decide("confirm"),
		secondary_action_label: __("Cancel ({0})", [selected.size]),
		secondary_action: () => decide("cancel"),
	});
	const $wrapper = dialog.fields_dict.review.$wrapper;
	$wrapper.html(render_review_table(rows, selected));

	const sync = () => {
		dialog.get_primary_btn().text(__("Confirm & Execute ({0})", [selected.size]));
		dialog.get_secondary_btn().text(__("Cancel ({0})", [selected.size]));
		$wrapper.find(".ee-apa-count").text(selection_count_text(selected.size, rows.length));
		$wrapper.find(".ee-apa-check").each(function () {
			$(this).prop("checked", selected.has(String($(this).attr("data-name"))));
		});
	};

	$wrapper.on("change", ".ee-apa-check", function () {
		const name = String($(this).attr("data-name"));
		if (this.checked) selected.add(name);
		else selected.delete(name);
		sync();
	});
	$wrapper.on("click", "[data-ee-select]", function () {
		selected.clear();
		if ($(this).attr("data-ee-select") === "all") {
			select_all(rows).forEach((name) => selected.add(name));
		}
		sync();
	});

	sync();
	dialog.show();
	return dialog;
}

function open_review_dialog(listview) {
	call(
		"my_pending_actions",
		{},
		{
			freeze: true,
			callback: (r) => {
				const rows = (r && r.message) || [];
				update_review_button(listview, rows.length);
				if (!rows.length) {
					frappe.msgprint(__("Nothing is waiting for your confirmation."));
					return;
				}
				show_review_dialog(listview, rows);
			},
		}
	);
}

function decide_selected(listview, kind) {
	const rows = listview.get_checked_items();
	if (!rows.length) return;
	const problem = batch_limit_problem(rows.length);
	if (problem) {
		frappe.msgprint(problem);
		return;
	}
	const confirm_with = (pending) => {
		const runnable = runnable_rows(rows, pending, frappe.session.user);
		if (!runnable.length) {
			frappe.msgprint(
				__(
					"None of the selected actions are yours, Pending and unexpired, so there is nothing to decide. A batch only decides your own actions; open another person's action to decide it on its own."
				)
			);
			return;
		}
		ask(__("Confirm"), render_selected_confirmation(kind, runnable, rows.length - runnable.length), () =>
			run_batch(
				listview,
				kind,
				oldest_first(rows).map((row) => row.name),
				labels_for(runnable)
			)
		);
	};
	call(
		"my_pending_actions",
		{},
		{
			silent: true,
			callback: (r) => confirm_with((r && r.message) || []),
			error: () => confirm_with(null),
		}
	);
}

frappe.listview_settings["AI Pending Action"] = {
	// requested_by and status are not list columns, and the selected-rows confirmation needs them.
	add_fields: ["requested_by", "status", "risk", "tool_name", "summary"],

	onload(listview) {
		listview.page.add_actions_menu_item(__("Confirm & Execute Selected"), () =>
			decide_selected(listview, "confirm")
		);
		listview.page.add_actions_menu_item(__("Cancel Selected"), () =>
			decide_selected(listview, "cancel")
		);
		watch_realtime_updates(listview, refresh_review_button, REALTIME_RECOUNT_MS);
		refresh_review_button(listview);
	},

	refresh(listview) {
		refresh_review_button(listview);
	},
};
