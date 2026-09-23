/**
 * The media library: the Media tab, and the same grid as a picker inside the composer.
 *
 * **Adding a file** uploads it with no doctype (`transport.upload`), then `spa.create_asset`
 * makes the Marketing Media Asset and Frappe attaches the file to it, which is what lets the rest
 * of the team see a private one. The upload form asks for the usage rights up front, and new
 * media starts as *Needs client approval* unless the uploader says otherwise, because only
 * *Cleared for social* media can be queued.
 *
 * **Private by default.** A public file is served to anyone with its address. Facebook and
 * Instagram fetch media by address, so a photo meant for them has to be public (or live in Google
 * Cloud Storage); the checkbox says so, and the composer's checks name a private one.
 */

import { call, upload, M } from "./transport.js";
import { append, assetThumb, button, checkbox, clear, dialog, el, field, fill, input, select } from "./dom.js";

const ANY = "";

/** The Media tab. */
export async function renderMedia(app) {
	const toolbar = append(el("div", "ee-mk-toolbar"), el("h2", "ee-mk-toolbar-title", "Media"));
	const browser = mediaBrowser(app, { multiple: false, onPick: null });
	fill(app.pane, toolbar, uploadForm(app, () => browser.reload()), browser.node);
	await browser.reload();
}

/**
 * The composer's picker, as a dialog. `options`: `{title, multiple, assetType, exclude,
 * onPick(assets)}`. Cleared-for-social media only by default, since nothing else can be queued.
 */
export function openPicker(app, options) {
	const chosen = new Map();
	const browser = mediaBrowser(app, {
		multiple: options.multiple,
		assetType: options.assetType || ANY,
		exclude: new Set(options.exclude || []),
		clearedOnly: true,
		onPick: (asset, selected) => {
			if (!options.multiple) chosen.clear();
			if (selected) chosen.set(asset.name, asset);
			else chosen.delete(asset.name);
		},
		chosen,
	});
	const box = el("div", "ee-mk-picker");
	append(box, uploadForm(app, (asset) => browser.reload(asset)), browser.node);
	dialog(options.title || "Choose media", box, [
		{ label: "Cancel" },
		{
			label: options.multiple ? "Add selected" : "Use selected",
			primary: true,
			onClick: () => {
				if (!chosen.size) return false;
				options.onPick(Array.from(chosen.values()));
				return true;
			},
		},
	]);
	browser.reload();
}

/** Search, filters and a grid; `reload(justAdded)` refetches the first page. */
function mediaBrowser(app, opts) {
	const node = el("section", "ee-mk-section ee-mk-media");
	const search = input("search", "", "Search titles");
	const type = select([{ value: ANY, label: "All types" }, ...(app.data.asset_types || [])], opts.assetType || ANY);
	const cleared = checkbox(opts.clearedOnly !== false && opts.onPick !== null);
	const filters = el("div", "ee-mk-filters");
	append(
		filters,
		field("Search", search).row,
		field("Type", type).row,
		append(el("label", "ee-mk-inline"), cleared, el("span", null, "Cleared for social only"))
	);
	const grid = el("div", "ee-mk-grid");
	const more = el("div", "ee-mk-more");
	append(node, filters, grid, more);

	let start = 0;
	let timer = null;
	const load = async (append_, justAdded) => {
		if (!append_) {
			start = 0;
			app.showPlaceholder(grid, "◷", "Loading media…");
		}
		let result;
		try {
			result = await call(M.MEDIA, {
				search: search.value,
				asset_type: type.value,
				cleared_only: cleared.checked ? 1 : 0,
				start,
			});
		} catch (e) {
			app.showPlaceholder(grid, "!", "The media could not load", e.message);
			return;
		}
		const assets = (result.assets || []).filter((a) => !(opts.exclude && opts.exclude.has(a.name)));
		if (!append_) renderMediaGrid(app, grid, assets, opts, justAdded);
		else for (const asset of assets) grid.appendChild(tile(asset, opts));
		start += (result.assets || []).length;
		clear(more);
		if (result.more) more.appendChild(button("Show more", "ee-mk-btn", () => load(true)));
	};
	search.addEventListener("input", () => {
		clearTimeout(timer);
		timer = setTimeout(() => load(false), 300);
	});
	type.addEventListener("change", () => load(false));
	cleared.addEventListener("change", () => load(false));
	return { node, reload: (justAdded) => load(false, justAdded) };
}

function renderMediaGrid(app, grid, assets, opts, justAdded) {
	if (assets.length === 0) {
		app.showPlaceholder(
			grid,
			"🖼",
			"No media matches",
			"Add a photo or video above. Only media cleared for social can go into a post."
		);
		return;
	}
	clear(grid);
	for (const asset of assets) {
		const node = tile(asset, opts);
		grid.appendChild(node);
		if (justAdded && justAdded.name === asset.name && opts.onPick) node.click();
	}
}

