/**
 * The approval queue: every post waiting for approval, oldest first.
 *
 * Approving happens on the post's own page, never here: what is approved must be what was
 * reviewed, and a list row is not a review. The queue says, for each post, whether the reader
 * could approve it and why not -- they wrote it, or made the latest change -- and offers the one
 * safe action inline: **Send back**, which returns the post to its author as a Draft.
 */

import { call, M } from "./transport.js";
import { VIEW_POST, buildRoute } from "./routes.js";
import { whenLabel } from "./calendar.js";
import { append, button, dialog, el, field, fill, link, networkTag, textarea } from "./dom.js";

export async function renderQueue(app) {
	const toolbar = append(el("div", "ee-mk-toolbar"), el("h2", "ee-mk-toolbar-title", "Approval queue"));
	const list = el("div", "ee-mk-list");
	fill(app.pane, toolbar, list);
	app.showPlaceholder(list, "◷", "Loading the queue…");
	let data;
	try {
		data = await call(M.QUEUE);
	} catch (e) {
		app.showPlaceholder(list, "!", "The queue could not load", e.message);
		return;
	}
	renderQueueList(app, list, data.posts || []);
}

function renderQueueList(app, list, posts) {
	if (posts.length === 0) {
		app.showPlaceholder(list, "✓", "Nothing is waiting for approval", "Posts submitted for approval show here, oldest first.");
		return;
	}
	fill(list);
	for (const post of posts) {
		const row = el("article", "ee-mk-row ee-mk-queue-row");
		const main = el("div", "ee-mk-row-main");
		append(
			main,
			link(post.title, buildRoute(VIEW_POST, post.name), (h) => app.navigate(h), "ee-mk-row-title"),
			el(
				"div",
				"ee-mk-row-meta",
				[
					`by ${post.author || "someone"}`,
					post.scheduled_at ? `for ${whenLabel(post.scheduled_at)}` : "to go out as soon as it is approved",
					post.problems ? `${post.problems} network problem${post.problems === 1 ? "" : "s"}` : null,
				]
					.filter(Boolean)
					.join(" · ")
			),
			append(el("div", "ee-mk-row-nets"), ...post.networks.map(networkTag))
		);
		const side = el("div", "ee-mk-row-actions");
		const blockers = post.approve_problems || [];
		append(
			side,
			blockers.length
				? el("span", "ee-mk-muted", youCannot(blockers))
				: el("span", "ee-mk-ready", "✓ You can approve this"),
			link("Review", buildRoute(VIEW_POST, post.name), (h) => app.navigate(h), "ee-mk-btn ee-mk-btn-primary"),
			button("Send back", "ee-mk-btn", () => sendBack(app, post))
		);
		append(row, main, side);
		list.appendChild(row);
	}
}

function youCannot(problems) {
	if (problems.some((p) => p.includes("you wrote it"))) return "You wrote this";
	if (problems.some((p) => p.includes("latest change"))) return "You made the latest change";
	if (problems.some((p) => p.includes("Marketing Manager"))) return "Needs a Marketing Manager";
	return problems[0];
}

function sendBack(app, post) {
	const reason = textarea("", 3);
	dialog(`Send “${post.title}” back to Draft?`, [field("What should change?", reason).row], [
		{ label: "Back" },
		{
			label: "Send back",
			primary: true,
			onClick: async () => {
				try {
					await call(M.SEND_BACK, { post: post.name, reason: reason.value });
					app.say("Sent back to Draft.", "ok");
					app.refreshBootstrap();
					renderQueue(app);
				} catch (e) {
					app.fail(e);
				}
			},
		},
	]);
}
