/**
 * The screenshot annotator: box, arrow, pen, text, blur and crop over one image, with undo.
 *
 * **What leaves the browser is only ever the flattened canvas.** The image is drawn once into
 * a `base` canvas (capped at 2560px on the long edge) and never touched again; every edit is
 * an op in a list, and the `work` canvas is `base` with the ops replayed onto it. `toBlob()`
 * exports `work`. The file the person picked or pasted is never read again after that first
 * draw, so the original cannot be uploaded by accident.
 *
 * **Blur and crop are pixel operations, not overlays.** Box, arrow, pen and text are drawn
 * as vectors, but blur rewrites the pixels under its rectangle and crop replaces the canvas,
 * both on `work` — the canvas that is exported. A blur drawn as a translucent overlay would
 * be one layer-strip away from the original; this one is burned in before anything is
 * uploaded. Undo replays `base` plus the remaining ops, so undoing a blur restores the
 * pixels from `base` rather than trying to reverse a mosaic, which cannot be done.
 *
 * **The mosaic carries noise on purpose.** Plain block-averaging of text is reversible in
 * practice: tools such as Depix match averaged blocks against a rendered font and read the
 * text back. Every block here is shifted by a small seeded random amount, so no block equals
 * the average a matcher would compute. The seed lives in the op so undo/replay draws the
 * same mosaic; it is never exported.
 *
 * Display is scaled to fit the host while the canvases keep full resolution, so a phone
 * shows the whole screenshot and the export stays sharp. Pointer events throughout: one code
 * path for mouse, pen and touch, with `touch-action: none` on the canvas so a drag draws
 * instead of scrolling the panel.
 *
 * The pure helpers (geometry, the mosaic, op building) are exported and have no DOM
 * dependency, so `scripts/test_capture_annotate.js` exercises them in plain node. Nothing
 * touches `document` at import time.
 */

export const MAX_EXPORT_EDGE = 2560;
/** Frappe's default `max_file_size` is 10 MB; stay under it with room for the form envelope. */
export const DEFAULT_MAX_BYTES = 8 * 1024 * 1024;
export const TOOLS = ["box", "arrow", "pen", "text", "blur", "crop"];
export const MIN_CROP = 16;
/** Amplitude of the per-block noise in the blur mosaic, in 0-255 channel units. */
export const BLUR_JITTER = 14;

const TOOL_LABELS = {
	box: "Box",
	arrow: "Arrow",
	pen: "Pen",
	text: "Text",
	blur: "Blur",
	crop: "Crop",
};

const TOOL_HINTS = {
	box: "Drag to draw a box.",
	arrow: "Drag to draw an arrow.",
	pen: "Draw freehand.",
	text: "Tap where the text should go.",
	blur: "Drag over anything private. It is hidden for good.",
	crop: "Drag to keep only part of the image.",
};

const INK = "#e5484d";
const HALO = "rgba(255, 255, 255, 0.92)";
const FONT = 'system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif';
const LOAD_TIMEOUT_MS = 20000;

// ------------------------------------------------------------------ pure helpers

function clamp(value, lo, hi) {
	return Math.min(hi, Math.max(lo, value));
}

function finite(...values) {
	return values.every((v) => typeof v === "number" && Number.isFinite(v));
}

/** The export dimensions for an image: unchanged, or scaled down so the long edge fits. */
export function exportSize(width, height, maxEdge) {
	const cap = maxEdge > 0 ? maxEdge : MAX_EXPORT_EDGE;
	if (!finite(width, height) || width <= 0 || height <= 0) return { w: 0, h: 0, scale: 0 };
	const long = Math.max(width, height);
	const scale = long > cap ? cap / long : 1;
	return {
		w: Math.max(1, Math.round(width * scale)),
		h: Math.max(1, Math.round(height * scale)),
		scale,
	};
}

/**
 * Display scale that fits `width x height` inside the box. Never above 1: enlarging a small
 * screenshot on screen only makes it look blurred and invites marks finer than its pixels.
 */
export function fitScale(width, height, boxWidth, boxHeight) {
	if (!finite(width, height) || width <= 0 || height <= 0) return 1;
	let scale = 1;
	if (finite(boxWidth) && boxWidth > 0) scale = Math.min(scale, boxWidth / width);
	if (finite(boxHeight) && boxHeight > 0) scale = Math.min(scale, boxHeight / height);
	return scale > 0 ? scale : 1;
}

/** A drag from any corner to any corner, as `{x, y, w, h}` with a non-negative size. */
export function normRect(x1, y1, x2, y2) {
	return {
		x: Math.min(x1, x2),
		y: Math.min(y1, y2),
		w: Math.abs(x2 - x1),
		h: Math.abs(y2 - y1),
	};
}

/**
 * Intersect a rectangle with the image, snapped OUTWARD to whole pixels, or null when
 * nothing is left. Outward, because for a blur a pixel half-covered by the selection is a
 * pixel the person meant to hide.
 */
