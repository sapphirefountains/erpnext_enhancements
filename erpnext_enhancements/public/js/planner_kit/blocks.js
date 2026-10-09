/*
 * Planner kit: personal blocks and day notes on screen (Phase 6D UI, TASK-2026-02470), for both
 * planners and My week. The backend is `api/planner_blocks.py`.
 *
 * Personal blocks ("unavailable 2–4 pm", "shop day"): planners (the schedulers) block anyone, and each
 * person blocks their own time. Others see "Unavailable"; the planners and the person see the note.
 * Day notes ("Shop meeting 7 am") are for everyone or one group, written by the schedulers.
 *
 *   planner_kit.blocks.chip_html(block, {note, who, date, resource, user, person, compact, editable})
 *   planner_kit.blocks.notes_html(notes, {date, compact, can_add, project_link, full})
 *   planner_kit.blocks.note_list_html(notes, {can_add, project_link, loading})   // the day peek's head
 *   planner_kit.blocks.form({block, note, date, resource, resource_label, people, self_only,
 *                            on_saved, on_deleted})                               // block time
 *   planner_kit.blocks.note_form({note, date, on_saved, on_deleted})              // a day note
 *
 * Rules kept here:
 *
 *   - A block's note is drawn ONLY from what the page passes as `note` (the page reads it from the
 *     payload's `block_notes`, which the server fills per viewer). A note on the block object itself
 *     is never read, so a payload that ever carried one would still show "Unavailable".
 *   - A block is never a booking you can drag: its markup carries no `data-pk-drag` and none of the
 *     planners' card classes, so neither page's drag code can pick it up.
 *   - Everything people typed is escaped.
 *   - `self_only` (My week, a technician on a planner) sends no person at all: the server makes the
 *     block the caller's own, so nobody can block someone else's time from there.
 *   - A note the page could not read is never wiped: the form sends `note` only when it changed.
 *   - Saving never refuses over a conflict. The server answers with the sentence to show ("This
 *     overlaps Dig at Riverwalk; your PM will see it in the Conflict center"), shown as a warning.
 *
 * The renderers are pure, for tests/test_planner_phase6d_ui.py.
 */

import { escape, t, day_label, glow_key, glow_keys } from "./escape.js";

export const METHODS = {
	save_block: "erpnext_enhancements.api.planner_blocks.save_block",
	delete_block: "erpnext_enhancements.api.planner_blocks.delete_block",
	save_note: "erpnext_enhancements.api.planner_blocks.save_day_note",
	delete_note: "erpnext_enhancements.api.planner_blocks.delete_day_note",
	get_notes: "erpnext_enhancements.api.planner_blocks.get_day_notes",
};

// A day note's audience: blank (everyone) or one Planner Resource group (planner_blocks.AUDIENCES).
export const AUDIENCES = ["Field", "PM", "Design", "Subcontractor"];
export const UNAVAILABLE = "Unavailable";

// "14:30" / "14:30:00" -> 870; null when it is not a time.
export function time_minutes(value) {
	const match = /^(\d{1,2}):(\d{2})/.exec(String(value == null ? "" : value).trim());
	if (!match) return null;
	const minutes = Number(match[1]) * 60 + Number(match[2]);
	return minutes >= 0 && minutes <= 24 * 60 ? minutes : null;
}

function hhmm(minutes) {
	const h = Math.floor(minutes / 60);
	const m = minutes % 60;
	return `${h < 10 ? "0" : ""}${h}:${m < 10 ? "0" : ""}${m}`;
}

function clock12(minutes) {
	const hour = Math.floor(minutes / 60);
	const minute = minutes % 60;
	const suffix = hour < 12 || hour === 24 ? "am" : "pm";
	const shown = hour % 12 || 12;
	return [minute ? `${shown}:${minute < 10 ? "0" : ""}${minute}` : String(shown), suffix];
}

// ["14:00", "16:00"] -> "2–4 pm"; ["11:00", "13:30"] -> "11 am–1:30 pm" (the engine's block_window).
export function block_window(slot) {
	if (!Array.isArray(slot) || slot.length !== 2) return "";
	const first = time_minutes(slot[0]);
	const last = time_minutes(slot[1]);
	if (first === null || last === null) return "";
	const [a, a_suffix] = clock12(first);
	const [b, b_suffix] = clock12(last);
	return a_suffix === b_suffix ? `${a}–${b} ${b_suffix}` : `${a} ${a_suffix}–${b} ${b_suffix}`;
}

