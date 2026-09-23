/**
 * The composer: write a post, choose where it goes and when, see each network's version, and
 * move it through approval. A post that is no longer editable opens here read-only.
 *
 * Three things it is careful about:
 *
 *   - **What is approved is what was reviewed.** Approve sends the `modified` this page loaded
 *     (`workflow.approval_problems`), and the button is off while the page holds unsaved changes:
 *     an approver who edits becomes the last editor, and somebody else must approve.
 *   - **The server decides what a network would refuse.** Counters count against the limits
 *     the server's checks use, but the list of problems comes from `spa.check_post`, which runs
 *     the outbox's own checks. A post with any is refused at submit and again at approval.
 *   - **Unsaved work is not lost to a stray click.** `app.leaveGuard` asks before navigating
 *     away, and the browser asks before closing the tab.
 */

import { call, M } from "./transport.js";
import { VIEW_MONTH, VIEW_NEW, VIEW_POST, VIEW_RESULTS, buildRoute } from "./routes.js";
import { datePart, timePart, whenLabel } from "./calendar.js";
import {
	YOUTUBE,
	counters,
	isDirty,
	isOver,
	moved,
	networksOf,
	payloadOf,
	previewOf,
	textFor,
} from "./composer.js";
import {
	append,
	assetThumb,
	button,
	checkbox,
	clear,
	dialog,
	el,
	field,
	fill,
	input,
	link,
	networkTag,
	statusPill,
	textarea,
} from "./dom.js";
import { openPicker } from "./view_media.js";

const CHECK_DELAY_MS = 700;

function emptyState(day) {
	return {
		title: "",
		body: "",
		link: "",
		link_title: "",
		link_description: "",
		day: day || "",
		time: day ? "09:00" : "",
		video_title: "",
		video_tags: "",
		thumbnail: null,
		youtube_playlist_id: "",
		targets: [],
		media: [],
	};
}

function stateFrom(post) {
	return {
		title: post.title || "",
		body: post.body || "",
		link: post.link || "",
		link_title: post.link_title || "",
		link_description: post.link_description || "",
		day: datePart(post.scheduled_at),
		time: timePart(post.scheduled_at),
		video_title: post.video_title || "",
		video_tags: post.video_tags || "",
		thumbnail: post.thumbnail || null,
		youtube_playlist_id: post.youtube_playlist_id || "",
		targets: (post.targets || []).map((t) => ({
			social_account: t.social_account,
			network: t.network,
			label: t.label,
			variant_text: t.variant_text || "",
			first_comment: t.first_comment || "",
		})),
		media: (post.media || []).slice(),
	};
}

export async function renderComposer(app, route) {
	let saved = null;
	if (route.view === VIEW_POST) {
		app.showPlaceholder(app.pane, "◷", "Loading the post…");
		saved = await call(M.GET_POST, { name: route.name });
	}
	const seed = route.view === VIEW_NEW ? app.takeSeed() : null;
	const baseline = saved ? stateFrom(saved) : emptyState(route.day);
	const state = saved ? stateFrom(saved) : seed || emptyState(route.day);
	const actions = saved
		? saved.actions
		: { can_edit: true, can_submit: true, can_approve: false, approve_problems: [], locked: false };
	const editable = !!actions.can_edit;
	app.leaveGuard = () => editable && isDirty(state, baseline);

	const ctx = { app, saved, state, baseline, actions, editable, els: {}, checkSeq: 0, checkTimer: null };

	const form = el("div", "ee-mk-form");
	const side = el("aside", "ee-mk-side");
	ctx.els.checks = el("section", "ee-mk-section ee-mk-checks");
	ctx.els.previews = el("section", "ee-mk-section ee-mk-previews");
	append(side, ctx.els.checks, ctx.els.previews);

	append(
		form,
		titleSection(ctx),
		(ctx.els.accounts = el("section", "ee-mk-section")),
		textSection(ctx),
		linkSection(ctx),
		(ctx.els.media = el("section", "ee-mk-section")),
		(ctx.els.youtube = el("section", "ee-mk-section")),
		whenSection(ctx)
	);
	drawAccounts(ctx);
	drawMedia(ctx);
	drawYouTube(ctx);
	if (!editable) disableAll(form);

	fill(app.pane, header(ctx), lockedNote(ctx), append(el("div", "ee-mk-composer"), form, side), actionBar(ctx));
	refresh(ctx, true);
	runCheck(ctx);
}

