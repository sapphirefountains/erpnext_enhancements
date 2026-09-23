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
 * It never guesses where a network folds "… more", because that moves with the device, and a
 * confident wrong preview is worse than a plain one. The counters count against the limits the
 * server's own checks use (`get_bootstrap().limits`), and the server's answer (`check_post`)
 * is the one the page trusts.
 */

export const FACEBOOK = "Facebook";
export const INSTAGRAM = "Instagram";
export const LINKEDIN = "LinkedIn";
export const YOUTUBE = "YouTube";

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

function withLink(text, link) {
	const url = String(link || "").trim();
	if (!url || text.includes(url)) return text;
	return text ? `${text}\n\n${url}` : url;
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
	const base = textFor(state, target);
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
		if (media.length) out.text = withLink(base, link);
		else if (link) out.card = { url: link, title: "", description: "" };
		if (link && !media.length) out.notes.push("Facebook draws the link card from the page it points to.");
	} else if (network === INSTAGRAM) {
		if (link) out.notes.push("The link is not sent to Instagram.");
		if (media.length > 1) out.notes.push(`A carousel of ${media.length}.`);
	} else if (network === LINKEDIN) {
		if (media.length) out.text = withLink(base, link);
		else if (link) {
			out.card = {
				url: link,
				title: String(state.link_title || "").trim(),
				description: String(state.link_description || "").trim(),
			};
		}
	} else if (network === YOUTUBE) {
		out.title = String(state.video_title || "").trim();
		out.text = withLink(base, link);
		out.media = media.filter((asset) => asset.asset_type === "Video").slice(0, 1);
		out.notes.push("The post text is the video's description.");
		if (String(state.video_tags || "").trim()) out.notes.push(`Tags: ${String(state.video_tags).trim()}`);
	}
	return out;
}