// The window of a timed block as ["HH:MM", "HH:MM"], or null for an all-day one.
export function block_slot(block) {
	block = block || {};
	if (block.all_day === true || block.all_day === 1 || block.all_day === "1") return null;
	const raw = Array.isArray(block.slot) && block.slot.length === 2 ? block.slot : [block.from_time, block.to_time];
	const first = time_minutes(raw[0]);
	const last = time_minutes(raw[1]);
	if (first === null || last === null || last <= first) return null;
	return [hhmm(first), hhmm(last)];
}

// "Unavailable 2–4 pm" or "Unavailable all day": what everyone sees.
export function block_text(block) {
	const slot = block_slot(block);
	return slot ? t("Unavailable {0}", [block_window(slot)]) : t("Unavailable all day");
}

// One block on a calendar. The note is ONLY `ctx.note` (from the page's block_notes).
export function chip_html(block, ctx) {
	block = block || {};
	ctx = ctx || {};
	const name = block.name || block.ref || block.block || "";
	const slot = block_slot(block);
	const text = ctx.compact ? (slot ? block_window(slot) : t("All day")) : block_text(block);
	const label = ctx.who ? `${ctx.who} · ${text}` : text;
	const note = ctx.note ? String(ctx.note) : "";
	const classes = ["pk-block"];
	if (!slot) classes.push("pk-block-allday");
	if (ctx.compact) classes.push("pk-block-compact");
	if (ctx.editable) classes.push("pk-block-edit");
	const tip = [ctx.who ? `${ctx.who}: ${block_text(block)}` : block_text(block), note, ctx.editable ? t("Click to change or delete it.") : ""]
		.filter(Boolean)
		.join("\n");
	return (
		`<div class="${classes.join(" ")}" role="button" tabindex="0" data-pk-block="${escape(name)}"` +
		` data-pk-block-date="${escape(ctx.date || block.date || "")}" data-pk-block-resource="${escape(ctx.resource || block.resource || "")}"` +
		` data-pk-block-user="${escape(ctx.user || block.user || "")}"` +
		// The person's key for hover glow (hovering their name lights up their blocks too).
		(ctx.person ? ` data-pk-persons="${escape(glow_keys([ctx.person]))}"` : "") +
		` title="${escape(tip)}">` +
		`<span class="pk-block-label">${escape(label)}</span>` +
		(note && !ctx.compact ? `<span class="pk-block-note">${escape(note)}</span>` : "") +
		`</div>`
	);
}

function note_text(note) {
	const place = note && (note.project_title || note.project);
	const audience = note && note.audience ? ` (${t(note.audience)})` : "";
	return `${String((note && note.note) || "").trim()}${place ? ` · ${place}` : ""}${audience}`;
}

function project_html(note, link) {
	if (!note.project) return "";
	const title = escape(note.project_title || note.project);
	return link
		? `<span class="pk-note-project" data-pk-project="${escape(glow_key(note.project))}" title="${escape(
				t("See this project at a glance")
		  )}">${title}</span>`
		: `<span class="pk-note-project">${title}</span>`;
}

function tag_html(note) {
	return note.audience
		? `<span class="pk-note-tag" title="${escape(t("Only for the {0} group", [t(note.audience)]))}">${escape(t(note.audience))}</span>`
		: "";
}

// A day's notes on the calendar: a thin row, truncated, the whole text on hover and a tap opening
// the day; `compact` (the month view) is one small marker; `full` (My week) shows every word.
export function notes_html(notes, ctx) {
	ctx = ctx || {};
	const list = (Array.isArray(notes) ? notes : []).filter((note) => note && String(note.note || "").trim());
	const date = ctx.date || "";
	if (ctx.compact) {
		if (!list.length) return "";
		const tip = list.map(note_text).join("\n");
		return (
			`<span class="pk-notes-mark" role="button" tabindex="0" data-pk-note-open="${escape(date)}" title="${escape(tip)}"` +
			` aria-label="${escape(t("Notes: {0}", [tip]))}">🗒${list.length > 1 ? ` ${list.length}` : ""}</span>`
		);
	}
	const full = ctx.full ? " pk-note-full" : "";
	const open = ctx.full ? "" : ` role="button" tabindex="0" data-pk-note-open="${escape(date)}"`;
	const lines = list.map((note) => {
		return (
			`<div class="pk-note-line${full}"${open} title="${escape(note_text(note))}">` +
			`<span class="pk-note-text">${escape(String(note.note || "").trim())}</span>${tag_html(note)}${project_html(
				note,
				ctx.project_link
			)}</div>`
		);
	});
	const add = ctx.can_add
		? `<button type="button" class="pk-note-add" data-pk-note-add="${escape(date)}" title="${escape(
				t("Add a note for the crew on this day")
		  )}">${escape(t("+ note"))}</button>`
		: "";
	if (!lines.length && !add) return "";
	return `<div class="pk-notes" data-pk-notes-day="${escape(date)}">${lines.join("")}${add}</div>`;
}

