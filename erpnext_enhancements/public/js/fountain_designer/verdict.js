// The verdict panel: always beside the step (below it on a phone). Chips for the blockers, the warnings
// and whether the package is ready; the top issues, each with a link to the step that fixes it; the
// rollups; the duty-point chart. Pure: it takes a derive reply and returns markup, with the escape, the
// chip and the translator handed in (sf_kit's in the page, plain stand-ins under node).

import { SECTIONS } from "./model.js";
import { STEP_KEYS, steps_for } from "./router.js";
import { chart_html } from "./chart.js";

// The rollups, in the order the panel lists them (PLAN.md 3.2's rollup fields).
export const ROLLUPS = [
	{ key: "design_flow_gpm", label: "Design flow", unit: "gpm", digits: 0 },
	{ key: "operating_flow_gpm", label: "Operating flow", unit: "gpm", digits: 0 },
	{ key: "tdh_ft", label: "TDH", unit: "ft", digits: 1 },
	{ key: "operating_head_ft", label: "Operating head", unit: "ft", digits: 1 },
	{ key: "selected_pump", label: "Pump", kind: "text" },
	{ key: "pump_kw", label: "Pump power", unit: "kW", digits: 2 },
	{ key: "total_basin_gallons", label: "Basin water", unit: "gal", digits: 0 },
	{ key: "budget_total", label: "Budget", kind: "money" },
	{ key: "om_cost_per_year", label: "O&M per year", kind: "money" },
];

const SEVERITY_ORDER = { blocker: 0, warning: 1, info: 2 };

// The step an issue is fixed on: its own `step` when the server names one, else its section's step
// (model.SECTIONS), else Site for the site's checks, else Review.
export function step_for_issue(issue) {
	if (!issue) return "review";
	if (STEP_KEYS.includes(issue.step)) return issue.step;
	const section = SECTIONS.find((s) => s.key === issue.section || s.key === section_alias(issue.section));
	if (section) return section.step;
	if (issue.section === "site" || issue.section === "project") return "site";
	return "review";
}

// The issue vocabulary of issues.py (v1) beside the model's sections (v2).
function section_alias(section) {
	return { basins: "basin", tiers: "features", nozzles: "features", pipe: "piping", pumps: "pump" }[section] || section;
}

// The "Go to" link for an issue, or "" when its step is not one this mode shows (a basin issue on a
// quick design: its remedy button still works, and Full mode has the step).
export function goto_html(issue, h, mode) {
	const step = step_for_issue(issue);
	if (mode && !steps_for(mode).includes(step)) return "";
	return `<button type="button" class="fd-link" data-fd-goto="${h.esc(step)}" data-fd-focus="${h.esc(issue.path || "")}">${h.esc(
		h.__("Go to {0}", [step_word(step, h.__)])
	)}</button>`;
}

export function sort_issues(issues) {
	return (Array.isArray(issues) ? issues : [])
		.filter((i) => i && typeof i === "object")
		.map((issue, index) => ({ issue, index }))
		.sort((a, b) => sev(a.issue) - sev(b.issue) || a.index - b.index)
		.map((x) => x.issue);
}

const sev = (issue) => (issue.severity in SEVERITY_ORDER ? SEVERITY_ORDER[issue.severity] : 3);

export function counts(reply) {
	const issues = (reply && reply.issues) || [];
	const roll = (reply && reply.rollups) || {};
	const n = (severity) => issues.filter((i) => i && i.severity === severity).length;
	return {
		blockers: Number.isFinite(roll.blocker_count) ? roll.blocker_count : n("blocker"),
		warnings: Number.isFinite(roll.warning_count) ? roll.warning_count : n("warning"),
		ready: Boolean((reply && reply.verdict && reply.verdict.package_ready) || roll.issue_ready),
	};
}

// A number for a person: grouped thousands, fixed digits, the unit after it. Blank for nothing.
export function format_value(value, spec) {
	const s = spec || {};
	if (value === null || value === undefined || value === "") return "";
	if (s.kind === "text") return String(value);
	const n = Number(value);
	if (!Number.isFinite(n)) return String(value);
	if (s.kind === "money") {
		return "$" + n.toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: 0 });
	}
	const digits = s.digits == null ? 1 : s.digits;
	const text = n.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
	return s.unit ? `${text} ${s.unit}` : text;
}

