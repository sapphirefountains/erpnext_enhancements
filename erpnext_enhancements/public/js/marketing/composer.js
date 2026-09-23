/**
 * The composer's pure half: counters, the payload `save_post` takes, and the per-network
 * preview. No DOM and no fetch, so `scripts/test_marketing_client.js` can check it.
 *
 * **The preview mirrors the publishers, not the networks' marketing pages.** It shows what the
 * publisher in `marketing/publish/publishers/` actually sends:
 *
 *   - Facebook (`meta.py`): with photos or a video, the link is added to the end of the text;
 *     with neither, the text and the link go separately and Facebook draws the link card.
 *   - Instagram (`meta.py`): the caption is the text. **The link is not sent.**
 *   - LinkedIn (`linkedin.py`): with media, the link is added to the text; without, the post is
 *     an article card carrying our Link Title and Link Description.
 *   - YouTube (`youtube.py`): the text is the description, with the link added at the end; the
 *     Video Title is the title.
 *
 * And a link to our own site is shown **tagged**, as every publisher sends it (`tagged`, below,
 * the twin of `publish/tracking.py`). An unsaved draft has no name to tag with yet, and says so.
 *
 * It never guesses where a network folds "… more", because that moves with the device, and a
 * confident wrong preview is worse than a plain one. The counters count against the limits the
 * server's own checks use (`get_bootstrap().limits`), and the server's answer (`check_post`)
 * is the one the page trusts.
 */

export const FACEBOOK = "Facebook";
export const INSTAGRAM = "Instagram";
export const LINKEDIN = "LinkedIn";
export const YOUTUBE = "YouTube";

// ------------------------------------------------------------------ tracking tags
//
// The same rule as `marketing/publish/tracking.py` (TASK-2026-01488), so the preview shows the
// link each network is actually sent. `marketing/publish/tracking_vectors.json` holds both to the
// same answers: `scripts/test_marketing_client.js` runs this half, `test_marketing_metrics.py`
// the other. Change one, change both, and add the case to the vectors.

const OWN_HOSTS = ["sapphirefountains.com"];
const SOURCES = { Facebook: "facebook", Instagram: "instagram", LinkedIn: "linkedin", YouTube: "youtube" };
const TAGGED_NETWORKS = ["Facebook", "LinkedIn", "YouTube"];

export function slug(text) {
	const value = String(text || "")
		.toLowerCase()
		.replace(/[^a-z0-9]+/g, "-")
		.replace(/^-+|-+$/g, "");
	return value.slice(0, 60).replace(/^-+|-+$/g, "");
}

export function isOwn(link) {
	let url;
	try {
		url = new URL(String(link || "").trim());
	} catch (e) {
		return false;
	}
	const host = url.hostname.toLowerCase();
	if ((url.protocol !== "http:" && url.protocol !== "https:") || !host) return false;
	return OWN_HOSTS.some((own) => host === own || host.endsWith(`.${own}`));
}

function hasUtm(link) {
	const base = String(link || "").split("#")[0];
	const at = base.indexOf("?");
	const query = at === -1 ? "" : base.slice(at + 1);
	return query
		.split("&")
		.filter(Boolean)
		.some((part) => part.split("=")[0].toLowerCase().startsWith("utm_"));
}

function partition(text, separator) {
	const at = text.indexOf(separator);
	return at === -1 ? [text, "", ""] : [text.slice(0, at), separator, text.slice(at + separator.length)];
}

/** The link as it goes to `network`: tagged if it is ours and untagged, else unchanged. */
export function tagged(link, network, postName, campaign) {
	const text = String(link || "").trim();
	const source = SOURCES[network];
	const content = slug(postName);
	if (!text || !TAGGED_NETWORKS.includes(network) || !source || !content) return text;
	if (!isOwn(text) || hasUtm(text)) return text;
	const [base, hashMark, fragment] = partition(text, "#");
	const [path, , query] = partition(base, "?");
	const tags = `utm_source=${source}&utm_medium=social&utm_campaign=${slug(campaign) || "organic"}&utm_content=${content}`;
	return `${path}?${query ? `${query}&${tags}` : tags}${hashMark}${fragment}`;
}

/** Text carrying the link: the author's own copy swapped for the sent one, or the sent one added. */
export function withSentLink(text, link, sent) {
	const body = String(text || "");
	const original = String(link || "").trim();
	const out = String(sent || "").trim();
	if (!out) return body;
	if (original && body.includes(original)) return body.split(original).join(out);
	if (body.includes(out)) return body;
	return body ? `${body}\n\n${out}` : out;
}

/** Characters as a person counts them: an emoji is one, not the two UTF-16 units `.length` sees. */
export function characters(text) {
	return Array.from(String(text || "")).length;
}

/** YouTube limits its description in bytes, and an emoji is four. */
export function utf8Bytes(text) {
	return new TextEncoder().encode(String(text || "")).length;
}