export function clampRect(rect, width, height) {
	if (!rect || !finite(rect.x, rect.y, rect.w, rect.h, width, height)) return null;
	const x0 = Math.max(0, Math.floor(rect.x));
	const y0 = Math.max(0, Math.floor(rect.y));
	const x1 = Math.min(Math.floor(width), Math.ceil(rect.x + rect.w));
	const y1 = Math.min(Math.floor(height), Math.ceil(rect.y + rect.h));
	if (x1 <= x0 || y1 <= y0) return null;
	return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
}

/**
 * A pointer position in image pixels. `box` is the canvas's `getBoundingClientRect()`, which
 * already reflects every CSS scale between the image and the screen — one division here
 * replaces tracking the display scale by hand.
 */
export function toImagePoint(clientX, clientY, box, width, height) {
	const bw = box && box.width > 0 ? box.width : 1;
	const bh = box && box.height > 0 ? box.height : 1;
	const left = box && finite(box.left) ? box.left : 0;
	const top = box && finite(box.top) ? box.top : 0;
	const x = ((clientX - left) / bw) * width;
	const y = ((clientY - top) / bh) * height;
	return {
		x: clamp(finite(x) ? x : 0, 0, width),
		y: clamp(finite(y) ? y : 0, 0, height),
	};
}

/** Stroke width that reads the same on a phone photo and a 1280px screenshot. */
export function inkWidth(width, height) {
	return clamp(Math.round(Math.max(width, height) / 260), 3, 12);
}

export function textSize(width, height) {
	return clamp(Math.round(Math.max(width, height) / 45), 16, 72);
}

/**
 * Mosaic block size. Scaled to the image so a blur on a 2560px capture is not a fine grid a
 * matcher can read, with a floor of 10px because below that small text survives.
 */
export function blurBlock(width, height) {
	return clamp(Math.round(Math.max(width, height) / 64), 10, 48);
}

/**
 * The two back corners and the shaft end of an arrowhead pointing at (x2, y2). The shaft
 * stops at `base` so its round cap cannot poke through the tip.
 */
export function arrowHead(x1, y1, x2, y2, size) {
	const angle = Math.atan2(y2 - y1, x2 - x1);
	const spread = Math.PI / 7;
	return {
		left: [x2 - size * Math.cos(angle - spread), y2 - size * Math.sin(angle - spread)],
		right: [x2 - size * Math.cos(angle + spread), y2 - size * Math.sin(angle + spread)],
		base: [x2 - size * 0.8 * Math.cos(angle), y2 - size * 0.8 * Math.sin(angle)],
	};
}

/** Small seeded PRNG, so a blur's noise is the same every time the op list is replayed. */
export function mulberry32(seed) {
	let a = seed >>> 0;
	return function next() {
		a = (a + 0x6d2b79f5) >>> 0;
		let t = a;
		t = Math.imul(t ^ (t >>> 15), t | 1);
		t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
		return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
	};
}

/**
 * Burn a mosaic into RGBA pixel data in place. Returns the number of blocks written.
 *
 * `data` is `width * height * 4` bytes (an `ImageData.data`). Each block inside `rect` is
 * replaced by its average color, shifted by `(rng() - 0.5) * 2 * BLUR_JITTER` — one offset
 * for all three color channels, so the noise reads as texture rather than confetti. With no
 * `rng` the mosaic is a plain average (used by the tests to check the arithmetic).
 */
export function pixelate(data, width, height, rect, block, rng) {
	const r = clampRect(rect, width, height);
	if (!r || !data || data.length < width * height * 4) return 0;
	const size = Math.max(2, Math.floor(block) || 2);
	const noise = typeof rng === "function" ? rng : null;
	const xEndAll = r.x + r.w;
	const yEndAll = r.y + r.h;
	let blocks = 0;

	for (let by = r.y; by < yEndAll; by += size) {
		const yEnd = Math.min(by + size, yEndAll);
		for (let bx = r.x; bx < xEndAll; bx += size) {
			const xEnd = Math.min(bx + size, xEndAll);
			let sr = 0;
			let sg = 0;
			let sb = 0;
			let sa = 0;
			let n = 0;
			for (let y = by; y < yEnd; y++) {
				let i = (y * width + bx) * 4;
				for (let x = bx; x < xEnd; x++, i += 4) {
					sr += data[i];
					sg += data[i + 1];
					sb += data[i + 2];
					sa += data[i + 3];
					n++;
				}
			}
			if (!n) continue;
			const shift = noise ? (noise() - 0.5) * 2 * BLUR_JITTER : 0;
			const cr = clamp(Math.round(sr / n + shift), 0, 255);
			const cg = clamp(Math.round(sg / n + shift), 0, 255);
			const cb = clamp(Math.round(sb / n + shift), 0, 255);
			const ca = clamp(Math.round(sa / n), 0, 255);
			for (let y = by; y < yEnd; y++) {
				let i = (y * width + bx) * 4;
				for (let x = bx; x < xEnd; x++, i += 4) {
					data[i] = cr;
					data[i + 1] = cg;
					data[i + 2] = cb;
					data[i + 3] = ca;
				}
			}
			blocks++;
		}
	}
	return blocks;
}