// h: { esc, chip(label, tone), __, parser }. state: { pending, error, mode }.
export function verdict_html(reply, h, state) {
	const { esc, chip, __ } = h;
	const st = state || {};
	const c = counts(reply);
	const v = (reply && reply.verdict) || {};
	const chips = [
		chip(c.blockers === 1 ? __("1 blocker") : __("{0} blockers", [c.blockers]), c.blockers ? "danger" : "good"),
		chip(c.warnings === 1 ? __("1 warning") : __("{0} warnings", [c.warnings]), c.warnings ? "warn" : "neutral"),
		chip(c.ready ? __("Package ready") : __("Package not ready"), c.ready ? "good" : "neutral"),
	].join(" ");
	const status = st.error
		? `<p class="fd-verdict-status fd-verdict-status--error" role="alert">${esc(st.error)}</p>`
		: st.pending
			? `<p class="fd-verdict-status" role="status">${esc(__("Updating…"))}</p>`
			: `<p class="fd-verdict-status" role="status"></p>`;
	if (!reply) {
		// No reply yet: no count to show, and a green "0 blockers" would claim a check that has not run.
		return `<div class="fd-verdict-chips">${chip(__("Not worked out yet"), "neutral")}</div>${status}<p class="fd-muted">${esc(
			__("The verdict appears once the design has been worked out.")
		)}</p>`;
	}
	const top = sort_issues(reply.issues).filter((i) => i.severity !== "info").slice(0, 3);
	const issues = top.length
		? `<ol class="fd-top-issues">${top.map((i) => top_issue_html(i, h, st.mode)).join("")}</ol>`
		: `<p class="fd-muted">${esc(__("No open issues."))}</p>`;
	const roll = (reply && reply.rollups) || {};
	const rows = ROLLUPS.map((r) => {
		const value = r.key === "selected_pump" ? roll.selected_pump_label || roll.selected_pump : roll[r.key];
		const text = format_value(value, r);
		return `<div class="fd-rollup"><dt>${esc(__(r.label))}</dt><dd data-fd-rollup="${esc(r.key)}">${
			text ? esc(text) : '<span class="fd-muted">—</span>'
		}</dd></div>`;
	}).join("");
	const chart = chart_html(reply.charts && reply.charts.duty_svg, {
		esc,
		parser: h.parser,
		label: __("Duty point: pump curve against the system curve"),
		empty: __("No duty-point chart yet."),
	});
	return `
		<div class="fd-verdict-chips">${chips}</div>
		${status}
		${v.headline ? `<p class="fd-verdict-headline fd-verdict-headline--${esc(level(v.level))}">${esc(v.headline)}</p>` : ""}
		<h3 class="fd-panel-title">${esc(__("Top issues"))}</h3>
		${issues}
		<h3 class="fd-panel-title">${esc(__("Rollups"))}</h3>
		<dl class="fd-rollups">${rows}</dl>
		<h3 class="fd-panel-title">${esc(__("Duty point"))}</h3>
		${chart}`;
}

function level(value) {
	return ["good", "warn", "bad"].includes(value) ? value : "warn";
}

const TONE = { blocker: "danger", warning: "warn", info: "neutral" };
const WORD = { blocker: "Blocker", warning: "Warning", info: "Advisory" };

export function severity_chip(issue, h) {
	return h.chip(h.__(WORD[issue.severity] || "Advisory"), TONE[issue.severity] || "neutral");
}

function top_issue_html(issue, h, mode) {
	return `<li class="fd-top-issue">
		${severity_chip(issue, h)}
		<span class="fd-top-issue-title">${h.esc(issue.title)}</span>
		${goto_html(issue, h, mode)}
	</li>`;
}

export function step_word(step, __) {
	const words = {
		start: "Start",
		site: "Site",
		features: "Features",
		basins: "Basins",
		pump_room: "Pump room",
		review: "Review",
		outputs: "Outputs",
	};
	return __(words[step] || "Review");
}

// The compact strip a phone shows at the top of the stage: the three chips and the budget.
export function strip_html(reply, h) {
	const { esc, chip, __ } = h;
	if (!reply) return chip(__("Not worked out yet"), "neutral");
	const c = counts(reply);
	const budget = reply && reply.rollups ? format_value(reply.rollups.budget_total, { kind: "money" }) : "";
	// Each chip keeps its word: a status is never a bare number or a color.
	return `${chip(c.blockers === 1 ? __("1 blocker") : __("{0} blockers", [c.blockers]), c.blockers ? "danger" : "good")} ${chip(
		c.warnings === 1 ? __("1 warning") : __("{0} warnings", [c.warnings]),
		c.warnings ? "warn" : "neutral"
	)} ${chip(c.ready ? __("Ready") : __("Not ready"), c.ready ? "good" : "neutral")}${
		budget ? ` <span class="fd-strip-budget">${esc(budget)}</span>` : ""
	}`;
}