// The notes at the top of the day peek: every word, the group and project, and Edit for the planners.
export function note_list_html(notes, ctx) {
	ctx = ctx || {};
	const list = (Array.isArray(notes) ? notes : []).filter((note) => note && String(note.note || "").trim());
	if (ctx.loading) return `<div class="pk-day-notes"><p class="pk-empty">${escape(t("Loading the day's notes…"))}</p></div>`;
	if (!list.length && !ctx.can_add) return "";
	const items = list.map((note) => {
		const project = note.project
			? ctx.project_link
				? `<span class="pk-link" role="button" tabindex="0" data-pk-note-project="${escape(note.project)}">${escape(
						note.project_title || note.project
				  )}</span>`
				: `<span>${escape(note.project_title || note.project)}</span>`
			: "";
		const edit =
			note.can_edit && note.name
				? `<button type="button" class="btn btn-default btn-xs" data-pk-note-edit="${escape(note.name)}">${escape(t("Edit"))}</button>`
				: "";
		return (
			`<li class="pk-day-note"><div class="pk-day-note-text">${escape(String(note.note || "").trim())}</div>` +
			`<div class="pk-day-note-meta">${tag_html(note)}${project}${edit}</div></li>`
		);
	});
	const add = ctx.can_add
		? `<button type="button" class="btn btn-default btn-xs" data-pk-note-add-peek>${escape(t("Add a note"))}</button>`
		: "";
	return (
		`<div class="pk-day-notes"><div class="pk-section-title">${escape(t("Notes for the crew"))}</div>` +
		(items.length ? `<ul class="pk-day-note-list">${items.join("")}</ul>` : `<p class="pk-empty">${escape(t("No notes for this day."))}</p>`) +
		add +
		`</div>`
	);
}

// ---------------------------------------------------------------------- the two forms