// ------------------------------------------------------------------ header

function header(ctx) {
	const { saved, app } = ctx;
	const bar = el("div", "ee-mk-toolbar");
	append(
		bar,
		link("‹ Calendar", buildRoute(VIEW_MONTH), (h) => app.navigate(h), "ee-mk-btn ee-mk-btn-small"),
		el("h2", "ee-mk-toolbar-title", saved ? saved.title : "New post"),
		saved ? statusPill(saved.status) : null
	);
	if (saved) {
		const meta = [];
		if (saved.author) meta.push(`Written by ${saved.author}`);
		if (saved.last_edited_by && saved.last_edited_by !== saved.author) meta.push(`last edited by ${saved.last_edited_by}`);
		if (saved.approver) meta.push(`approved by ${saved.approver}${saved.approved_at ? `, ${whenLabel(saved.approved_at)}` : ""}`);
		append(bar, el("span", "ee-mk-meta-line", meta.join(" · ")));
		if (ctx.actions.locked) {
			append(bar, link("Results", buildRoute(VIEW_RESULTS, saved.name), (h) => app.navigate(h), "ee-mk-btn ee-mk-btn-small"));
		}
	}
	return bar;
}

function lockedNote(ctx) {
	if (ctx.editable || !ctx.saved) return null;
	const note = el("div", "ee-mk-locked-note");
	note.setAttribute("role", "note");
	const status = ctx.saved.status;
	const text = ctx.actions.locked
		? `This post is ${status}. What it says, where it goes and when are locked to what was approved. To change it, cancel it and copy it into a new draft.`
		: "You can read this post but not change it.";
	return append(note, el("span", null, text));
}

// ------------------------------------------------------------------ form sections

function section(title, ...children) {
	return append(el("section", "ee-mk-section"), el("h3", "ee-mk-section-title", title), ...children);
}

function titleSection(ctx) {
	const box = input("text", ctx.state.title, "Only for finding it here; no network shows it");
	box.maxLength = 140;
	box.addEventListener("input", () => {
		ctx.state.title = box.value;
		refresh(ctx, true);
	});
	return append(el("section", "ee-mk-section"), field("Title", box, "Leave it empty to use the first line of the text.").row);
}

function drawAccounts(ctx) {
	const { app, state } = ctx;
	const host = ctx.els.accounts;
	const list = el("div", "ee-mk-accounts");
	fill(host, el("h3", "ee-mk-section-title", "Where it goes"), list);
	const enabled = (app.data.accounts || []).filter((a) => a.enabled);
	const chosen = new Set(state.targets.map((t) => t.social_account));
	// A saved post can name an account that has since been switched off; it stays listed so it
	// can be removed, and the checks say why it cannot go out.
	const listed = enabled.concat(
		state.targets
			.filter((t) => !enabled.some((a) => a.name === t.social_account))
			.map((t) => ({ name: t.social_account, network: t.network, label: t.label, enabled: false }))
	);
	if (listed.length === 0) {
		app.showPlaceholder(
			list,
			"∅",
			"No accounts connected yet",
			"A System Manager connects Facebook, Instagram, LinkedIn and YouTube in Marketing Connections."
		);
		return;
	}
	for (const account of listed) {
		const row = el("div", "ee-mk-account");
		const box = checkbox(chosen.has(account.name));
		box.disabled = !ctx.editable;
		const label = el("label", "ee-mk-account-label");
		append(
			label,
			box,
			networkTag(account.network),
			el("span", null, account.label || account.name),
			account.enabled ? null : el("span", "ee-mk-muted", "(switched off)")
		);
		row.appendChild(label);
		box.addEventListener("change", () => {
			if (box.checked) {
				state.targets.push({
					social_account: account.name,
					network: account.network,
					label: account.label,
					variant_text: "",
					first_comment: "",
				});
			} else {
				state.targets = state.targets.filter((t) => t.social_account !== account.name);
			}
			drawAccounts(ctx);
			drawYouTube(ctx);
			refresh(ctx);
		});
		const target = state.targets.find((t) => t.social_account === account.name);
		if (target) row.appendChild(targetDetails(ctx, target));
		list.appendChild(row);
	}
}