export function hashtags(text) {
	return (String(text || "").match(/(^|\s)#[\p{L}\p{N}_]+/gu) || []).length;
}

export function mentions(text) {
	return (String(text || "").match(/(^|\s)@[\w.]+/g) || []).length;
}

/** The text an account gets: its own variant, else the post's text. */
export function textFor(state, target) {
	const variant = String((target && target.variant_text) || "").trim();
	return variant || String(state.body || "");
}

/** `[{label, value, max}]` for one network's text. `max` is null where there is no limit. */
export function counters(network, text, limits) {
	const rules = (limits && limits[network]) || {};
	if (network === INSTAGRAM) {
		return [
			{ label: "characters", value: characters(text), max: rules.text || null },
			{ label: "hashtags", value: hashtags(text), max: rules.hashtags || null },
			{ label: "@mentions", value: mentions(text), max: rules.mentions || null },
		];
	}
	if (network === LINKEDIN) return [{ label: "characters", value: characters(text), max: rules.text || null }];
	if (network === YOUTUBE) {
		return [{ label: "bytes of description", value: utf8Bytes(text), max: rules.text_bytes || null }];
	}
	return [{ label: "characters", value: characters(text), max: null }];
}

export function isOver(counter) {
	return counter.max !== null && counter.value > counter.max;
}

/** `"2026-09-23"` + `"09:05"` -> `"2026-09-23 09:05:00"`; `""` when there is no day. */
export function scheduledAt(day, time) {
	if (!/^\d{4}-\d{2}-\d{2}$/.test(String(day || ""))) return "";
	const clock = /^\d{2}:\d{2}$/.test(String(time || "")) ? time : "09:00";
	return `${day} ${clock}:00`;
}

/** What `save_post` and `check_post` take. The server takes nothing else (`spa_rules.POST_FIELDS`). */
export function payloadOf(state) {
	return {
		title: String(state.title || "").trim(),
		body: String(state.body || ""),
		link: String(state.link || "").trim(),
		link_title: String(state.link_title || "").trim(),
		link_description: String(state.link_description || "").trim(),
		scheduled_at: scheduledAt(state.day, state.time),
		video_title: String(state.video_title || "").trim(),
		video_tags: String(state.video_tags || "").trim(),
		video_thumbnail: state.thumbnail ? state.thumbnail.name : "",
		youtube_playlist_id: String(state.youtube_playlist_id || "").trim(),
		targets: (state.targets || []).map((t) => ({
			social_account: t.social_account,
			variant_text: String(t.variant_text || ""),
			first_comment: String(t.first_comment || ""),
		})),
		media: (state.media || []).map((asset) => asset.name),
	};
}

/** Whether the composer holds changes the server has not seen. */
export function isDirty(state, saved) {
	return JSON.stringify(payloadOf(state)) !== JSON.stringify(payloadOf(saved || {}));
}

/** The networks the selected accounts are on, in the order they were chosen, once each. */
export function networksOf(state) {
	const seen = [];
	for (const target of state.targets || []) {
		if (target.network && !seen.includes(target.network)) seen.push(target.network);
	}
	return seen;
}

/** Move the item at `index` by `delta` places; the order of the media is the carousel's order. */
export function moved(list, index, delta) {
	const items = list.slice();
	const to = index + delta;
	if (index < 0 || index >= items.length || to < 0 || to >= items.length) return items;
	const [item] = items.splice(index, 1);
	items.splice(to, 0, item);
	return items;
}

/**
 * What one account's post would look like, as data for the page to draw.
 *
 * `{network, account, title, text, media, card, notes, first_comment}` -- `card` is a link card
 * (`{url, title, description}`) or null, and `notes` are short sentences about what the
 * publisher does that the drawing cannot show.
 */
export function previewOf(state, target) {
	const network = target.network;
	const media = (state.media || []).slice();
	const link = String(state.link || "").trim();
	// What the publisher sends: our own site's link tagged for this post and network.
	const sent = tagged(link, network, state.name, state.campaign);
	const base = textFor(state, target);
	// The author's own copy of the link in the text carries the same tags.
	const swapped = link && base.includes(link) ? base.split(link).join(sent) : base;
	const out = {
		network,
		account: target.label || target.social_account,
		title: "",
		text: base,
		media,
		card: null,
		notes: [],
		first_comment: network === YOUTUBE ? "" : String(target.first_comment || "").trim(),
	};
	if (network === FACEBOOK) {
		if (media.length) out.text = withSentLink(base, link, sent);
		else {
			out.text = swapped;
			if (link) out.card = { url: sent, title: "", description: "" };
		}
		if (link && !media.length) out.notes.push("Facebook draws the link card from the page it points to.");
	} else if (network === INSTAGRAM) {
		if (link) out.notes.push("The link is not sent to Instagram.");
		if (media.length > 1) out.notes.push(`A carousel of ${media.length}.`);
	} else if (network === LINKEDIN) {
		if (media.length) out.text = withSentLink(base, link, sent);
		else {
			out.text = swapped;
			if (link) {
				out.card = {
					url: sent,
					title: String(state.link_title || "").trim(),
					description: String(state.link_description || "").trim(),
				};
			}
		}
	} else if (network === YOUTUBE) {
		out.title = String(state.video_title || "").trim();
		out.text = withSentLink(base, link, sent);
		out.media = media.filter((asset) => asset.asset_type === "Video").slice(0, 1);
		out.notes.push("The post text is the video's description.");
		if (String(state.video_tags || "").trim()) out.notes.push(`Tags: ${String(state.video_tags).trim()}`);
	}
	if (network !== INSTAGRAM && link && sent === link && isOwn(link) && !slug(state.name)) {
		out.notes.push("Tracking tags are added to this link once the post is saved.");
	}
	return out;
}