function tile(asset, opts) {
	const pickable = !!opts.onPick;
	const node = el(pickable ? "button" : "div", "ee-mk-tile");
	if (pickable) {
		node.type = "button";
		node.setAttribute("aria-pressed", opts.chosen && opts.chosen.has(asset.name) ? "true" : "false");
		node.addEventListener("click", () => {
			const selected = node.getAttribute("aria-pressed") !== "true";
			if (!opts.multiple) {
				for (const other of node.parentNode.querySelectorAll(".ee-mk-tile[aria-pressed='true']")) {
					other.setAttribute("aria-pressed", "false");
				}
			}
			node.setAttribute("aria-pressed", selected ? "true" : "false");
			opts.onPick(asset, selected);
		});
	}
	append(
		node,
		assetThumb(asset, "ee-mk-thumb ee-mk-thumb-large"),
		el("span", "ee-mk-tile-title", asset.title),
		el("span", `ee-mk-rights ee-mk-rights-${asset.usage_rights === "Cleared for social" ? "ok" : "no"}`, rightsLabel(asset.usage_rights)),
		asset.private ? el("span", "ee-mk-muted", "Private file") : null
	);
	return node;
}

function rightsLabel(rights) {
	return `${rights === "Cleared for social" ? "✓" : "✕"} ${rights || "No rights set"}`;
}

/** Upload one file and make it an asset. `onAdded(asset)` runs once it exists. */
function uploadForm(app, onAdded) {
	const wrap = el("details", "ee-mk-upload");
	append(wrap, el("summary", null, "Add a photo, video or PDF"));
	const file = input("file");
	file.accept = "image/*,video/*,application/pdf";
	const title = input("text", "");
	const rights = select(app.data.usage_rights || [], (app.data.usage_rights || [])[0]);
	const alt = input("text", "", "Describe the picture for people who cannot see it");
	const isPublic = checkbox(false);
	const progress = el("div", "ee-mk-progress");
	progress.setAttribute("role", "status");
	file.addEventListener("change", () => {
		const chosen = file.files && file.files[0];
		if (chosen && !title.value) title.value = chosen.name.replace(/\.[^.]+$/, "");
	});
	const go = button("Upload", "ee-mk-btn ee-mk-btn-primary", async () => {
		const chosen = file.files && file.files[0];
		if (!chosen) {
			app.say("Choose a file first.", "error");
			return;
		}
		const assetType = typeOf(chosen.type);
		if (!assetType) {
			app.say("Only photos, videos and PDFs can be added.", "error");
			return;
		}
		go.disabled = true;
		try {
			const size = await measure(chosen, assetType);
			const sent = upload(chosen, !isPublic.checked, (share) => {
				progress.textContent = `Uploading… ${Math.round(share * 100)}%`;
			});
			const uploaded = await sent.promise;
			progress.textContent = "Saving…";
			const asset = await call(M.CREATE_ASSET, {
				file: uploaded.name,
				title: title.value || chosen.name,
				asset_type: assetType,
				usage_rights: rights.value,
				alt_text: alt.value,
				mime_type: chosen.type,
				width: size.width,
				height: size.height,
				duration_seconds: size.duration,
			});
			progress.textContent = `Added “${asset.title}”.`;
			file.value = "";
			title.value = "";
			alt.value = "";
			onAdded(asset);
		} catch (e) {
			progress.textContent = "";
			app.fail(e);
		} finally {
			go.disabled = false;
		}
	});
	append(
		wrap,
		field("File", file).row,
		field("Title", title).row,
		field("Usage rights", rights, "Only media cleared for social can be queued. A client's fountain needs the client's approval first.").row,
		field("Alt text", alt).row,
		append(
			el("label", "ee-mk-inline"),
			isPublic,
			el(
				"span",
				null,
				"Public file. Facebook and Instagram fetch media from its web address, so they need this; anyone with the address can open it."
			)
		),
		append(el("div", "ee-mk-upload-actions"), go, progress)
	);
	return wrap;
}

function typeOf(mime) {
	const value = String(mime || "").toLowerCase();
	if (value.startsWith("image/")) return "Image";
	if (value.startsWith("video/")) return "Video";
	if (value === "application/pdf") return "Document";
	return "";
}

/**
 * Width, height and (for a video) duration, read in the browser before upload. The server
 * checks Instagram's ratios and Reel length against these; without them it cannot.
 */
function measure(file, assetType) {
	return new Promise((resolve) => {
		const none = { width: null, height: null, duration: null };
		if (assetType === "Document") return resolve(none);
		const url = URL.createObjectURL(file);
		const done = (value) => {
			URL.revokeObjectURL(url);
			resolve(value);
		};
		if (assetType === "Image") {
			const img = new Image();
			img.onload = () => done({ width: img.naturalWidth, height: img.naturalHeight, duration: null });
			img.onerror = () => done(none);
			img.src = url;
		} else {
			const video = document.createElement("video");
			video.preload = "metadata";
			video.onloadedmetadata = () =>
				done({ width: video.videoWidth || null, height: video.videoHeight || null, duration: isFinite(video.duration) ? video.duration : null });
			video.onerror = () => done(none);
			video.src = url;
		}
		return undefined;
	});
}