export function create_blocks(env) {
	const frappe = () => env.frappe || {};
	const call = (method, args) =>
		Promise.resolve(frappe().call({ method, args: args || {} })).then((r) => (r && r.message) || null);
	const toast = (text, extra) => env.toast && env.toast(text, extra || {});
	const alert = (text) => frappe().msgprint && frappe().msgprint(text);

	function report(result, fallback) {
		const said = result && result.message;
		if (said) toast(said, { tone: "warning", timeout: 14000 });
		else toast(fallback, { tone: "success" });
		((result && result.warnings) || []).forEach((warning) => {
			if (frappe().show_alert) frappe().show_alert({ message: warning, indicator: "orange" }, 10);
		});
	}

	// Block time, or change and delete a block. `people` ([{value, label}]) offers a person to pick
	// (the schedulers); without it the form shows who it is for and sends no person when `self_only`.
	function form(opts) {
		opts = opts || {};
		const Dialog = frappe().ui && frappe().ui.Dialog;
		if (!Dialog) return null;
		const block = opts.block && (opts.block.name || opts.block.ref) ? opts.block : null;
		const name = block ? block.name || block.ref : "";
		const slot = block ? block_slot(block) : null;
		const initial_note = String(opts.note || "").trim();
		const people = !opts.self_only && Array.isArray(opts.people) ? opts.people : null;
		const fields = [];
		if (people) {
			fields.push({
				fieldtype: "Select",
				fieldname: "resource",
				label: t("Person"),
				reqd: 1,
				options: [{ label: "", value: "" }].concat(people.map((person) => ({ label: person.label, value: person.value }))),
				default: opts.resource || "",
			});
		} else {
			fields.push({
				fieldtype: "HTML",
				fieldname: "who",
				options: `<p class="pk-form-who">${escape(opts.self_only ? t("Your own time") : opts.resource_label || "")}</p>`,
			});
		}
		fields.push(
			{ fieldtype: "Date", fieldname: "date", label: t("Day"), reqd: 1, default: (block && block.date) || opts.date || "" },
			{ fieldtype: "Check", fieldname: "all_day", label: t("All day"), default: slot ? 0 : 1 },
			{ fieldtype: "Time", fieldname: "from_time", label: t("From"), depends_on: "eval:!doc.all_day", default: slot ? `${slot[0]}:00` : "" },
			{ fieldtype: "Column Break" },
			{ fieldtype: "Time", fieldname: "to_time", label: t("To"), depends_on: "eval:!doc.all_day", default: slot ? `${slot[1]}:00` : "" },
			{ fieldtype: "Section Break" },
			{
				fieldtype: "Small Text",
				fieldname: "note",
				label: t("Note"),
				default: initial_note,
				description: t("Only this person and the planners see the note. Everyone else sees “Unavailable”."),
			},
			{
				fieldtype: "HTML",
				fieldname: "hint",
				options: `<p class="pk-note">${escape(
					t("It saves straight away, even in Draft mode. Booked work it overlaps is listed in the Conflict center.")
				)}</p>`,
			}
		);
		const config = {
			title: block ? t("Unavailable time") : t("Block time"),
			fields,
			primary_action_label: t("Save"),
			primary_action: (values) => save(values),
		};
		if (block && opts.can_delete !== false) {
			config.secondary_action_label = t("Delete");
			config.secondary_action = () => remove();
		}
		const dialog = new Dialog(config);

		function save(values) {
			values = values || {};
			const all_day = !!values.all_day;
			const first = time_minutes(values.from_time);
			const last = time_minutes(values.to_time);
			if (!all_day && (first === null || last === null)) {
				alert(t("Give a from and a to time, or tick All day."));
				return null;
			}
			if (!all_day && last <= first) {
				alert(t("The end time is before the start time."));
				return null;
			}
			const args = { date: values.date, all_day: all_day ? 1 : 0 };
			if (!all_day) {
				args.from_time = values.from_time;
				args.to_time = values.to_time;
			}
			if (name) args.name = name;
			// Only a scheduler picks a person; self_only sends none, so the server makes it the caller's own.
			if (people && !opts.self_only && values.resource) args.resource = values.resource;
			const note = String(values.note || "").trim();
			if (note !== initial_note) args.note = note;
			return call(METHODS.save_block, args)
				.then((result) => {
					if (!result) return;
					dialog.hide();
					const when = all_day ? t("all day") : block_window([values.from_time, values.to_time]);
					report(result, t("Blocked: {0}, {1}", [day_label(values.date), when]));
					if (typeof opts.on_saved === "function") opts.on_saved(result);
				})
				.catch(() => null);
		}

		function remove() {
			const confirm = frappe().confirm;
			const go = () =>
				call(METHODS.delete_block, { name })
					.then((result) => {
						if (!result) return;
						dialog.hide();
						toast(t("The unavailable time is deleted."), { tone: "success" });
						if (typeof opts.on_deleted === "function") opts.on_deleted(result);
					})
					.catch(() => null);
			if (typeof confirm === "function") confirm(t("Delete this unavailable time?"), go);
			else go();
		}

		dialog.show();
		return dialog;
	}

	// A day note: for everyone or one group, optionally about a project. Schedulers only (the server
	// checks); `note` is an existing note to change or delete.
	function note_form(opts) {
		opts = opts || {};
		const Dialog = frappe().ui && frappe().ui.Dialog;
		if (!Dialog) return null;
		const note = opts.note && opts.note.name ? opts.note : null;
		const config = {
			title: note ? t("Day note") : t("Add a day note"),
			fields: [
				{ fieldtype: "Date", fieldname: "date", label: t("Day"), reqd: 1, default: (note && note.date) || opts.date || "" },
				{
					fieldtype: "Small Text",
					fieldname: "note",
					label: t("Note"),
					reqd: 1,
					default: (note && note.note) || "",
					description: t("For example: Shop meeting 7 am, or City inspection at Riverwalk."),
				},
				{
					fieldtype: "Select",
					fieldname: "audience",
					label: t("Who it is for"),
					options: [{ label: t("Everyone"), value: "" }].concat(AUDIENCES.map((group) => ({ label: t(group), value: group }))),
					default: (note && note.audience) || "",
					description: t("It shows on both planners, in My week and in the morning message of the people it is for."),
				},
				{ fieldtype: "Link", fieldname: "project", label: t("Project"), options: "Project", default: (note && note.project) || "" },
			],
			primary_action_label: t("Save"),
			primary_action: (values) => save(values),
		};
		if (note) {
			config.secondary_action_label = t("Delete");
			config.secondary_action = () => remove();
		}
		const dialog = new Dialog(config);

		function save(values) {
			values = values || {};
			const text = String(values.note || "").trim();
			if (!text) {
				alert(t("Write the note first."));
				return null;
			}
			const args = { date: values.date, note: text, audience: values.audience || "", project: values.project || "" };
			if (note) args.name = note.name;
			return call(METHODS.save_note, args)
				.then((result) => {
					if (!result) return;
					dialog.hide();
					toast(t("Note saved for {0}", [day_label(values.date)]), { tone: "success" });
					if (typeof opts.on_saved === "function") opts.on_saved(result);
				})
				.catch(() => null);
		}

		function remove() {
			const confirm = frappe().confirm;
			const go = () =>
				call(METHODS.delete_note, { name: note.name })
					.then((result) => {
						if (!result) return;
						dialog.hide();
						toast(t("The note is deleted."), { tone: "success" });
						if (typeof opts.on_deleted === "function") opts.on_deleted(result);
					})
					.catch(() => null);
			if (typeof confirm === "function") confirm(t("Delete this note?"), go);
			else go();
		}

		dialog.show();
		return dialog;
	}

	// One day's notes for a day outside the dates on screen (the day peek's arrows step past them).
	function get_notes(date) {
		return call(METHODS.get_notes, { start: date, end: date }).then((answer) => ((answer && answer.notes) || {})[date] || []);
	}

	return { form, note_form, get_notes };
}

