/*
 * Planner kit: Simple mode, the quiet list both planners switch to (docs/planner-simple-mode.md; Nik,
 * 2026-10-22: "an easy view that is simple and doesn't overwhelm the user").
 *
 *   const view = planner_kit.simple.mount(root, {
 *     title: "Project Planner",                // the heading
 *     placeholder: "Search jobs…",              // the search box's hint
 *     noun: ["job", "jobs"],                    // "20 of 640 jobs"
 *     load: (search, start, limit) => Promise,  // the page's read: get_simple_jobs / get_simple_sites
 *     open: (doctype, name) => ...,             // an item, or a card's name (a Project)
 *     off: () => ...,                           // the switch, turned off: the full planner
 *     help: () => ...,                          // "?"
 *     is_phone: () => bool,                     // Coming up above Done
 *   });
 *   view.refresh();                             // read again: the same search, as many cards as are shown
 *   view.render();  view.state();  view.root();
 *
 * A card per job (or site), split in two: **Done** (grey, newest first) and **Coming up** (bold, late first),
 * three of each, and "Show all N" opening that half in place to the whole job, start to finish. It writes
 * nothing. Its own controls take no history entry: the search box and "Show all" are not screens, and
 * "Show more" adds cards below. The switch and a click on an item or a card's name are the page's to route.
 *
 * Everything a record says (a subject, a name, a customer) goes through `escape`. `card_html`,
 * `item_html`, `initials`, `day_text` and `span_text` are pure, so scripts/test_planner_simple.js runs
 * this file under node. The rules are planner_kit.bundle.css (.pk-simple-*).
 */

import { escape, t } from "./escape.js";

// Items each half shows before "Show all".
export const SHOWN = 3;
// The search waits this long after the last key before it reads again.
export const SEARCH_MS = 300;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// "Korben Jessop" -> "KJ", "Nik" -> "N", "jesse.griffin@x.com" -> "JG". Two letters at most.
export function initials(name) {
	const text = String(name || "").split("@")[0].trim();
	if (!text) return "";
	const words = text.split(/[\s._-]+/).filter(Boolean);
	const letters = (words.length > 1 ? [words[0], words[words.length - 1]] : [words[0]]).map((w) => w.charAt(0));
	return letters.join("").toUpperCase();
}

// "Oct 20" within the year holding `today`, "Oct 20, 2025" otherwise; "" for nothing.
export function day_text(ymd, today) {
	const text = String(ymd || "");
	if (!/^\d{4}-\d{2}-\d{2}/.test(text)) return "";
	const label = `${MONTHS[Number(text.slice(5, 7)) - 1]} ${Number(text.slice(8, 10))}`;
	return today && String(today).slice(0, 4) === text.slice(0, 4) ? label : `${label}, ${text.slice(0, 4)}`;
}

// A card's span: "Oct 1, 2026 – Apr 28, 2027", one day alone, or "" with no dates. Always with the year.
export function span_text(first, last) {
	const a = day_text(first, null);
	const b = day_text(last, null);
	if (!a && !b) return "";
	return !b || a === b ? a || b : `${a} – ${b}`;
}

const STATE_MARK = { done: "✓", closed: "–", late: "!", open: "", unscheduled: "" };