function targetDetails(ctx, target) {
	const wrap = el("div", "ee-mk-account-details");
	const variant = textarea(target.variant_text, 3, "Leave empty to use the post text");
	variant.disabled = !ctx.editable;
	variant.addEventListener("input", () => {
		target.variant_text = variant.value;
		refresh(ctx);
	});
	wrap.appendChild(field(`Text for ${target.label || target.network} only`, variant).row);
	if (target.network !== YOUTUBE) {
		const comment = input("text", target.first_comment, "Posted as the first comment, after the post");
		comment.disabled = !ctx.editable;
		comment.addEventListener("input", () => {
			target.first_comment = comment.value;
			refresh(ctx);
		});
		wrap.appendChild(field("First comment", comment).row);
	}
	return wrap;
}

function textSection(ctx) {
	const body = textarea(ctx.state.body, 7, "What the post says");
	body.addEventListener("input", () => {
		ctx.state.body = body.value;
		refresh(ctx);
	});
	ctx.els.counters = el("div", "ee-mk-counters");
	ctx.els.counters.setAttribute("aria-live", "polite");
	return append(el("section", "ee-mk-section"), field("Text", body).row, ctx.els.counters);
}

function linkSection(ctx) {
	const url = input("url", ctx.state.link, "https://…");
	const title = input("text", ctx.state.link_title);
	const description = textarea(ctx.state.link_description, 2);
	const bind = (node, key) =>
		node.addEventListener("input", () => {
			ctx.state[key] = node.value;
			refresh(ctx);
		});
	bind(url, "link");
	bind(title, "link_title");
	bind(description, "link_description");
	return section(
		"Link",
		field("Address", url).row,
		field("Link title", title, "LinkedIn shows this on the link card when the post has no photos.").row,
		field("Link description", description).row
	);
}

function drawMedia(ctx) {
	const { app, state } = ctx;
	const list = el("div", "ee-mk-media-list");
	const host = ctx.els.media;
	fill(host, el("h3", "ee-mk-section-title", "Photos and video"), list);
	if (state.media.length === 0) {
		app.showPlaceholder(list, "🖼", "No media", "Instagram and YouTube need some; Facebook and LinkedIn can go without.");
	}
	state.media.forEach((asset, index) => {
		const row = el("div", "ee-mk-media-item");
		append(
			row,
			assetThumb(asset),
			append(el("div", "ee-mk-media-main"), el("div", "ee-mk-media-title", asset.title), el("div", "ee-mk-muted", `${asset.asset_type} · ${asset.usage_rights}`))
		);
		if (ctx.editable) {
			const up = button("↑", "ee-mk-btn ee-mk-btn-small", () => {
				state.media = moved(state.media, index, -1);
				drawMedia(ctx);
				refresh(ctx);
			});
			up.setAttribute("aria-label", `Move ${asset.title} earlier`);
			up.disabled = index === 0;
			const down = button("↓", "ee-mk-btn ee-mk-btn-small", () => {
				state.media = moved(state.media, index, 1);
				drawMedia(ctx);
				refresh(ctx);
			});
			down.setAttribute("aria-label", `Move ${asset.title} later`);
			down.disabled = index === state.media.length - 1;
			const remove = button("Remove", "ee-mk-btn ee-mk-btn-small", () => {
				state.media = state.media.filter((_, i) => i !== index);
				drawMedia(ctx);
				refresh(ctx);
			});
			append(row, up, down, remove);
		}
		list.appendChild(row);
	});
	if (ctx.editable) {
		host.appendChild(
			button("Add photos or video", "ee-mk-btn", () =>
				openPicker(app, {
					title: "Add photos or video",
					multiple: true,
					exclude: state.media.map((a) => a.name),
					onPick: (assets) => {
						state.media = state.media.concat(assets);
						drawMedia(ctx);
						refresh(ctx);
					},
				})
			)
		);
	}
}