export const BLOCKS_CSS = `
.pk-block{display:flex;flex-direction:column;gap:1px;min-width:0;padding:2px 6px;border:1px dashed #94a3b8;border-radius:6px;font-size:11px;line-height:1.3;color:var(--text-muted);background-color:var(--card-bg);background-image:repeating-linear-gradient(135deg,transparent 0 5px,rgba(100,116,139,.22) 5px 7px);cursor:default;}
.pk-block.pk-block-edit{cursor:pointer;}
.pk-block.pk-block-edit:hover,.pk-block.pk-block-edit:focus{border-color:var(--primary,#2490ef);outline:none;}
.pk-block.pk-block-allday{border-style:solid;}
.pk-block.pk-block-compact{flex-direction:row;padding:0 4px;font-size:10px;}
.pk-item.pk-k-block{border-left-color:#94a3b8;background-image:repeating-linear-gradient(135deg,transparent 0 5px,rgba(100,116,139,.16) 5px 7px);}
.pk-block-label{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.pk-block-note{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-style:italic;}
.pk-notes{display:flex;flex-wrap:wrap;align-items:center;gap:3px;min-width:0;font-size:11px;}
.pk-note-line{display:flex;align-items:center;gap:4px;min-width:0;max-width:100%;padding:1px 6px;border-radius:6px;background:rgba(234,179,8,.16);color:var(--text-color);cursor:pointer;}
.pk-note-line:hover,.pk-note-line:focus{background:rgba(234,179,8,.3);outline:none;}
.pk-note-line.pk-note-full{cursor:default;white-space:normal;}
.pk-note-text{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0;}
.pk-note-full .pk-note-text{white-space:pre-wrap;}
.pk-note-tag{flex:0 0 auto;font-size:9px;text-transform:uppercase;letter-spacing:.03em;padding:0 4px;border-radius:6px;background:var(--control-bg);color:var(--text-muted);}
.pk-note-project{flex:0 0 auto;font-size:10px;color:var(--primary,#2490ef);white-space:nowrap;}
.pk-note-project[data-pk-project]{cursor:pointer;}
.pk-note-project[data-pk-project]:hover{text-decoration:underline;}
.pk-note-add{border:1px dashed var(--border-color);border-radius:6px;background:none;color:var(--text-muted);font-size:10px;padding:0 6px;line-height:16px;cursor:pointer;}
.pk-note-add:hover,.pk-note-add:focus{color:var(--primary,#2490ef);border-color:var(--primary,#2490ef);outline:none;}
.pk-notes-mark{display:inline-flex;align-items:center;gap:2px;font-size:11px;cursor:pointer;border-radius:6px;padding:0 3px;}
.pk-notes-mark:hover,.pk-notes-mark:focus{background:var(--control-bg);outline:none;}
.pk-day-notes{margin:0 0 10px;}
.pk-day-note-list{list-style:none;margin:0 0 6px;padding:0;}
.pk-day-note{padding:5px 8px;margin-bottom:4px;border-radius:8px;background:rgba(234,179,8,.14);font-size:12px;}
.pk-day-note-text{white-space:pre-wrap;}
.pk-day-note-meta{display:flex;flex-wrap:wrap;align-items:center;gap:6px;margin-top:2px;font-size:11px;}
.pk-form-who{font-weight:600;margin:0 0 6px;}
`;
