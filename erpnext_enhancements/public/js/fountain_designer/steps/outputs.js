// Outputs. The bill of materials and the budget, the pushes to a Quotation and a Material Request, the
// schedules and the packet (printed by phase G), and the O&M, winterization and start-up summaries as
// drawers (disclosures in the page, so opening one needs no history entry and Back still leaves the step).

import { stage_head } from "../ui.js";
import { format_value } from "../verdict.js";

export const key = "outputs";

export function render(ctx) {
	const { h, reply } = ctx;
	const { esc, __ } = h;
	const head = stage_head(h, __("Outputs"), __("The budget, the bill of materials and the papers that go with them."));
	if (!reply) return head + h.state_html("loading", { title: __("Working out the design…") });
	const bom = Array.isArray(reply.bom) ? reply.bom : [];
	const total = reply.rollups && reply.rollups.budget_total;
	const money = (n) => format_value(n, { kind: "money" });
	const groups = {};
	for (const line of bom) (groups[line.section || "Other"] = groups[line.section || "Other"] || []).push(line);
	const body = Object.entries(groups)
		.map(
			([section, lines]) => `<tbody>
			<tr class="fd-table-group"><th scope="rowgroup" colspan="5">${esc(__(section))}</th></tr>
			${lines
				.map(
					(l) => `<tr>
				<td>${esc(l.description || l.item || "")}${l.item ? `<span class="fd-muted"> ${esc(l.item)}</span>` : ""}${
						l.source === "Manual" ? " " + h.chip(__("Manual"), "neutral") : ""
					}</td>
				<td class="fd-num">${esc(format_value(l.qty, { digits: Number.isInteger(l.qty) ? 0 : 1 }))}</td>
				<td>${esc(l.uom || "")}</td>
				<td class="fd-num">${esc(money(l.rate))}</td>
				<td class="fd-num">${esc(money(l.amount))}</td>
			</tr>`
				)
				.join("")}
		</tbody>`
		)
		.join("");
	const table = bom.length
		? `<div class="fd-table-wrap"><table class="fd-table">
			<caption class="fd-visually-hidden">${esc(__("Bill of materials"))}</caption>
			<thead><tr><th scope="col">${esc(__("Line"))}</th><th scope="col" class="fd-num">${esc(__("Qty"))}</th><th scope="col">${esc(__("Unit"))}</th>
			<th scope="col" class="fd-num">${esc(__("Rate"))}</th><th scope="col" class="fd-num">${esc(__("Amount"))}</th></tr></thead>
			${body}
			<tfoot><tr><th scope="row" colspan="4">${esc(__("Budget"))}</th><td class="fd-num"><strong>${esc(money(total))}</strong></td></tr></tfoot>
		</table></div>`
		: h.state_html("empty", { title: __("No bill of materials yet."), body: __("It appears once the design is worked out.") });
	const saved = Boolean(ctx.design && ctx.design.name);
	const can = ctx.can || {};
	const push = `<div class="fd-actions">
		<button type="button" class="sf-btn sf-btn--primary" data-fd-action="push-quotation"${saved && can.push_quotation && bom.length && !ctx.busy ? "" : " disabled"}>${esc(__("Push to Quotation"))}</button>
		<button type="button" class="sf-btn sf-btn--secondary" data-fd-action="push-mr"${saved && can.push_material_request && bom.length && !ctx.busy ? "" : " disabled"}>${esc(__("Push to Material Request"))}</button>
	</div>
	<p class="fd-hint">${esc(__("A line with an Item becomes an item line; any other becomes a text line."))}</p>
	${ctx.last_push ? `<p class="fd-push-result" role="status">${ctx.last_push}</p>` : ""}`;
	const out = reply.outputs || {};
	const papers = (out.schedules || []).concat(out.packet ? [Object.assign({ key: "packet" }, out.packet)] : []);
	const papers_html = papers.length
		? `<ul class="fd-list">${papers
				.map((p) => {
					const url = safe_desk_url(p.url);
					return `<li>${url ? `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(__(p.label))}</a>` : `${esc(__(p.label))} <span class="fd-muted">${esc(__("(not available yet)"))}</span>`}</li>`;
				})
				.join("")}</ul>`
		: `<p class="fd-muted">${esc(__("No schedules yet."))}</p>`;
	const om = out.om;
	const om_html = om
		? `<dl class="fd-rollups">${(om.lines || [])
				.map((l) => `<div class="fd-rollup"><dt>${esc(l.label)}</dt><dd>${esc(money(l.amount))}</dd></div>`)
				.join("")}<div class="fd-rollup fd-rollup--total"><dt>${esc(__("Per year"))}</dt><dd>${esc(money(om.total_per_year))}</dd></div></dl>`
		: `<p class="fd-muted">${esc(__("Not worked out yet."))}</p>`;
	const winter = out.winterization;
	const winter_html =
		winter && (winter.steps || []).length
			? `<ol class="fd-list">${winter.steps.map((s) => `<li>${esc(s)}</li>`).join("")}</ol>`
			: `<p class="fd-muted">${esc(__("Not worked out yet."))}</p>`;
	const start = Array.isArray(out.startup) && out.startup.length ? `<ol class="fd-list">${out.startup.map((s) => `<li>${esc(s)}</li>`).join("")}</ol>` : "";
	const drawer = (id, title, html) => `<details class="fd-drawer" data-fd-drawer="${id}"><summary>${esc(title)}</summary><div class="fd-drawer-body">${html}</div></details>`;
	return `${head}
		<section class="fd-card"><header class="fd-card-head"><h3 class="fd-card-title">${esc(__("Budget"))}</h3>
			<p class="fd-budget">${esc(money(total) || "—")}</p></header>
			${push}
			${ctx.full ? table : drawer("bom", __("Bill of materials"), table)}
		</section>
		<section class="fd-card"><header class="fd-card-head"><h3 class="fd-card-title">${esc(__("Schedules and packet"))}</h3></header>${papers_html}</section>
		${drawer("om", __("O&M per year: {0}", [money(om && om.total_per_year) || "—"]), om_html)}
		${drawer("winterization", winter && winter.needed === false ? __("Winterization: not needed") : __("Winterization"), winter_html)}
		${start ? drawer("startup", __("Start-up checklist"), start) : ""}`;
}

// A print link only to a desk route or the print view on this site; anything else is shown as text.
export function safe_desk_url(url) {
	if (typeof url !== "string") return "";
	return /^\/(desk|app|printview|api\/method)\b[^\s"'<>]*$/.test(url) ? url : "";
}
