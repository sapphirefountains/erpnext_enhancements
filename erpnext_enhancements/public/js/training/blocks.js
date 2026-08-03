// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt
//
// The block renderers: one function per `Training Content Block.block_type`,
// each turning a published block payload into a detached HTMLElement the player
// drops into the lesson column.
//
// Kept apart from player.js because the set of block types is the part of the
// runtime that grows. Adding "Audio" or "Interactive Diagram" later should be a
// new key in `TR.blocks` and nothing else — no edit to the view machinery, no
// second place that decides what a lesson looks like.
//
// Three things here are load-bearing and are not stylistic preferences:
//
// * Rich Text and Callout HTML is injected verbatim. It was sanitised on the
//   server at publish (`_split_lesson` runs `frappe.utils.sanitize_html`), and
//   sanitising is a server responsibility precisely because a client cannot be
//   trusted to have done it. Re-sanitising here would create a second, weaker
//   definition of "safe" and invite someone to eventually rely on it.
// * A PDF is never rendered in `<iframe src="…​.pdf">`. iOS's viewer renders page
//   one and silently drops the rest, so a learner scrolls, sees nothing more,
//   and reports the course as broken. We show a card that opens the file
//   properly and gate on dwell plus an explicit acknowledgement instead.
// * Everything except the sanitised HTML goes in through `textContent`. Headings
//   and captions are author-entered free text and were never sanitised.
//
// No `frappe.*` anywhere: these run for Website Users with `desk_access = 0`,
// who never load the desk bundle. Network access is only ever through the
// transport handed down on `ctx`.
(function () {
	"use strict";

	var TR = (window.TR = window.TR || {});
	var blocks = (TR.blocks = TR.blocks || {});

	// How long a document has to be open before the acknowledgement unlocks.
	// Deliberately short: it exists to stop a reflexive tick-and-move-on, not to
	// simulate reading. Anything longer would be measuring patience.
	var DOC_MIN_DWELL_SECONDS = 20;

	// ------------------------------------------------------------------ helpers

	function el(tag, className, text) {
		var node = document.createElement(tag);
		if (className) node.className = className;
		if (text != null) node.textContent = String(text);
		return node;
	}

	function settingsValue(ctx, key, fallback) {
		var settings = (ctx && ctx.settings) || {};
		var value = parseInt(settings[key], 10);
		return isNaN(value) || value <= 0 ? fallback : value;
	}

	function blockProgress(ctx, block) {
		if (!ctx || typeof ctx.blockProgress !== "function") return {};
		return ctx.blockProgress(block.block_key) || {};
	}

	function heartbeat(ctx, payload) {
		if (!ctx || typeof ctx.heartbeat !== "function") return Promise.resolve({});
		return ctx.heartbeat(payload).catch(function () {
			// A dropped heartbeat is not the learner's problem and must never
			// interrupt them. The server's stale-attempt sweep and the next
			// successful beat between them recover the state.
			return {};
		});
	}

	function requestGateRefresh(ctx) {
		if (ctx && typeof ctx.requestGateRefresh === "function") ctx.requestGateRefresh();
	}

	function onFlush(ctx, fn) {
		if (ctx && typeof ctx.onFlush === "function") ctx.onFlush(fn);
	}

	function label(ctx, text) {
		return ctx && typeof ctx.t === "function" ? ctx.t(text) : text;
	}

	// Resolves the playable/downloadable URL for a block. `mediaUrl` is a
	// transport method because a private GCS object needs a short-lived signed
	// URL minted server-side; the raw `block.file` path is only a fallback for a
	// plain Frappe File attachment.
	function mediaUrl(ctx, block, raw) {
		if (ctx && typeof ctx.mediaUrl === "function") {
			return ctx.mediaUrl(block).then(function (url) {
				return url || raw || "";
			});
		}
		return Promise.resolve(raw || "");
	}

	// The common chrome around every block: optional heading above, optional
	// caption below. Built here so a new block type cannot forget it.
	function card(block, modifier) {
		var section = el("section", "tr-block tr-block-" + modifier);
		section.setAttribute("data-block-key", block.block_key || "");
		section.setAttribute("data-block-type", block.type || "");
		if (block.heading) section.appendChild(el("h3", "tr-block-heading", block.heading));
		var body = el("div", "tr-block-body");
		section.appendChild(body);
		section.__body = body;
		return section;
	}

	function finish(section, block) {
		if (block.caption) section.appendChild(el("p", "tr-block-caption", block.caption));
		return section;
	}

	function note(text, tone) {
		var box = el("p", "tr-block-note" + (tone ? " tr-block-note-" + tone : ""), text);
		box.setAttribute("role", "note");
		return box;
	}

	// ----------------------------------------------------------------- lightbox

	// Self-contained rather than a player service: an image block is the only
	// thing that wants a lightbox, and a block renderer that works when dropped
	// into the Phase-3 builder preview is worth more than one shared helper.
	function openLightbox(ctx, src, alt) {
		if (ctx && typeof ctx.lightbox === "function") {
			ctx.lightbox(src, alt);
			return;
		}
		var previous = document.activeElement;
		var overlay = el("div", "tr-lightbox");
		overlay.setAttribute("role", "dialog");
		overlay.setAttribute("aria-modal", "true");
		overlay.setAttribute("aria-label", alt || label(ctx, "Image"));
		overlay.tabIndex = -1;

		var img = el("img", "tr-lightbox-image");
		img.src = src;
		img.alt = alt || "";
		overlay.appendChild(img);

		var close = el("button", "tr-lightbox-close", "×");
		close.type = "button";
		close.setAttribute("aria-label", label(ctx, "Close"));
		overlay.appendChild(close);

		function dismiss() {
			document.removeEventListener("keydown", onKey);
			if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
			if (previous && typeof previous.focus === "function") previous.focus();
		}
		function onKey(event) {
			if (event.key === "Escape") dismiss();
		}
		overlay.addEventListener("click", dismiss);
		document.addEventListener("keydown", onKey);

		document.body.appendChild(overlay);
		overlay.focus();
	}

	// ---------------------------------------------------------------- rich text

	blocks["Rich Text"] = function (block, ctx) {
		var section = card(block, "richtext");
		// Server-sanitised at publish. See the file header before changing this.
		section.__body.innerHTML = block.html || "";
		return finish(section, block);
	};

	blocks.Callout = function (block, ctx) {
		var section = card(block, "callout");
		section.__body.innerHTML = block.html || "";
		// An aside rather than a div so a screen reader announces it as the set
		// apart thing the author meant it to be.
		section.setAttribute("role", "note");
		return finish(section, block);
	};

	// -------------------------------------------------------------------- image

	blocks.Image = function (block, ctx) {
		var section = card(block, "image");
		var button = el("button", "tr-image-tap");
		button.type = "button";

		var img = el("img", "tr-image");
		img.loading = "lazy";
		img.decoding = "async";
		// The caption is the only author-written description we have; without it
		// an empty alt at least marks the image decorative rather than lying.
		img.alt = block.caption || block.heading || "";
		section.__body.appendChild(button);
		button.appendChild(img);

		mediaUrl(ctx, block, block.image).then(function (url) {
			if (!url) {
				section.__body.appendChild(note(label(ctx, "This image is unavailable."), "warn"));
				return;
			}
			img.src = url;
			button.setAttribute("aria-label", label(ctx, "Open image full size"));
			button.addEventListener("click", function () {
				openLightbox(ctx, url, img.alt);
			});
		});

		return finish(section, block);
	};

	// ---------------------------------------------------------------------- pdf

	// Dwell + acknowledgement, and nothing finer. Real per-page telemetry —
	// which page, how long on it, did they reach the end — needs PDF.js rendering
	// the document into our own canvas, which is a vendored dependency and a
	// viewer's worth of UI. Deliberately out of scope: the honest gate for a
	// document is "it was open long enough to have been read, and they said they
	// read it", and pretending otherwise would be the same overclaim the module
	// README warns about for video coverage.
	blocks.PDF = function (block, ctx) {
		var section = card(block, "pdf");
		var minDwell = settingsValue(ctx, "doc_min_dwell_seconds", DOC_MIN_DWELL_SECONDS);
		var stored = blockProgress(ctx, block);
		var opened = false;
		var openedAt = 0;
		var acknowledged = !!stored.ack;
		var timer = null;

		var row = el("div", "tr-doc-row");
		var open = el("a", "tr-doc-open tr-button tr-button-secondary", label(ctx, "Open PDF"));
		open.target = "_blank";
		open.rel = "noopener";
		open.setAttribute("aria-disabled", "true");
		row.appendChild(open);
		section.__body.appendChild(row);

		var status = el("p", "tr-doc-status");
		status.setAttribute("role", "status");
		status.setAttribute("aria-live", "polite");
		section.__body.appendChild(status);

		var ackWrap = el("label", "tr-doc-ack");
		var ackBox = el("input");
		ackBox.type = "checkbox";
		ackBox.disabled = true;
		ackBox.checked = acknowledged;
		ackWrap.appendChild(ackBox);
		ackWrap.appendChild(el("span", null, label(ctx, "I have read this document.")));
		section.__body.appendChild(ackWrap);

		function dwellSeconds() {
			if (!opened) return 0;
			return Math.floor((Date.now() - openedAt) / 1000);
		}

		function paint() {
			if (acknowledged) {
				status.textContent = label(ctx, "Marked as read.");
				return;
			}
			if (!opened) {
				status.textContent = label(ctx, "Open the document to continue.");
				return;
			}
			var left = minDwell - dwellSeconds();
			status.textContent =
				left > 0
					? label(ctx, "You can mark this as read in {0}s.").replace("{0}", String(left))
					: label(ctx, "Tick the box below when you have read it.");
		}

		function beat(extra) {
			var seconds = Math.min(dwellSeconds(), minDwell);
			var payload = {
				block_key: block.block_key,
				kind: "doc",
				duration: minDwell,
				played: seconds > 0 ? [[0, seconds]] : [],
				claimed: seconds,
				seeks: 0,
				hidden: 0,
			};
			if (extra) {
				for (var k in extra) if (Object.prototype.hasOwnProperty.call(extra, k)) payload[k] = extra[k];
			}
			return heartbeat(ctx, payload);
		}

		open.addEventListener("click", function () {
			if (opened) return;
			opened = true;
			openedAt = Date.now();
			paint();
			// The learner is now in another tab, so an interval here keeps running
			// but the UI they are looking at is the PDF. The tick only exists to
			// have the checkbox already unlocked when they come back.
			timer = window.setInterval(function () {
				paint();
				if (dwellSeconds() < minDwell) return;
				window.clearInterval(timer);
				timer = null;
				if (!acknowledged) ackBox.disabled = false;
				beat();
			}, 1000);
		});

		// One-way on purpose. The tick is a statement of fact recorded against a
		// compliance record; letting it be taken back turns "I read it" into a
		// toggle, and leaves the server holding whichever beat arrived last.
		ackBox.addEventListener("change", function () {
			if (!ackBox.checked) {
				ackBox.checked = true;
				return;
			}
			acknowledged = true;
			ackBox.disabled = true;
			paint();
			beat({ ack: 1 }).then(function () {
				requestGateRefresh(ctx);
			});
		});

		onFlush(ctx, function () {
			if (timer) {
				window.clearInterval(timer);
				timer = null;
			}
			if (opened && !acknowledged) return beat();
			return null;
		});

		mediaUrl(ctx, block, block.file).then(function (url) {
			if (!url) {
				open.setAttribute("aria-disabled", "true");
				section.__body.appendChild(note(label(ctx, "This document is unavailable."), "warn"));
				return;
			}
			open.href = url;
			open.removeAttribute("aria-disabled");
		});

		paint();
		return finish(section, block);
	};

	// ------------------------------------------------------------------- video

	blocks.Video = function (block, ctx) {
		var section = card(block, "video");
		if (TR.Video && typeof TR.Video.mount === "function") {
			TR.Video.mount(section.__body, block, ctx);
		} else {
			// Only reachable if video.js failed to load. Say so plainly rather than
			// rendering an empty card the learner will stare at.
			section.__body.appendChild(
				note(label(ctx, "The video player did not load. Please refresh the page."), "warn")
			);
		}
		return finish(section, block);
	};

	// ----------------------------------------------------------- external embed

	// The note is not decoration. This block behaves visibly differently from a
	// Video block — no checkpoints, no coverage bar, nothing to satisfy — and a
	// learner who is not told why assumes the page is broken and opens a ticket.
	// The server refuses to apply a coverage gate here; see the module README.
	blocks["External Embed"] = function (block, ctx) {
		var section = card(block, "embed");
		if (!block.embed_url) {
			section.__body.appendChild(note(label(ctx, "This embed is unavailable."), "warn"));
			return finish(section, block);
		}

		var frame = el("iframe", "tr-embed-frame");
		frame.src = block.embed_url;
		frame.loading = "lazy";
		frame.referrerPolicy = "no-referrer";
		frame.setAttribute("allowfullscreen", "allowfullscreen");
		frame.setAttribute("title", block.heading || label(ctx, "Embedded content"));
		// Not sandboxed: a Drive preview and every hosted video player need
		// allow-scripts and allow-same-origin together, which is exactly the pair
		// that makes a sandbox no stronger than none. Choosing the block type is
		// the author's decision to trust that origin.
		section.__body.appendChild(frame);

		section.__body.appendChild(
			note(
				label(
					ctx,
					"This is played by another site, so we cannot see into it: no in-video questions " +
						"and no watch tracking for this one."
				)
			)
		);
		return finish(section, block);
	};

	// -------------------------------------------------------- downloadable file

	blocks["Downloadable File"] = function (block, ctx) {
		var section = card(block, "file");
		// `blockProgress` hands back a fresh object when the server has nothing
		// stored for this block, so the flag has to live here rather than on it.
		var sent = !!blockProgress(ctx, block).ack;
		var link = el("a", "tr-doc-open tr-button tr-button-secondary", label(ctx, "Download"));
		link.target = "_blank";
		link.rel = "noopener";
		link.setAttribute("aria-disabled", "true");
		section.__body.appendChild(link);

		link.addEventListener("click", function () {
			if (sent) return;
			sent = true;
			heartbeat(ctx, {
				block_key: block.block_key,
				kind: "doc",
				duration: 1,
				played: [[0, 1]],
				claimed: 1,
				seeks: 0,
				hidden: 0,
				ack: 1,
			}).then(function () {
				requestGateRefresh(ctx);
			});
		});

		mediaUrl(ctx, block, block.file).then(function (url) {
			if (!url) {
				section.__body.appendChild(note(label(ctx, "This file is unavailable."), "warn"));
				return;
			}
			link.href = url;
			link.removeAttribute("aria-disabled");
		});

		return finish(section, block);
	};

	// ------------------------------------------------------------------ divider

	blocks.Divider = function (block) {
		var section = el("section", "tr-block tr-block-divider");
		section.setAttribute("data-block-key", block.block_key || "");
		section.appendChild(el("hr", "tr-divider"));
		return section;
	};

	// ----------------------------------------------------------------- dispatch

	// The dispatcher lives here rather than in the player so `TR.blocks` stays a
	// clean type-name → renderer map. An unknown type renders a placeholder
	// instead of throwing: a lesson published from a newer app version must
	// degrade to "one block you cannot see yet", never to a blank page.
	TR.renderBlock = function (block, ctx) {
		var type = (block && block.type) || "";
		var render = blocks[type];
		if (typeof render !== "function") {
			var section = el("section", "tr-block tr-block-unknown");
			section.appendChild(
				note(label(ctx, "This part of the lesson needs a newer version of the app."), "warn")
			);
			return section;
		}
		try {
			return render(block, ctx);
		} catch (err) {
			// One broken block must not take the lesson down with it.
			if (window.console && console.warn) console.warn("Training block failed to render", type, err);
			var failed = el("section", "tr-block tr-block-unknown");
			failed.appendChild(note(label(ctx, "This part of the lesson could not be shown."), "warn"));
			return failed;
		}
	};
})();