// One item: its date, what it is, who. Clickable when it names a record.
export function item_html(item, today) {
	const state = Object.prototype.hasOwnProperty.call(STATE_MARK, item.state) ? item.state : "open";
	const when = item.date ? day_text(item.date, today) : "";
	const until = item.end ? ` – ${day_text(item.end, today)}` : "";
	const notes = [];
	if (state === "late") notes.push(t("late"));
	if (state === "closed") notes.push(t("not done"));
	if (item.review) notes.push(t("in review"));
	if (item.kind === "projected") notes.push(t("planned"));
	if (item.kind === "group" && Array.isArray(item.of) && item.of[1]) notes.push(t("{0} of {1} done", [item.of[0], item.of[1]]));
	const who = (item.who || []).filter(Boolean);
	const people = who.length
		? `<span class="pk-simple-who" title="${escape(who.join(", "))}">${who.map((name) => escape(initials(name))).join(" ")}</span>`
		: "";
	const phase = item.phase ? `<span class="pk-simple-phase">${escape(item.phase)}</span>` : "";
	const body = `
		<span class="pk-simple-mark" aria-hidden="true">${STATE_MARK[state]}</span>
		<span class="pk-simple-when">${escape(when + until)}</span>
		<span class="pk-simple-what">${escape(item.label || item.name || "")}${phase}${
			notes.length ? `<span class="pk-simple-note">${escape(notes.join(" · "))}</span>` : ""
		}</span>
		${people}`;
	const klass = `pk-simple-item pk-simple-${state}`;
	if (!item.doctype || !item.name) return `<li class="${klass}">${body}</li>`;
	return `<li class="${klass}"><button type="button" class="pk-simple-row" data-pk-simple-open="${escape(item.doctype)}" data-pk-simple-name="${escape(
		item.name
	)}">${body}</button></li>`;
}

// One half of a card. `open`: the whole list, not the first SHOWN.
function half_html(card, key, items, extra, open, today) {
	const list = open ? items.concat(extra) : items.slice(0, SHOWN);
	const total = items.length + extra.length;
	const heading = key === "done" ? t("Done") : t("Coming up");
	const empty = key === "done" ? t("Nothing done yet") : card.finished ? t("Finished") : t("Nothing booked");
	const rows = list.length ? `<ul class="pk-simple-list">${list.map((item) => item_html(item, today)).join("")}</ul>` : `<div class="pk-simple-empty">${escape(empty)}</div>`;
	const hidden = total - list.length;
	const notes = [];
	if (!open && hidden > 0) {
		notes.push(
			`<button type="button" class="pk-simple-all" data-pk-simple-all="${escape(card.name)}" data-pk-simple-half="${key}" aria-expanded="false">${escape(
				t("Show all {0}", [total])
			)}</button>`
		);
	} else if (open && total > Math.min(items.length, SHOWN)) {
		// Whatever the closed half would leave out (the first SHOWN, never the not-scheduled ones) can be put away again.
		notes.push(
			`<button type="button" class="pk-simple-all" data-pk-simple-all="${escape(card.name)}" data-pk-simple-half="${key}" aria-expanded="true">${escape(
				t("Show fewer")
			)}</button>`
		);
	}
	if (key === "coming" && extra.length && !open) notes.push(`<span class="pk-simple-note">${escape(t("{0} not scheduled", [extra.length]))}</span>`);
	return `
		<section class="pk-simple-half pk-simple-${key}">
			<h4 class="pk-simple-head">${escape(heading)} <span class="pk-simple-count">${escape(String(total))}</span></h4>
			${rows}
			${notes.length ? `<div class="pk-simple-foot">${notes.join(" ")}</div>` : ""}
		</section>`;
}

// One card. `open` is the set of "<card>|<half>" keys opened to the whole list; `phone` puts Coming up first.
export function card_html(card, today, open, phone) {
	const is_open = (half) => !!(open && open.has(`${card.name}|${half}`));
	const facts = [];
	if (card.pm) facts.push(t("PM {0}", [card.pm]));
	if (card.customer && card.customer !== card.title) facts.push(card.customer);
	const span = span_text(card.first, card.last);
	if (span) facts.push(span);
	const status = card.status ? `<span class="pk-simple-status${card.finished ? " pk-simple-finished" : ""}">${escape(t(card.status))}</span>` : "";
	const done = half_html(card, "done", card.done || [], [], is_open("done"), today);
	const coming = half_html(card, "coming", card.coming || [], card.unscheduled || [], is_open("coming"), today);
	return `
		<article class="pk-simple-card${card.finished ? " pk-simple-finished" : ""}" data-pk-simple-card="${escape(card.name)}">
			<header class="pk-simple-title">
				<button type="button" class="pk-simple-name" data-pk-simple-open="Project" data-pk-simple-name="${escape(card.name)}">${escape(card.title || card.name)}</button>
				${status}
				<div class="pk-simple-facts">${escape(facts.join(" · "))}</div>
			</header>
			<div class="pk-simple-halves">${phone ? coming + done : done + coming}</div>
		</article>`;
}