function drawYouTube(ctx) {
	const host = ctx.els.youtube;
	const { state, app } = ctx;
	if (!networksOf(state).includes(YOUTUBE)) {
		host.hidden = true;
		clear(host);
		return;
	}
	host.hidden = false;
	const title = input("text", state.video_title);
	title.maxLength = 100;
	const tags = input("text", state.video_tags, "fountains, water feature");
	const playlist = input("text", state.youtube_playlist_id, "PL…");
	const bind = (node, key) =>
		node.addEventListener("input", () => {
			state[key] = node.value;
			refresh(ctx);
		});
	bind(title, "video_title");
	bind(tags, "video_tags");
	bind(playlist, "youtube_playlist_id");
	const thumb = el("div", "ee-mk-thumb-pick");
	const drawThumb = () => {
		clear(thumb);
		if (state.thumbnail) {
			append(thumb, assetThumb(state.thumbnail), el("span", null, state.thumbnail.title));
			if (ctx.editable) {
				thumb.appendChild(
					button("Remove", "ee-mk-btn ee-mk-btn-small", () => {
						state.thumbnail = null;
						drawThumb();
						refresh(ctx);
					})
				);
			}
		} else {
			thumb.appendChild(el("span", "ee-mk-muted", "No custom thumbnail"));
		}
		if (ctx.editable) {
			thumb.appendChild(
				button("Choose…", "ee-mk-btn ee-mk-btn-small", () =>
					openPicker(app, {
						title: "Choose a thumbnail",
						multiple: false,
						assetType: "Image",
						onPick: (assets) => {
							state.thumbnail = assets[0] || null;
							drawThumb();
							refresh(ctx);
						},
					})
				)
			);
		}
	};
	drawThumb();
	fill(
		host,
		el("h3", "ee-mk-section-title", "YouTube"),
		field("Video title", title, "The video's public title, up to 100 characters.").row,
		field("Tags", tags, "Separated by commas.").row,
		append(el("div", "ee-mk-field"), el("span", "ee-mk-field-label", "Thumbnail"), thumb),
		field("Playlist ID", playlist, "From the playlist's address, after list=.").row
	);
	if (!ctx.editable) disableAll(host);
}

function whenSection(ctx) {
	const { state, app } = ctx;
	const day = input("date", state.day);
	const time = input("time", state.time || "");
	const clearBtn = button("Publish as soon as approved", "ee-mk-btn ee-mk-btn-small", () => {
		day.value = "";
		time.value = "";
		state.day = "";
		state.time = "";
		refresh(ctx);
	});
	day.min = app.data.today;
	day.addEventListener("input", () => {
		state.day = day.value;
		if (day.value && !time.value) {
			time.value = "09:00";
			state.time = "09:00";
		}
		refresh(ctx);
	});
	time.addEventListener("input", () => {
		state.time = time.value;
		refresh(ctx);
	});
	return section(
		"When",
		append(el("div", "ee-mk-when"), field("Date", day).row, field("Time", time).row, clearBtn),
		el(
			"p",
			"ee-mk-field-help",
			`Times are in ${app.data.timezone || "the site's time zone"}. With no date, the post goes out as soon as it is approved.`
		)
	);
}

// ------------------------------------------------------------------ live parts

/** Redraw what depends on the text, and ask the server again shortly. */
function refresh(ctx, skipCheck) {
	drawCounters(ctx);
	drawPreviews(ctx);
	if (skipCheck) return;
	clearTimeout(ctx.checkTimer);
	ctx.checkTimer = setTimeout(() => runCheck(ctx), CHECK_DELAY_MS);
}

function drawCounters(ctx) {
	const { state, app } = ctx;
	const host = ctx.els.counters;
	clear(host);
	for (const target of state.targets) {
		const row = el("div", "ee-mk-counter");
		append(row, networkTag(target.network), el("span", "ee-mk-muted", target.label || ""));
		for (const counter of counters(target.network, textFor(state, target), app.data.limits)) {
			const over = isOver(counter);
			const text = counter.max ? `${counter.value} / ${counter.max} ${counter.label}` : `${counter.value} ${counter.label}`;
			append(row, el("span", over ? "ee-mk-count ee-mk-count-over" : "ee-mk-count", over ? `${text} — too long` : text));
		}
		host.appendChild(row);
	}
}

function drawPreviews(ctx) {
	const { state, app } = ctx;
	const host = ctx.els.previews;
	const list = el("div", "ee-mk-preview-list");
	fill(host, el("h3", "ee-mk-section-title", "How it will look"), list);
	if (state.targets.length === 0) {
		app.showPlaceholder(list, "👀", "Choose an account", "Each account's version of the post shows here.");
		return;
	}
	for (const target of state.targets) list.appendChild(previewCard(previewOf(state, target)));
}