/**
 * Turn a finished drag into an op, or null when the drag was too small to mean anything (a
 * stray tap with the box tool should not leave a 1px box to undo). Coordinates are image
 * pixels in the canvas's CURRENT space — after a crop, that is the cropped image.
 */
export function buildOp(tool, start, end, points, dims, seed) {
	if (!start || !end || !dims || !(dims.w > 0 && dims.h > 0)) return null;
	const width = inkWidth(dims.w, dims.h);
	if (tool === "box") {
		const rect = normRect(start.x, start.y, end.x, end.y);
		if (rect.w < 4 || rect.h < 4) return null;
		return { type: "box", rect, width };
	}
	if (tool === "blur") {
		const rect = clampRect(normRect(start.x, start.y, end.x, end.y), dims.w, dims.h);
		if (!rect || rect.w < 4 || rect.h < 4) return null;
		return { type: "blur", rect, block: blurBlock(dims.w, dims.h), seed: seed >>> 0 };
	}
	if (tool === "crop") {
		const rect = clampRect(normRect(start.x, start.y, end.x, end.y), dims.w, dims.h);
		if (!rect || rect.w < MIN_CROP || rect.h < MIN_CROP) return null;
		// Cropping to the whole image changes nothing and would sit in the undo list as a no-op.
		if (rect.x === 0 && rect.y === 0 && rect.w === dims.w && rect.h === dims.h) return null;
		return { type: "crop", rect };
	}
	if (tool === "arrow") {
		if (Math.hypot(end.x - start.x, end.y - start.y) < 8) return null;
		return { type: "arrow", from: { x: start.x, y: start.y }, to: { x: end.x, y: end.y }, width };
	}
	if (tool === "pen") {
		const list = Array.isArray(points) && points.length ? points : [start];
		return {
			type: "pen",
			points: list.map((p) => ({ x: Math.round(p.x * 10) / 10, y: Math.round(p.y * 10) / 10 })),
			width,
		};
	}
	return null;
}

/** Image dimensions after replaying every crop in `ops` over a `width x height` image. */
export function dimsAfter(width, height, ops) {
	let w = width;
	let h = height;
	for (const op of ops || []) {
		if (!op || op.type !== "crop") continue;
		const r = clampRect(op.rect, w, h);
		if (r) {
			w = r.w;
			h = r.h;
		}
	}
	return { w, h };
}

/**
 * The undo stack. Deliberately just a list: undo is "drop the last op and replay the rest
 * over the original", so there is no inverse to store and nothing to get out of step.
 */
export function createHistory() {
	const ops = [];
	return {
		push(op) {
			if (op) ops.push(op);
			return ops.length;
		},
		undo() {
			return ops.pop() || null;
		},
		list() {
			return ops.slice();
		},
		clear() {
			ops.length = 0;
		},
		get size() {
			return ops.length;
		},
	};
}

// ------------------------------------------------------------------ canvas plumbing

function makeCanvas(doc, width, height) {
	const canvas = doc.createElement("canvas");
	canvas.width = Math.max(1, Math.round(width));
	canvas.height = Math.max(1, Math.round(height));
	return canvas;
}

/**
 * Give a canvas's backing store back. iOS Safari caps total canvas memory per page, and a
 * detached canvas is not freed until it is collected; zeroing it frees the pixels now, which
 * matters when somebody retakes a phone photo three times.
 */
function release(canvas) {
	if (!canvas) return;
	try {
		canvas.width = 0;
		canvas.height = 0;
	} catch (e) {
		// Nothing to recover; the canvas is being thrown away either way.
	}
}

function cloneCanvas(doc, source) {
	const copy = makeCanvas(doc, source.width, source.height);
	copy.getContext("2d").drawImage(source, 0, 0);
	return copy;
}

function strokeWithHalo(ctx, width, tracePath) {
	ctx.save();
	ctx.lineJoin = "round";
	ctx.lineCap = "round";
	// A pale under-stroke so a red mark still reads on a red error banner or a dark theme.
	ctx.strokeStyle = HALO;
	ctx.lineWidth = width + Math.max(2, Math.round(width * 0.8));
	tracePath();
	ctx.stroke();
	ctx.strokeStyle = INK;
	ctx.lineWidth = width;
	tracePath();
	ctx.stroke();
	ctx.restore();
}