// The "?" help, as planner_kit.legend takes it. `kind` is "jobs" (the Project Planner) or "sites".
export function legend_sections(kind) {
	const item = (text) => ({ sample_html: "", text });
	const sample = (state, text) => ({ sample_html: `<span class="pk-simple-${state}"><span class="pk-simple-mark">${STATE_MARK[state]}</span></span>`, text });
	const sites = kind === "sites";
	return [
		{
			title: t("Simple mode"),
			items: [
				item(
					sites
						? t("Every maintenance site, one card each: the visits done on the left and the visits coming up on the right, from its first visit to its next.")
						: t("Every job, one card each: what is done on the left and what is coming up on the right, from the job's first day to its last.")
				),
				item(t("Search finds any job by its name, number or customer, however old. Show all opens a half to the whole list.")),
				item(t("Nothing here changes the schedule. Click an item to open it, or turn Simple off for the full planner. This device remembers which you used last.")),
			],
		},
		{
			title: t("What the marks mean"),
			items: [
				sample("done", t("Done, with the day it was finished.")),
				sample("late", t("Late: not done, and its last day has passed.")),
				sample("closed", t("Never done, on a job that is finished or canceled.")),
				item(t("Not scheduled counts open work with no date yet. Initials are the crew and the people assigned; hover for their names.")),
			].concat(
				sites
					? [item(t("Planned is a visit the contract will schedule; it is not drafted yet. In review is a visit done and waiting for approval."))]
					: [item(t("A job from before the phase templates lists its own groups, each with how much of it is done."))]
			),
		},
	];
}