function previewCard(preview) {
	const card = el("article", `ee-mk-preview ee-mk-preview-${String(preview.network || "").toLowerCase()}`);
	append(card, append(el("div", "ee-mk-preview-head"), networkTag(preview.network), el("span", null, preview.account)));
	if (preview.title) card.appendChild(el("div", "ee-mk-preview-title", preview.title));
	if (preview.media.length) {
		const strip = el("div", "ee-mk-preview-media");
		for (const asset of preview.media.slice(0, 4)) strip.appendChild(assetThumb(asset, "ee-mk-thumb ee-mk-thumb-large"));
		if (preview.media.length > 4) strip.appendChild(el("span", "ee-mk-muted", `+${preview.media.length - 4}`));
		card.appendChild(strip);
	}
	card.appendChild(el("div", "ee-mk-preview-text", preview.text || "(no text)"));
	if (preview.card) {
		const box = el("div", "ee-mk-preview-card");
		append(
			box,
			preview.card.title ? el("div", "ee-mk-preview-card-title", preview.card.title) : null,
			preview.card.description ? el("div", "ee-mk-muted", preview.card.description) : null,
			el("div", "ee-mk-preview-card-url", preview.card.url)
		);
		card.appendChild(box);
	}
	if (preview.first_comment) card.appendChild(el("div", "ee-mk-preview-comment", `First comment: ${preview.first_comment}`));
	for (const note of preview.notes) card.appendChild(el("div", "ee-mk-preview-note", note));
	return card;
}

async function runCheck(ctx) {
	const seq = (ctx.checkSeq += 1);
	const host = ctx.els.checks;
	const heading = el("h3", "ee-mk-section-title", "What the networks would refuse");
	const list = el("div", "ee-mk-problems");
	fill(host, heading, list);
	if (!ctx.editable && ctx.saved) {
		// A locked post was checked before it was approved; show what was recorded then.
		drawProblems(ctx, list, { general: ctx.saved.network_check, by_network: {} });
		return;
	}
	ctx.app.showPlaceholder(list, "◷", "Checking…");
	let result;
	try {
		result = await call(M.CHECK_POST, { values: payloadOf(ctx.state) });
	} catch (e) {
		if (seq === ctx.checkSeq) ctx.app.showPlaceholder(list, "!", "Could not check", e.message);
		return;
	}
	if (seq !== ctx.checkSeq) return; // a newer check is on its way
	drawProblems(ctx, list, result);
}

function drawProblems(ctx, list, result) {
	const general = result.general || [];
	const byNetwork = result.by_network || {};
	const networks = Object.keys(byNetwork).filter((n) => byNetwork[n].length);
	if (general.length === 0 && networks.length === 0) {
		ctx.app.showPlaceholder(list, "✓", "Nothing here would be refused", "The networks' own checks pass.");
		return;
	}
	clear(list);
	for (const problem of general) list.appendChild(el("div", "ee-mk-problem", problem));
	for (const network of networks) {
		const group = append(el("div", "ee-mk-problem-group"), networkTag(network));
		for (const problem of byNetwork[network]) group.appendChild(el("div", "ee-mk-problem", problem));
		list.appendChild(group);
	}
}

// ------------------------------------------------------------------ actions

function actionBar(ctx) {
	const { saved, actions, editable, app } = ctx;
	const bar = el("div", "ee-mk-actions");
	const status = saved ? saved.status : "Draft";

	if (editable) {
		bar.appendChild(button(saved ? "Save" : "Save draft", "ee-mk-btn", () => run(ctx, () => save(ctx))));
	}
	if (!saved || actions.can_submit) {
		bar.appendChild(button("Submit for approval", "ee-mk-btn ee-mk-btn-primary", () => run(ctx, () => submit(ctx))));
	}
	if (saved && status === "Pending Approval") {
		if (actions.can_approve) {
			bar.appendChild(button("Approve", "ee-mk-btn ee-mk-btn-primary", () => approve(ctx)));
		} else if ((actions.approve_problems || []).length) {
			bar.appendChild(el("span", "ee-mk-muted", `You cannot approve this: ${actions.approve_problems.join("; ")}.`));
		}
		if (actions.can_send_back) {
			bar.appendChild(
				button("Send back", "ee-mk-btn", () =>
					withReason(ctx, "Send back to Draft", "What should change?", "Send back", M.SEND_BACK, "Sent back to Draft.")
				)
			);
		}
	}
	if (saved && actions.locked) {
		bar.appendChild(
			button("Copy into a new draft", "ee-mk-btn", () => {
				const copy = stateFrom(saved);
				copy.day = "";
				copy.time = "";
				app.seed = copy;
				app.navigate(buildRoute(VIEW_NEW));
			})
		);
	}
	if (saved && actions.can_cancel) {
		bar.appendChild(
			button("Cancel post", "ee-mk-btn ee-mk-btn-danger", () =>
				withReason(
					ctx,
					"Cancel this post",
					actions.locked ? "Why? Canceling stops what has not gone out; it cannot take down anything already published." : "Why? (optional)",
					"Cancel the post",
					M.CANCEL,
					"Canceled."
				)
			)
		);
	}
	if (saved && actions.can_delete) bar.appendChild(button("Delete", "ee-mk-btn ee-mk-btn-danger", () => remove(ctx)));
	return bar;
}

