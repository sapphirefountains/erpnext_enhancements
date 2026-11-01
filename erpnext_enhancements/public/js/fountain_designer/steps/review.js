// Review. The verdict with every issue and its remedies (one click applies a remedy as an edit), a way to
// the step that fixes it, the sign-off for a warning the designer accepts (acknowledge_issue, with a
// note), the readiness of each section, and the schematic.

import { stage_head } from "../ui.js";
import { goto_html, severity_chip, sort_issues } from "../verdict.js";
import { chart_html } from "../chart.js";

export const key = "review";

export function render(ctx) {
	const { h, reply } = ctx;
	const { esc, __ } = h;
	const head = stage_head(h, __("Review"), __("What works, what does not, and what to do about it."));
	if (!reply) return head + h.state_html("loading", { title: __("Working out the design…") });
	const v = reply.verdict || {};
	const issues = sort_issues(reply.issues);
	const list = issues.length
		? `<ul class="fd-issues">${issues.map((i) => issue_html(ctx, i)).join("")}</ul>`
		: h.state_html("empty", { title: __("No issues."), body: __("Nothing for the design team to fix.") });
	const sections = ((reply.readiness && reply.readiness.sections) || [])
		.map((s) => {
			const tone = { ok: "good", warn: "warn", blocked: "danger", missing: "warn", n_a: "neutral" }[s.status] || "neutral";
			const word = { ok: "Ready", warn: "Warnings", blocked: "Blocked", missing: "Missing", n_a: "Not needed" }[s.status] || s.status;
			const missing = (s.missing || []).map((m) => esc(typeof m === "string" ? m : m && m.label)).join("; ");
			return `<tr><th scope="row">${esc(__(s.label || s.key))}</th><td>${h.chip(__(word || ""), tone)}</td><td>${missing}</td></tr>`;
		})
		.join("");
	return `${head}
		<section class="fd-card fd-verdict-card fd-verdict-card--${esc(["good", "warn", "bad"].includes(v.level) ? v.level : "warn")}">
			<p class="fd-verdict-big">${esc(v.headline || __("Worked out."))}</p>
		</section>
		<section class="fd-card"><header class="fd-card-head"><h3 class="fd-card-title">${esc(__("Issues"))}</h3></header>${list}</section>
		${
			sections
				? `<section class="fd-card"><header class="fd-card-head"><h3 class="fd-card-title">${esc(__("Readiness"))}</h3></header>
				<div class="fd-table-wrap"><table class="fd-table"><caption class="fd-visually-hidden">${esc(__("Readiness by section"))}</caption>
				<thead><tr><th scope="col">${esc(__("Section"))}</th><th scope="col">${esc(__("Status"))}</th><th scope="col">${esc(__("Still needed"))}</th></tr></thead>
				<tbody>${sections}</tbody></table></div></section>`
				: ""
		}
		<section class="fd-card"><header class="fd-card-head"><h3 class="fd-card-title">${esc(__("Schematic"))}</h3></header>
			${chart_html(reply.charts && reply.charts.schematic_svg, { esc, parser: h.parser, label: __("Schematic"), empty: __("No schematic yet.") })}
		</section>`;
}

function issue_html(ctx, issue) {
	const { h } = ctx;
	const { esc, __ } = h;
	const remedies = (issue.remedies || [])
		.map(
			(r, n) =>
				`<button type="button" class="sf-btn sf-btn--secondary" data-fd-remedy="${esc(issue.key)}" data-fd-n="${n}"${
					ctx.can_write ? "" : " disabled"
				}>${esc(r.label)}</button>`
		)
		.join("");
	const ack = issue.acknowledged;
	const can_ack = issue.severity === "warning" && !ack && ctx.can_write && ctx.design && ctx.design.name;
	const acking = ctx.acking === issue.key;
	const ack_form = acking
		? `<form class="fd-ack" data-fd-ack-form="${esc(issue.key)}">
			<label class="fd-label" for="fd-ack-note">${esc(__("Why this is acceptable"))}</label>
			<textarea class="fd-input fd-textarea" id="fd-ack-note" rows="2" required></textarea>
			<div class="fd-actions">
				<button type="submit" class="sf-btn sf-btn--primary">${esc(__("Sign off"))}</button>
				<button type="button" class="sf-btn sf-btn--quiet" data-fd-action="ack-cancel">${esc(__("Cancel"))}</button>
			</div>
		</form>`
		: "";
	return `<li class="fd-issue" data-fd-issue="${esc(issue.key)}">
		<div class="fd-issue-head">${severity_chip(issue, h)} <strong>${esc(issue.title)}</strong></div>
		${issue.detail ? `<p>${esc(issue.detail)}</p>` : ""}
		${issue.fix_hint ? `<p class="fd-hint">${esc(issue.fix_hint)}</p>` : ""}
		${issue.citation ? `<p class="fd-muted">${esc(__("Source: {0}", [issue.citation]))}</p>` : ""}
		${ack ? `<p class="fd-ack-done">${h.chip(__("Signed off"), "good")} ${esc(__("{0} on {1}: {2}", [ack.by || "", String(ack.on || "").slice(0, 16), ack.note || ""]))}</p>` : ""}
		<div class="fd-actions">
			${remedies}
			${goto_html(issue, h, ctx.model.mode)}
			${can_ack && !acking ? `<button type="button" class="sf-btn sf-btn--quiet" data-fd-ack="${esc(issue.key)}">${esc(__("Acknowledge"))}</button>` : ""}
		</div>
		${ack_form}
	</li>`;
}