export function create_simple(env) {
	function mount(root, opts) {
		const o = opts || {};
		const noun = o.noun || ["job", "jobs"];
		const s = { cards: [], total: 0, more: false, search: "", loading: false, error: false, open: new Set(), today: "", token: 0 };
		let timer = null;
		root.classList.add("pk-simple");
		root.innerHTML = `
			<div class="pk-simple-bar">
				<h3 class="pk-simple-heading"></h3>
				<input type="search" class="form-control input-sm pk-simple-search" autocomplete="off">
				<button type="button" class="btn btn-default btn-sm pk-simple-switch" role="switch" aria-checked="true"></button>
				<button type="button" class="btn btn-default btn-sm pk-simple-help">?</button>
			</div>
			<div class="pk-simple-status-line" aria-live="polite"></div>
			<div class="pk-simple-cards"></div>
			<div class="pk-simple-tail"></div>`;
		const $q = (sel) => root.querySelector(sel);
		$q(".pk-simple-heading").textContent = o.title || "";
		const search = $q(".pk-simple-search");
		search.setAttribute("placeholder", o.placeholder || t("Search…"));
		search.setAttribute("aria-label", o.placeholder || t("Search"));
		const toggle = $q(".pk-simple-switch");
		toggle.textContent = t("Simple");
		toggle.setAttribute("title", t("Simple mode is on. Turn it off for the full planner."));
		const help = $q(".pk-simple-help");
		help.setAttribute("title", t("How Simple mode works"));
		help.setAttribute("aria-label", t("How Simple mode works"));

		const phone = () => !!(typeof o.is_phone === "function" && o.is_phone());

		function render() {
			const cards = $q(".pk-simple-cards");
			const line = $q(".pk-simple-status-line");
			const tail = $q(".pk-simple-tail");
			if (s.error && !s.cards.length) {
				line.textContent = "";
				cards.innerHTML = `<div class="pk-simple-empty">${escape(t("Simple mode could not be loaded."))} <button type="button" class="btn btn-default btn-xs pk-simple-retry">${escape(
					t("Try again")
				)}</button></div>`;
				tail.innerHTML = "";
				return;
			}
			if (!s.cards.length) {
				line.textContent = s.loading ? t("Loading…") : "";
				cards.innerHTML = s.loading ? "" : `<div class="pk-simple-empty">${escape(s.search ? t("Nothing matches “{0}”.", [s.search]) : t("Nothing to show yet."))}</div>`;
				tail.innerHTML = "";
				return;
			}
			line.textContent = s.loading ? t("Loading…") : t("{0} of {1} {2}", [s.cards.length, s.total, s.total === 1 ? noun[0] : noun[1]]);
			cards.innerHTML = s.cards.map((card) => card_html(card, s.today, s.open, phone())).join("");
			tail.innerHTML = s.more
				? `<button type="button" class="btn btn-default btn-sm pk-simple-more"${s.loading ? " disabled" : ""}>${escape(t("Show more {0}", [noun[1]]))}</button>`
				: s.error
				? `<div class="pk-simple-empty">${escape(t("More could not be loaded."))} <button type="button" class="btn btn-default btn-xs pk-simple-retry">${escape(t("Try again"))}</button></div>`
				: "";
		}

		// Reads `start`..`start + limit`; `append` adds them below, otherwise they replace what is shown. An
		// answer for an older request (a search typed since) is dropped.
		function read(start, limit, append) {
			if (typeof o.load !== "function") return Promise.resolve();
			const token = ++s.token;
			s.loading = true;
			s.error = false;
			render();
			return Promise.resolve(o.load(s.search, start, limit))
				.then((answer) => {
					if (token !== s.token) return;
					const a = answer || {};
					const got = Array.isArray(a.cards) ? a.cards : [];
					s.cards = append ? s.cards.concat(got) : got;
					s.total = Number(a.total) || s.cards.length;
					s.more = !!a.more;
					s.today = a.today || s.today;
					s.loading = false;
					render();
				})
				.catch((e) => {
					if (token !== s.token) return;
					s.loading = false;
					s.error = true;
					if (env && env.warn) env.warn(e);
					render();
				});
		}

		function refresh() {
			return read(0, Math.max(s.cards.length, 0) || null, false);
		}

		search.addEventListener("input", () => {
			if (timer) clearTimeout(timer);
			timer = setTimeout(() => {
				timer = null;
				const text = search.value.trim();
				if (text === s.search) return;
				s.search = text;
				s.open.clear();
				read(0, null, false);
			}, SEARCH_MS);
		});
		search.addEventListener("keydown", (e) => {
			// The planners' own shortcuts never act on what is typed here. Esc still reaches the kit and the Desk.
			if (e.key !== "Escape") e.stopPropagation();
		});
		toggle.addEventListener("click", () => typeof o.off === "function" && o.off());
		help.addEventListener("click", () => typeof o.help === "function" && o.help());
		root.addEventListener("click", (e) => {
			const target = e.target && e.target.closest ? e.target : null;
			if (!target) return;
			const all = target.closest("[data-pk-simple-all]");
			if (all) {
				const key = `${all.getAttribute("data-pk-simple-all")}|${all.getAttribute("data-pk-simple-half")}`;
				if (s.open.has(key)) s.open.delete(key);
				else s.open.add(key);
				render();
				const again = root.querySelector(
					`[data-pk-simple-card="${CSS_escape(all.getAttribute("data-pk-simple-all"))}"] [data-pk-simple-half="${all.getAttribute("data-pk-simple-half")}"]`
				);
				if (again && again.focus) again.focus();
				return;
			}
			const row = target.closest("[data-pk-simple-open]");
			if (row) {
				if (typeof o.open === "function") o.open(row.getAttribute("data-pk-simple-open"), row.getAttribute("data-pk-simple-name"));
				return;
			}
			if (target.closest(".pk-simple-more")) {
				read(s.cards.length, null, true);
				return;
			}
			if (target.closest(".pk-simple-retry")) {
				if (s.cards.length && s.more) read(s.cards.length, null, true);
				else read(0, null, false);
			}
		});

		return {
			refresh,
			render,
			root: () => root,
			state: () => s,
		};
	}

	return { mount };
}

// An attribute value as a selector string: quotes and backslashes escaped (a document name may hold them).
function CSS_escape(value) {
	return String(value == null ? "" : value).replace(/["\\]/g, "\\$&");
}