/** Run an action with the page's buttons held, and say what went wrong if it did. */
async function run(ctx, action) {
	const buttons = Array.from(ctx.app.pane.querySelectorAll(".ee-mk-actions button"));
	buttons.forEach((b) => (b.disabled = true));
	try {
		await action();
	} catch (e) {
		ctx.app.fail(e);
	} finally {
		buttons.forEach((b) => (b.disabled = false));
	}
}

async function save(ctx) {
	const result = await call(M.SAVE_POST, {
		values: payloadOf(ctx.state),
		name: ctx.saved ? ctx.saved.name : null,
		modified: ctx.saved ? ctx.saved.modified : null,
	});
	ctx.app.leaveGuard = null;
	ctx.app.navigate(buildRoute(VIEW_POST, result.name), true);
	ctx.app.say("Saved.", "ok");
	return result;
}

async function submit(ctx) {
	let name = ctx.saved ? ctx.saved.name : null;
	if (!ctx.saved || isDirty(ctx.state, ctx.baseline)) {
		const result = await call(M.SAVE_POST, {
			values: payloadOf(ctx.state),
			name,
			modified: ctx.saved ? ctx.saved.modified : null,
		});
		name = result.name;
	}
	await call(M.SUBMIT, { post: name });
	ctx.app.leaveGuard = null;
	ctx.app.navigate(buildRoute(VIEW_POST, name), true);
	ctx.app.say("Submitted for approval.", "ok");
	ctx.app.refreshBootstrap();
}

function approve(ctx) {
	const { saved, app } = ctx;
	if (isDirty(ctx.state, ctx.baseline)) {
		app.say("You have changed this post. Save it and ask someone else to approve it, or reload to discard your changes.", "error");
		return;
	}
	const when = saved.scheduled_at ? whenLabel(saved.scheduled_at) : "as soon as it is approved";
	const where = saved.targets.map((t) => `${t.network}: ${t.label}`).join(", ");
	dialog("Approve this post?", [el("p", null, `It will be queued for ${where}, to go out ${when}.`), el("p", null, "Its text, media, accounts and time are then locked.")], [
		{ label: "Not yet" },
		{
			label: "Approve",
			primary: true,
			onClick: () =>
				run(ctx, async () => {
					await call(M.APPROVE, { post: saved.name, modified: saved.modified });
					app.navigate(buildRoute(VIEW_POST, saved.name), true);
					app.say("Approved and queued.", "ok");
					app.refreshBootstrap();
				}),
		},
	]);
}

function withReason(ctx, title, prompt, confirmLabel, method, done) {
	const reason = textarea("", 3);
	dialog(title, [field(prompt, reason).row], [
		{ label: "Back" },
		{
			label: confirmLabel,
			primary: true,
			onClick: () =>
				run(ctx, async () => {
					await call(method, { post: ctx.saved.name, reason: reason.value });
					ctx.app.leaveGuard = null;
					ctx.app.navigate(buildRoute(VIEW_POST, ctx.saved.name), true);
					ctx.app.say(done, "ok");
					ctx.app.refreshBootstrap();
				}),
		},
	]);
}

function remove(ctx) {
	dialog("Delete this post?", el("p", null, "It has never been queued, so nothing on any network changes. This cannot be undone."), [
		{ label: "Keep it" },
		{
			label: "Delete",
			primary: true,
			onClick: () =>
				run(ctx, async () => {
					await call(M.DELETE_POST, { name: ctx.saved.name });
					ctx.app.leaveGuard = null;
					ctx.app.navigate(buildRoute(VIEW_MONTH));
					ctx.app.say("Deleted.", "ok");
				}),
		},
	]);
}

function disableAll(node) {
	for (const control of node.querySelectorAll("input, textarea, select, button")) control.disabled = true;
}

