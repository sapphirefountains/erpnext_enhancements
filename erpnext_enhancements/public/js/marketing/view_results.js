/**
 * What happened to a post on each network: the job's state, its link, every attempt from the
 * outbox's attempt log, and the engagement figures once they are collected.
 *
 * An **Unconfirmed** job is the one to act on: the request left ERPNext and no answer came back,
 * so the post may be live. That is resolved from the job's own form in the Desk
 * (`sweeper.resolve_job`), by someone who has looked at the network; this page says so rather
 * than offering a button that would answer without looking.
 */

import { call, M } from "./transport.js";
import { VIEW_POST, buildRoute } from "./routes.js";
import { whenLabel } from "./calendar.js";
import { append, el, fill, link, networkTag, outLink, statePill, statusPill } from "./dom.js";

const FIGURES = [
	["impressions", "Impressions"],
	["reach", "Reach"],
	["engagements", "Engagements"],
	["clicks", "Clicks"],
	["video_views", "Video views"],
];

export async function renderResults(app, route) {
	app.showPlaceholder(app.pane, "◷", "Loading results…");
	const data = await call(M.RESULTS, { name: route.name });
	const toolbar = el("div", "ee-mk-toolbar");
	append(
		toolbar,
		link("‹ Post", buildRoute(VIEW_POST, data.name), (h) => app.navigate(h), "ee-mk-btn ee-mk-btn-small"),
		el("h2", "ee-mk-toolbar-title", data.title),
		statusPill(data.status)
	);
	const list = el("div", "ee-mk-list");
	fill(app.pane, toolbar, list);
	renderJobs(app, list, data.jobs || []);
}

function renderJobs(app, list, jobs) {
	if (jobs.length === 0) {
		app.showPlaceholder(list, "◷", "Not queued yet", "Results show here once the post is approved and queued for its accounts.");
		return;
	}
	fill(list);
	for (const job of jobs) list.appendChild(jobCard(job));
}

function jobCard(job) {
	const card = el("article", "ee-mk-job");
	const head = el("div", "ee-mk-job-head");
	append(head, networkTag(job.network), el("span", "ee-mk-job-account", job.account), statePill(job.state));
	card.appendChild(head);

	const facts = [];
	if (job.published_at) facts.push(`Published ${whenLabel(job.published_at)}`);
	else if (job.state === "Pending" && job.available_at) facts.push(`Goes out ${whenLabel(job.available_at)} at the earliest`);
	if (job.attempts) facts.push(`${job.attempts} attempt${job.attempts === 1 ? "" : "s"}`);
	append(
		card,
		el("div", "ee-mk-row-meta", facts.join(" · ")),
		job.permalink ? outLink(`Open on ${job.network}`, job.permalink) : null,
		job.last_error ? el("div", job.state === "Published" ? "ee-mk-muted" : "ee-mk-problem", job.last_error) : null
	);
	if (job.state === "Unconfirmed") {
		card.appendChild(
			el(
				"div",
				"ee-mk-locked-note",
				`This may already be live on ${job.network}. Check the account there first, then record what you found on the job's form in the Desk.`
			)
		);
	}

	const totals = (job.metrics && job.metrics.totals) || {};
	const tiles = el("div", "ee-mk-tiles");
	for (const [key, label] of FIGURES) {
		const value = totals[key];
		append(tiles, append(el("div", "ee-mk-figure"), el("span", "ee-mk-figure-num", value === null || value === undefined ? "—" : String(value)), el("span", "ee-mk-muted", label)));
	}
	card.appendChild(tiles);
	if (!(job.metrics && job.metrics.days && job.metrics.days.length)) {
		card.appendChild(el("div", "ee-mk-muted", "Engagement figures appear here once they are collected from the network."));
	}

	if (job.log && job.log.length) {
		const table = el("table", "ee-mk-table");
		const headRow = el("tr");
		for (const label of ["When", "Try", "Outcome", "HTTP", "Sent", "By", "Detail"]) headRow.appendChild(el("th", null, label));
		append(table, append(el("thead"), headRow));
		const body = el("tbody");
		for (const entry of job.log) {
			const row = el("tr");
			for (const value of [
				whenLabel(entry.at),
				entry.attempt ? String(entry.attempt) : "",
				entry.outcome,
				entry.http_status ? String(entry.http_status) : "",
				entry.sent ? "Yes" : "No",
				entry.by || "The outbox",
				entry.message,
			]) {
				row.appendChild(el("td", null, value));
			}
			body.appendChild(row);
		}
		table.appendChild(body);
		append(card, el("h3", "ee-mk-section-title", "Attempts"), append(el("div", "ee-mk-table-wrap"), table));
	}
	return card;
}