function drawVector(ctx, op) {
	if (op.type === "box") {
		const r = op.rect;
		strokeWithHalo(ctx, op.width, () => {
			ctx.beginPath();
			ctx.rect(r.x, r.y, r.w, r.h);
		});
	} else if (op.type === "pen") {
		const pts = op.points || [];
		if (!pts.length) return;
		strokeWithHalo(ctx, op.width, () => {
			ctx.beginPath();
			ctx.moveTo(pts[0].x, pts[0].y);
			// A single tap still leaves a dot: a zero-length segment with a round cap.
			if (pts.length === 1) ctx.lineTo(pts[0].x + 0.01, pts[0].y);
			for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i].x, pts[i].y);
		});
	} else if (op.type === "arrow") {
		const size = Math.max(16, op.width * 4.5);
		const head = arrowHead(op.from.x, op.from.y, op.to.x, op.to.y, size);
		strokeWithHalo(ctx, op.width, () => {
			ctx.beginPath();
			ctx.moveTo(op.from.x, op.from.y);
			ctx.lineTo(head.base[0], head.base[1]);
		});
		ctx.save();
		ctx.beginPath();
		ctx.moveTo(op.to.x, op.to.y);
		ctx.lineTo(head.left[0], head.left[1]);
		ctx.lineTo(head.right[0], head.right[1]);
		ctx.closePath();
		ctx.lineJoin = "round";
		ctx.strokeStyle = HALO;
		ctx.lineWidth = Math.max(2, Math.round(op.width * 0.8));
		ctx.stroke();
		ctx.fillStyle = INK;
		ctx.fill();
		ctx.restore();
	} else if (op.type === "text") {
		ctx.save();
		ctx.font = `600 ${op.size}px ${FONT}`;
		ctx.textBaseline = "top";
		ctx.lineJoin = "round";
		ctx.strokeStyle = HALO;
		ctx.lineWidth = Math.max(3, Math.round(op.size / 5));
		ctx.strokeText(op.text, op.at.x, op.at.y);
		ctx.fillStyle = INK;
		ctx.fillText(op.text, op.at.x, op.at.y);
		ctx.restore();
	}
}

function burnBlur(canvas, op) {
	const r = clampRect(op.rect, canvas.width, canvas.height);
	if (!r) return;
	const ctx = canvas.getContext("2d");
	try {
		const img = ctx.getImageData(r.x, r.y, r.w, r.h);
		pixelate(img.data, r.w, r.h, { x: 0, y: 0, w: r.w, h: r.h }, op.block, mulberry32(op.seed));
		ctx.putImageData(img, r.x, r.y);
	} catch (e) {
		// getImageData refuses a tainted canvas. A solid fill still hides the region, which is
		// the only promise a blur makes; looking like a mosaic is secondary.
		ctx.save();
		ctx.fillStyle = "#6b7280";
		ctx.fillRect(r.x, r.y, r.w, r.h);
		ctx.restore();
	}
}

/** Apply one op to `canvas`. Returns the canvas to use next: a crop returns a new one. */
function applyOp(doc, canvas, op) {
	if (!op) return canvas;
	if (op.type === "crop") {
		const r = clampRect(op.rect, canvas.width, canvas.height);
		if (!r) return canvas;
		const out = makeCanvas(doc, r.w, r.h);
		out.getContext("2d").drawImage(canvas, r.x, r.y, r.w, r.h, 0, 0, r.w, r.h);
		release(canvas);
		return out;
	}
	if (op.type === "blur") {
		burnBlur(canvas, op);
		return canvas;
	}
	drawVector(canvas.getContext("2d"), op);
	return canvas;
}

function canvasToBlob(canvas, type) {
	return new Promise((resolve, reject) => {
		try {
			if (typeof canvas.toBlob === "function") {
				canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error("The image could not be exported."))), type);
				return;
			}
			const url = canvas.toDataURL(type);
			const [head, body] = url.split(",");
			const mime = (head.match(/data:([^;]+)/) || [])[1] || type;
			const bytes = atob(body);
			const buf = new Uint8Array(bytes.length);
			for (let i = 0; i < bytes.length; i++) buf[i] = bytes.charCodeAt(i);
			resolve(new Blob([buf], { type: mime }));
		} catch (e) {
			reject(e);
		}
	});
}

function loadImageElement(doc, url) {
	return new Promise((resolve, reject) => {
		const img = doc.createElement("img");
		const timer = setTimeout(() => reject(new Error("The image took too long to open.")), LOAD_TIMEOUT_MS);
		img.onload = () => {
			clearTimeout(timer);
			resolve(img);
		};
		img.onerror = () => {
			clearTimeout(timer);
			reject(new Error("The image could not be opened."));
		};
		img.decoding = "async";
		img.src = url;
	});
}

