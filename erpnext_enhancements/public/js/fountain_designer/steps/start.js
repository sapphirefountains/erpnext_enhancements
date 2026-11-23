// Start. With no design open: a new design (a kit, a name, quick or full) and the designs already made.
// With a design open: its name, its links (Project, Opportunity, Customer) and its mode.

import { card, field, stage_head, tiles } from "../ui.js";
import { format_value } from "../verdict.js";

export const key = "start";

export function render(ctx) {
	return ctx.design ? render_open(ctx) : render_list(ctx);
}

function render_list(ctx) {
	const { h } = ctx;
	const { esc, __ } = h;
	const kits = (ctx.boot.kits || []).map((k) => ({ value: k.key, label: k.label, desc: k.desc }));
	kits.push({ value: "", label: __("Blank"), desc: __("No kit: you add the features yourself.") });
	const form = ctx.new_form || {};
	const create = ctx.can_create
		? card(h, {
				id: "new",
				title: __("New design"),
				simple: `
				${tiles(h, "new.kit", kits, form.kit == null ? (kits[0] && kits[0].value) || "" : form.kit, __("Start from a kit"))}
				${field(h, { path: "new.title", label: "Name", kind: "text", value: form.title || "", hint: "What the customer calls it, e.g. Marriott lobby wall." })}
				${tiles(
					h,
					"new.mode",
					[
						{ value: "quick", label: __("Quick"), desc: __("A kit, a few inputs and a budget. For sales.") },
						{ value: "full", label: __("Full"), desc: __("Every section open. For the design team.") },
					],
					form.mode || "quick",
					__("Mode")
				)}
				<div class="fd-actions"><button type="button" class="sf-btn sf-btn--primary" data-fd-action="create"${
					ctx.busy ? " disabled" : ""
				}>${esc(__("Create design"))}</button></div>`,
			})
		: "";
	// The training demo (?demo=1): every answer from a fixture, nothing saved. Offered here, out of the demo.
	const demo = ctx.demo
		? ""
		: `<p class="fd-hint">${esc(__("Learning the designer? The training demo has three worked designs and saves nothing."))}
			<button type="button" class="fd-link" data-fd-action="open-demo">${esc(__("Open the training demo"))}</button></p>`;
	return `${stage_head(h, __("Fountain Designer"), __("Pick a kit and a name to start a design, or open one already made."))}
		${demo}
		${create}
		<section class="fd-card" aria-labelledby="fd-designs-title">
			<header class="fd-card-head"><h3 class="fd-card-title" id="fd-designs-title">${esc(__("Designs"))}</h3></header>
			<div class="fd-designs" data-fd-designs>${designs_html(ctx)}</div>
		</section>`;
}

export function designs_html(ctx) {
	const { h } = ctx;
	const { esc, __, chip } = h;
	const st = ctx.designs_state || { kind: "loading" };
	if (st.kind === "loading") return h.state_html("loading", { title: __("Loading designs…") });
	if (st.kind === "error") return h.state_html("error", { title: __("Could not load the designs."), body: st.message, action_label: __("Try again") });
	const rows = st.rows || [];
	if (!rows.length) return h.state_html("empty", { title: __("No designs yet."), body: __("Start one above.") });
	return `<ul class="fd-design-list">${rows
		.map((d) => {
			const mode = String(d.mode || "").toLowerCase() === "full" ? __("Full") : __("Quick");
			const chips = [
				chip(mode, "design"),
				d.status ? chip(__(d.status), d.status === "Issued" ? "good" : "neutral") : "",
				d.blocker_count ? chip(d.blocker_count === 1 ? __("1 blocker") : __("{0} blockers", [d.blocker_count]), "danger") : "",
				d.warning_count ? chip(d.warning_count === 1 ? __("1 warning") : __("{0} warnings", [d.warning_count]), "warn") : "",
				d.issue_ready ? chip(__("Package ready"), "good") : "",
			]
				.filter(Boolean)
				.join(" ");
			const budget = format_value(d.budget_total, { kind: "money" });
			return `<li class="fd-design-row">
				<button type="button" class="fd-design-open" data-fd-open="${esc(d.name)}">
					<span class="fd-design-title">${esc(d.design_title || d.name)}</span>
					<span class="fd-muted">${esc(d.name)}${budget ? " · " + esc(budget) : ""}${d.modified ? " · " + esc(String(d.modified).slice(0, 16)) : ""}</span>
				</button>
				<span class="fd-design-chips">${chips}</span>
			</li>`;
		})
		.join("")}</ul>`;
}

function render_open(ctx) {
	const { h, design, model, full } = ctx;
	const { esc, __ } = h;
	const kit = ctx.kit;
	const links = [
		["project", "Project"],
		["opportunity", "Opportunity"],
		["customer", "Customer"],
	]
		.map(
			([fieldname, doctype]) => `<div class="fd-field">
				<span class="fd-label" id="fd-link-${fieldname}-label">${esc(__(doctype))}</span>
				<div class="fd-link-slot" data-fd-link="${fieldname}" data-fd-doctype="${esc(doctype)}" aria-labelledby="fd-link-${fieldname}-label">
					<input type="text" class="fd-input" data-fd-field="${fieldname}" value="${esc(design[fieldname] || "")}"
						aria-labelledby="fd-link-${fieldname}-label"${ctx.can_write ? "" : " disabled"}>
				</div>
			</div>`
		)
		.join("");
	return `${stage_head(h, design.design_title || design.name || __("New design"), kit ? __(kit.label) : __("No kit"))}
		${card(h, {
			id: "identity",
			title: __("The design"),
			simple: `
				<div class="fd-field">
					<label class="fd-label" for="fd-title">${esc(__("Name"))}</label>
					<div class="fd-control"><input type="text" class="fd-input" id="fd-title" data-fd-field="design_title"
						value="${esc(design.design_title || "")}"${ctx.can_write ? "" : " disabled"}></div>
				</div>
				${tiles(
					h,
					"mode",
					[
						{ value: "quick", label: __("Quick"), desc: __("The kit's headline inputs, the verdict and the budget.") },
						{ value: "full", label: __("Full"), desc: __("Every section, equipment picks and outputs.") },
					],
					model.mode,
					__("Mode")
				)}
				<p class="fd-muted">${esc(design.name ? __("Saved as {0}", [design.name]) : "")}</p>`,
		})}
		${card(h, { id: "links", title: __("Links"), simple: `<div class="fd-grid">${links}</div>` })}
		${full ? card(h, { id: "notes", title: __("Notes"), simple: field(h, { path: "notes", label: "Notes for the design team", kind: "textarea", value: model.notes || "" }) }) : ""}
		<p><button type="button" class="fd-link" data-fd-action="all-designs">${esc(__("All designs"))}</button></p>`;
}