/** Anything drawable, from a Blob/File, an <img>, a canvas, an ImageBitmap or a URL. */
async function loadSource(doc, source) {
	if (!source) throw new Error("No image.");
	if (typeof Blob !== "undefined" && source instanceof Blob) {
		if (source.type && !/^image\//i.test(source.type)) throw new Error("That file is not an image.");
		if (typeof createImageBitmap === "function") {
			try {
				return await createImageBitmap(source, { imageOrientation: "from-image" });
			} catch (e) {
				try {
					return await createImageBitmap(source);
				} catch (e2) {
					// Older engines refuse some blobs here; an <img> decodes them.
				}
			}
		}
		const url = URL.createObjectURL(source);
		try {
			return await loadImageElement(doc, url);
		} finally {
			// The <img> has decoded by now; the URL is not needed to draw it.
			URL.revokeObjectURL(url);
		}
	}
	if (typeof source === "string") return loadImageElement(doc, source);
	if (source.tagName === "IMG" && !source.complete) {
		await new Promise((resolve, reject) => {
			source.addEventListener("load", resolve, { once: true });
			source.addEventListener("error", () => reject(new Error("The image could not be opened.")), { once: true });
		});
	}
	return source;
}

function sourceSize(source) {
	return {
		w: source.naturalWidth || source.videoWidth || source.width || 0,
		h: source.naturalHeight || source.videoHeight || source.height || 0,
	};
}

function node(doc, tag, className, text) {
	const n = doc.createElement(tag);
	if (className) n.className = className;
	if (text) n.textContent = text;
	return n;
}

// ------------------------------------------------------------------ the editor

/**
 * Mount the annotator inside `host` and start loading `imageSource` into it.
 *
 * Returns immediately with a controller; `ready` settles once the image is on the canvas
 * (and rejects, with a sentence for a human, when it cannot be opened). `toBlob()` waits for
 * it. `options`:
 *   - `maxEdge`   — export cap on the long edge (default 2560);
 *   - `maxHeight` — display height cap in CSS px, or a function returning one;
 *   - `tool`      — the starting tool (default "box");
 *   - `onChange`  — called after every edit and undo.
 */
export function openAnnotator(host, imageSource, options) {
	const opts = options || {};
	const doc = (host && host.ownerDocument) || document;
	const win = doc.defaultView || (typeof window !== "undefined" ? window : null);

	const state = {
		tool: TOOLS.includes(opts.tool) ? opts.tool : "box",
		base: null,
		work: null,
		history: createHistory(),
		drag: null,
		raf: 0,
		layoutRaf: 0,
		lastWidth: -1,
		destroyed: false,
		text: null,
		cssScale: 1,
	};

	const root = node(doc, "div", "ee-cap-ann");
	const toolbar = node(doc, "div", "ee-cap-ann-toolbar");
	toolbar.setAttribute("role", "toolbar");
	toolbar.setAttribute("aria-label", "Markup tools");

	const toolButtons = {};
	for (const tool of TOOLS) {
		const btn = node(doc, "button", "ee-cap-btn ee-cap-tool", TOOL_LABELS[tool]);
		btn.type = "button";
		btn.dataset.tool = tool;
		btn.addEventListener("click", () => setTool(tool));
		toolButtons[tool] = btn;
		toolbar.appendChild(btn);
	}
	const undoButton = node(doc, "button", "ee-cap-btn ee-cap-tool ee-cap-ann-undo", "Undo");
	undoButton.type = "button";
	undoButton.addEventListener("click", () => undo());
	toolbar.appendChild(undoButton);

	const hint = node(doc, "p", "ee-cap-ann-hint", "");
	const stage = node(doc, "div", "ee-cap-ann-stage");
	const view = doc.createElement("canvas");
	view.className = "ee-cap-ann-canvas";
	// Set inline as well as in the stylesheet: without it a touch drag scrolls the panel
	// instead of drawing, and that must not depend on the CSS having loaded.
	view.style.touchAction = "none";
	view.tabIndex = 0;
	view.setAttribute("role", "img");
	view.setAttribute("aria-label", "Screenshot. Drag on it to mark it up.");
	const loading = node(doc, "p", "ee-cap-ann-loading", "Opening image…");
	stage.appendChild(view);
	stage.appendChild(loading);
	root.appendChild(toolbar);
	root.appendChild(hint);
	root.appendChild(stage);
	host.appendChild(root);

	const cleanups = [];
	function listen(target, type, fn, opt) {
		if (!target) return;
		target.addEventListener(type, fn, opt);
		cleanups.push(() => target.removeEventListener(type, fn, opt));
	}

	function notify() {
		if (typeof opts.onChange !== "function") return;
		try {
			opts.onChange();
		} catch (e) {
			// A caller's bug must not break drawing.
		}
	}

	function syncControls() {
		for (const tool of TOOLS) {
			toolButtons[tool].setAttribute("aria-pressed", tool === state.tool ? "true" : "false");
		}
		undoButton.disabled = state.history.size === 0;
		hint.textContent = TOOL_HINTS[state.tool] || "";
		view.dataset.tool = state.tool;
	}

	function contentWidth(el) {
		let width = el.clientWidth || 0;
		try {
			const cs = win.getComputedStyle(el);
			width -= (parseFloat(cs.paddingLeft) || 0) + (parseFloat(cs.paddingRight) || 0);
		} catch (e) {
			// No computed style (detached node): the padding is a few pixels either way.
		}
		return width;
	}

	function layout() {
		state.layoutRaf = 0;
		if (!state.work || state.destroyed) return;
		const W = state.work.width;
		const H = state.work.height;
		const availWidth = contentWidth(stage) || host.clientWidth || W;
		state.lastWidth = stage.clientWidth;
		const viewportHeight = (win && win.innerHeight) || 800;
		let maxHeight = typeof opts.maxHeight === "function" ? opts.maxHeight() : opts.maxHeight;
		if (!(maxHeight > 0)) maxHeight = Math.max(220, Math.round(viewportHeight * 0.55));
		const scale = fitScale(W, H, availWidth, maxHeight);
		const cssWidth = Math.max(1, Math.round(W * scale));
		const cssHeight = Math.max(1, Math.round(H * scale));
		const dpr = Math.min(3, (win && win.devicePixelRatio) || 1);
		// The visible canvas is only as big as the screen needs; drawing a full 2560px canvas
		// on every pointer move is what makes annotation stutter on a kiosk tablet.
		const backingW = Math.min(W, Math.max(1, Math.round(cssWidth * dpr)));
		const backingH = Math.min(H, Math.max(1, Math.round(cssHeight * dpr)));
		view.style.width = `${cssWidth}px`;
		if (view.width !== backingW || view.height !== backingH) {
			view.width = backingW;
			view.height = backingH;
		}
		state.cssScale = cssWidth / W;
		paint();
	}

	function schedulePaint() {
		if (state.raf || state.destroyed) return;
		const raf = win && win.requestAnimationFrame ? win.requestAnimationFrame.bind(win) : (fn) => setTimeout(fn, 16);
		state.raf = raf(paint);
	}

	function drawPreview(ctx, drag) {
		const W = state.work.width;
		const H = state.work.height;
		const k = state.cssScale > 0 ? state.cssScale : 1;
		const dash = [6 / k, 4 / k];
		if (drag.tool === "blur" || drag.tool === "crop") {
			const r = normRect(drag.start.x, drag.start.y, drag.end.x, drag.end.y);
			ctx.save();
			if (drag.tool === "crop") {
				ctx.fillStyle = "rgba(0, 0, 0, 0.5)";
				ctx.beginPath();
				ctx.rect(0, 0, W, H);
				ctx.rect(r.x, r.y, r.w, r.h);
				ctx.fill("evenodd");
			} else {
				ctx.fillStyle = "rgba(15, 23, 42, 0.45)";
				ctx.fillRect(r.x, r.y, r.w, r.h);
			}
			ctx.lineWidth = 2 / k;
			ctx.setLineDash(dash);
			ctx.strokeStyle = "#ffffff";
			ctx.strokeRect(r.x, r.y, r.w, r.h);
			ctx.lineDashOffset = dash[0];
			ctx.strokeStyle = "#111827";
			ctx.strokeRect(r.x, r.y, r.w, r.h);
			ctx.restore();
			return;
		}
		const op = buildOp(drag.tool, drag.start, drag.end, drag.points, { w: W, h: H }, 0);
		if (op) drawVector(ctx, op);
	}

	function paint() {
		state.raf = 0;
		if (!state.work || state.destroyed) return;
		const ctx = view.getContext("2d");
		if (!ctx) return;
		ctx.setTransform(1, 0, 0, 1, 0, 0);
		ctx.clearRect(0, 0, view.width, view.height);
		ctx.imageSmoothingEnabled = true;
		try {
			ctx.imageSmoothingQuality = "high";
		} catch (e) {
			// Not supported everywhere; the default is fine.
		}
		ctx.drawImage(state.work, 0, 0, view.width, view.height);
		if (state.drag && state.drag.moved) {
			const k = view.width / state.work.width;
			ctx.setTransform(k, 0, 0, k, 0, 0);
			drawPreview(ctx, state.drag);
		}
	}

	function commit(op) {
		if (!op || !state.work) return;
		state.history.push(op);
		const before = [state.work.width, state.work.height];
		state.work = applyOp(doc, state.work, op);
		if (state.work.width !== before[0] || state.work.height !== before[1]) layout();
		else paint();
		syncControls();
		notify();
	}

	function replay() {
		let canvas = cloneCanvas(doc, state.base);
		for (const op of state.history.list()) canvas = applyOp(doc, canvas, op);
		if (state.work && state.work !== canvas) release(state.work);
		state.work = canvas;
	}

	function undo() {
		if (state.destroyed || !state.work) return false;
		if (state.text) {
			closeText(false);
			return true;
		}
		const op = state.history.undo();
		if (!op) return false;
		replay();
		layout();
		syncControls();
		notify();
		return true;
	}

	function setTool(tool) {
		if (!TOOLS.includes(tool)) return;
		closeText(true);
		state.tool = tool;
		syncControls();
	}

	// ---- text: an inline input over the canvas, committed on Enter or when focus leaves.
	// Inline rather than window.prompt(): a prompt is a blocking system sheet on a kiosk, and
	// it puts the text somewhere other than where it will be drawn.

	function openText(point) {
		closeText(true);
		if (!state.work) return;
		const W = state.work.width;
		const H = state.work.height;
		const size = textSize(W, H);
		const box = view.getBoundingClientRect();
		const scaleX = box.width / W || state.cssScale;
		const input = node(doc, "input", "ee-cap-ann-text");
		input.type = "text";
		input.maxLength = 200;
		input.setAttribute("aria-label", "Text to add to the image");
		input.style.left = `${view.offsetLeft + point.x * scaleX}px`;
		input.style.top = `${view.offsetTop + point.y * scaleX}px`;
		// Never under 16px: iOS zooms the page into any smaller input, which throws the
		// panel off-screen mid-annotation.
		input.style.fontSize = `${Math.max(16, Math.round(size * scaleX))}px`;
		input.addEventListener("keydown", (ev) => {
			if (ev.key === "Enter") {
				ev.preventDefault();
				ev.stopPropagation();
				closeText(true);
				focusCanvas();
			} else if (ev.key === "Escape") {
				// Stopped here so Escape abandons the text, not the whole report.
				ev.preventDefault();
				ev.stopPropagation();
				closeText(false);
				focusCanvas();
			}
		});
		input.addEventListener("blur", () => closeText(true));
		stage.appendChild(input);
		state.text = { input, at: { x: point.x, y: point.y }, size };
		setTimeout(() => {
			try {
				if (state.text && state.text.input === input) input.focus();
			} catch (e) {
				// Focus can fail on a node being removed; the text is simply abandoned.
			}
		}, 0);
	}

	function closeText(keep) {
		const editor = state.text;
		if (!editor) return;
		state.text = null;
		const value = String(editor.input.value || "").trim();
		editor.input.remove();
		if (keep && value) commit({ type: "text", at: editor.at, text: value.slice(0, 200), size: editor.size });
	}

	function focusCanvas() {
		try {
			view.focus({ preventScroll: true });
		} catch (e) {
			// Focus is a courtesy here.
		}
	}

	// ---- pointer handling

	function pointFrom(ev) {
		return toImagePoint(ev.clientX, ev.clientY, view.getBoundingClientRect(), state.work.width, state.work.height);
	}

	listen(view, "pointerdown", (ev) => {
		if (!state.work || state.destroyed) return;
		if (!ev.isPrimary || (ev.pointerType === "mouse" && ev.button !== 0)) return;
		if (state.text) {
			// A tap outside the text box finishes the text rather than starting a new mark.
			closeText(true);
			ev.preventDefault();
			return;
		}
		ev.preventDefault();
		const p = pointFrom(ev);
		state.drag = { tool: state.tool, pointerId: ev.pointerId, start: p, end: p, points: [p], moved: false };
		try {
			view.setPointerCapture(ev.pointerId);
		} catch (e) {
			// Without capture a drag that leaves the canvas ends at its edge; still usable.
		}
	});

	listen(view, "pointermove", (ev) => {
		const drag = state.drag;
		if (!drag || ev.pointerId !== drag.pointerId) return;
		ev.preventDefault();
		const p = pointFrom(ev);
		drag.end = p;
		if (drag.tool === "pen") {
			const last = drag.points[drag.points.length - 1];
			if (Math.hypot(p.x - last.x, p.y - last.y) >= 1) drag.points.push(p);
		}
		drag.moved = true;
		schedulePaint();
	});

	function endDrag(ev, canceled) {
		const drag = state.drag;
		if (!drag || (ev && ev.pointerId !== drag.pointerId)) return;
		state.drag = null;
		try {
			view.releasePointerCapture(drag.pointerId);
		} catch (e) {
			// Already released.
		}
		if (canceled || !state.work) {
			paint();
			return;
		}
		if (drag.tool === "text") {
			openText(drag.start);
			paint();
			return;
		}
		const op = buildOp(
			drag.tool,
			drag.start,
			drag.end,
			drag.points,
			{ w: state.work.width, h: state.work.height },
			Math.floor(Math.random() * 4294967296)
		);
		if (op) commit(op);
		else paint();
	}

	listen(view, "pointerup", (ev) => endDrag(ev, false));
	listen(view, "pointercancel", (ev) => endDrag(ev, true));

	listen(root, "keydown", (ev) => {
		const key = String(ev.key || "").toLowerCase();
		if ((ev.ctrlKey || ev.metaKey) && !ev.shiftKey && !ev.altKey && key === "z") {
			const tag = ev.target && ev.target.tagName;
			if (tag === "INPUT" || tag === "TEXTAREA") return;
			ev.preventDefault();
			undo();
		}
	});

	// Layout is always deferred to the next frame and only re-run when the width really
	// changed. Resizing the canvas inside a ResizeObserver callback changes the stage's height
	// in the same frame, and the browser reports that as a "ResizeObserver loop" error on
	// window.onerror — which the recorder would then dutifully file as a page error.
	function scheduleLayout() {
		if (state.layoutRaf || state.destroyed) return;
		const raf = win && win.requestAnimationFrame ? win.requestAnimationFrame.bind(win) : (fn) => setTimeout(fn, 16);
		state.layoutRaf = raf(layout);
	}

	listen(win, "resize", scheduleLayout);
	if (win && typeof win.ResizeObserver === "function") {
		try {
			const observer = new win.ResizeObserver(() => {
				if (stage.clientWidth !== state.lastWidth) scheduleLayout();
			});
			observer.observe(stage);
			cleanups.push(() => observer.disconnect());
		} catch (e) {
			// The window resize listener above still covers the common case.
		}
	}

	syncControls();

	const ready = loadSource(doc, imageSource).then((source) => {
		if (state.destroyed) return;
		const natural = sourceSize(source);
		const size = exportSize(natural.w, natural.h, opts.maxEdge);
		if (!size.w || !size.h) throw new Error("That image is empty.");
		const base = makeCanvas(doc, size.w, size.h);
		const ctx = base.getContext("2d");
		if (!ctx) throw new Error("This browser cannot edit images.");
		ctx.imageSmoothingEnabled = true;
		try {
			ctx.imageSmoothingQuality = "high";
		} catch (e) {
			// Optional.
		}
		ctx.drawImage(source, 0, 0, size.w, size.h);
		if (typeof source.close === "function") {
			try {
				source.close();
			} catch (e) {
				// An ImageBitmap we made; closing it only frees memory sooner.
			}
		}
		state.base = base;
		state.work = cloneCanvas(doc, base);
		loading.remove();
		layout();
		syncControls();
	});
	ready.catch((err) => {
		if (state.destroyed) return;
		loading.textContent = (err && err.message) || "That image could not be opened.";
		loading.classList.add("ee-cap-ann-failed");
	});

	/**
	 * The flattened PNG. If it is larger than `maxBytes` (a phone photo exported as PNG can
	 * be), it is re-exported smaller rather than failing the upload — the blur is already in
	 * the pixels, so scaling down only hides it further.
	 */
	async function toBlob(options) {
		await ready;
		if (state.destroyed || !state.work) throw new Error("The image is no longer available.");
		closeText(true);
		const maxBytes = options && options.maxBytes !== undefined ? options.maxBytes : DEFAULT_MAX_BYTES;
		const source = state.work;
		let scale = 1;
		let blob = null;
		for (let attempt = 0; attempt < 6; attempt++) {
			let canvas = source;
			if (scale !== 1) {
				canvas = makeCanvas(doc, source.width * scale, source.height * scale);
				const ctx = canvas.getContext("2d");
				ctx.imageSmoothingEnabled = true;
				ctx.drawImage(source, 0, 0, canvas.width, canvas.height);
			}
			try {
				blob = await canvasToBlob(canvas, "image/png");
			} finally {
				if (canvas !== source) release(canvas);
			}
			if (!(maxBytes > 0) || blob.size <= maxBytes) return blob;
			if (Math.max(source.width, source.height) * scale * 0.75 < 640) break;
			scale *= 0.75;
		}
		// Still too large at 640px: send it anyway and let the server's own limit speak.
		return blob;
	}

	function destroy() {
		if (state.destroyed) return;
		state.destroyed = true;
		for (const handle of [state.raf, state.layoutRaf]) {
			if (!handle) continue;
			try {
				if (win && win.cancelAnimationFrame) win.cancelAnimationFrame(handle);
				else clearTimeout(handle);
			} catch (e) {
				// Nothing left to cancel.
			}
		}
		if (state.text) {
			state.text.input.remove();
			state.text = null;
		}
		for (const fn of cleanups.splice(0)) {
			try {
				fn();
			} catch (e) {
				// Keep tearing down.
			}
		}
		release(state.work);
		release(state.base);
		release(view);
		state.work = null;
		state.base = null;
		state.history.clear();
		root.remove();
	}

	return {
		ready,
		toBlob,
		destroy,
		undo,
		setTool,
		hasEdits: () => state.history.size > 0,
		size: () => (state.work ? { w: state.work.width, h: state.work.height } : { w: 0, h: 0 }),
	};
}

/**
 * The annotator's rules, for the panel to fold into its single injected <style>. Kept here
 * so the class names and the rules that depend on them change together.
 */
export const ANNOTATOR_CSS = `
.ee-cap-ann { display: flex; flex-direction: column; gap: 8px; }
.ee-cap-ann-toolbar { display: flex; flex-wrap: wrap; gap: 6px; }
.ee-cap-ann .ee-cap-tool { min-width: var(--ee-cap-tap); padding: 0 12px; }
.ee-cap-ann .ee-cap-tool[aria-pressed="true"] { background: var(--ee-cap-accent); border-color: var(--ee-cap-accent); color: var(--ee-cap-accent-fg); }
.ee-cap-ann .ee-cap-ann-undo { margin-left: auto; }
.ee-cap-ann-hint { margin: 0; font-size: 13px; color: var(--ee-cap-muted); }
.ee-cap-ann-stage { position: relative; display: flex; justify-content: center; align-items: flex-start; width: 100%; min-height: 120px; padding: 8px; border-radius: 8px; border: 1px solid var(--ee-cap-border); background: var(--ee-cap-subtle); overflow: hidden; }
.ee-cap-ann-canvas { display: block; max-width: 100%; height: auto; touch-action: none; cursor: crosshair; border-radius: 2px; box-shadow: 0 1px 3px rgba(0, 0, 0, 0.25); }
.ee-cap-ann-canvas[data-tool="text"] { cursor: text; }
.ee-cap-ann-canvas:focus-visible { outline: 3px solid var(--ee-cap-focus); outline-offset: 2px; }
.ee-cap-ann-loading { margin: auto; padding: 24px 0; color: var(--ee-cap-muted); }
.ee-cap-ann-failed { color: var(--ee-cap-danger); }
.ee-cap-ann-text { position: absolute; z-index: 1; min-width: 8em; max-width: calc(100% - 16px); margin: 0; padding: 0 4px; line-height: 1.2; font-weight: 600; font-family: inherit; color: ${INK}; background: rgba(255, 255, 255, 0.92); border: 2px dashed ${INK}; border-radius: 4px; outline: none; }
`;
