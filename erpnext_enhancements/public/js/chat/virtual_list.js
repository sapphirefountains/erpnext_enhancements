/**
 * The virtualised transcript. Windowed rendering with dynamic item heights.
 *
 * WHY PURPOSE-BUILT. §4.13's target is ≤~80 message nodes retained at rest regardless of
 * scroll depth, in a room of 5,000 messages. Every off-the-shelf virtual list worth using is
 * a framework component, and this application deliberately ships no framework — the SPA is
 * plain DOM precisely so there cannot be a second Vue runtime on any page (§4.1). So this is
 * ~200 lines of windowing rather than a dependency, and the shape it has to handle is the
 * awkward one: chat messages are not fixed height, and an image changes its row's height
 * AFTER load.
 *
 * TWO INVARIANTS, both of which naive implementations break:
 *
 * 1. **The scroll anchor is a message docname, never a pixel offset.** Prepending a page of
 *    history changes every offset above the viewport; anchoring on an element and restoring
 *    its position afterwards is the only thing that keeps the reader's eyes where they were.
 *    The same mechanism handles an image finishing loading, which is the single most common
 *    cause of "it jumped while I was reading".
 * 2. **Measured heights are cached per message and never guessed twice.** A row whose height
 *    is estimated, rendered, and then measured must not be re-estimated on the next pass, or
 *    the list oscillates: measuring changes the total, which changes the window, which
 *    re-renders, which re-measures.
 */

/** Estimated row height before a row has ever been measured. */
const ESTIMATED_ROW_PX = 64;

/** Extra rows rendered above and below the viewport, so a scroll does not show blank space
 *  before the next frame. Six each way is roughly one flick on a phone. */
const OVERSCAN = 6;

export class VirtualTranscript {
	/**
	 * @param {HTMLElement} viewport the scrolling element
	 * @param {HTMLElement} canvas   the inner element rows are appended to
	 * @param {function(object, number): HTMLElement} renderRow
	 */
	constructor(viewport, canvas, renderRow) {
		this.viewport = viewport;
		this.canvas = canvas;
		this.renderRow = renderRow;
		this.items = [];
		/** docname -> measured px */
		this.heights = new Map();
		/** docname -> live node, for the rows currently in the window */
		this.nodes = new Map();
		this.spacerTop = document.createElement("div");
		this.spacerBottom = document.createElement("div");
		this.spacerTop.className = "ee-vt-spacer";
		this.spacerBottom.className = "ee-vt-spacer";
		this.canvas.appendChild(this.spacerTop);
		this.canvas.appendChild(this.spacerBottom);
		this._raf = null;
		this._observer = null;

		this.viewport.addEventListener("scroll", () => this.schedule(), { passive: true });

		if (typeof ResizeObserver !== "undefined") {
			// Rows whose height changes after render — an image loading, a long word wrapping
			// on a resize — re-measure themselves and correct the model. Without this the
			// spacers are wrong by exactly the image's height and the scrollbar lies.
			this._observer = new ResizeObserver((entries) => {
				let changed = false;
				for (const entry of entries) {
					const key = entry.target.dataset.messageName;
					if (!key) continue;
					const height = entry.target.offsetHeight;
					if (height && this.heights.get(key) !== height) {
						this.heights.set(key, height);
						changed = true;
					}
				}
				if (changed) this.schedule();
			});
		}
	}

	/**
	 * Replace the item list, preserving the reader's position.
	 *
	 * `anchor` is `{name, ratio}` — a message docname and where its top edge should sit as a
	 * fraction of viewport height. Passing it is what makes prepending a page of scrollback
	 * invisible.
	 */
	setItems(items, anchor) {
		this.items = items || [];
		// Drop measurements for rows that are gone, or the map grows without bound over a long
		// session of scrolling through 5,000 messages.
		const live = new Set(this.items.map((i) => i.name).filter(Boolean));
		for (const key of Array.from(this.heights.keys())) {
			if (!live.has(key)) this.heights.delete(key);
		}
		this.render(anchor);
	}

	schedule() {
		if (this._raf != null) return;
		this._raf = requestAnimationFrame(() => {
			this._raf = null;
			this.render();
		});
	}

	/** Height of one row: measured if we have ever seen it, estimated otherwise. */
	heightOf(item) {
		return this.heights.get(item.name) || ESTIMATED_ROW_PX;
	}

	/** Distance from the top of the list to the top of `index`. */
	offsetOf(index) {
		let total = 0;
		for (let i = 0; i < index && i < this.items.length; i++) total += this.heightOf(this.items[i]);
		return total;
	}

	render(anchor) {
		const viewportHeight = this.viewport.clientHeight || 0;
		const scrollTop = this.viewport.scrollTop;

		// Walk once, accumulating offsets, to find the window. O(n) over the item list rather
		// than O(n) over the DOM — 5,000 numbers is nothing; 5,000 elements is the problem.
		let start = 0;
		let end = this.items.length;
		let acc = 0;
		let startOffset = 0;
		for (let i = 0; i < this.items.length; i++) {
			const h = this.heightOf(this.items[i]);
			if (acc + h < scrollTop && i + OVERSCAN < this.items.length) {
				start = i + 1;
				startOffset = acc + h;
			}
			if (acc > scrollTop + viewportHeight) {
				end = i;
				break;
			}
			acc += h;
		}
		start = Math.max(0, start - OVERSCAN);
		end = Math.min(this.items.length, end + OVERSCAN);
		startOffset = this.offsetOf(start);

		const window_ = this.items.slice(start, end);
		const wanted = new Set(window_.map((i) => i.name));

		// Remove rows that left the window.
		for (const [key, node] of Array.from(this.nodes)) {
			if (!wanted.has(key)) {
				if (this._observer) this._observer.unobserve(node);
				node.remove();
				this.nodes.delete(key);
			}
		}

		// Insert/move rows that are in it, in order, before the bottom spacer.
		let cursor = this.spacerBottom;
		for (let i = window_.length - 1; i >= 0; i--) {
			const item = window_[i];
			let node = this.nodes.get(item.name);
			if (!node) {
				node = this.renderRow(item, start + i);
				node.dataset.messageName = item.name || "";
				this.nodes.set(item.name, node);
				this.canvas.insertBefore(node, cursor);
				if (this._observer) this._observer.observe(node);
				// First measurement, immediately: an unmeasured row makes the spacers wrong for
				// one frame, which is visible as a small jump on every scrollback page.
				const height = node.offsetHeight;
				if (height) this.heights.set(item.name, height);
			} else if (node.nextSibling !== cursor) {
				this.canvas.insertBefore(node, cursor);
			}
			cursor = node;
		}

		const total = this.offsetOf(this.items.length);
		const rendered = window_.reduce((sum, item) => sum + this.heightOf(item), 0);
		this.spacerTop.style.height = startOffset + "px";
		this.spacerBottom.style.height = Math.max(0, total - startOffset - rendered) + "px";

		if (anchor && anchor.name) this.scrollToAnchor(anchor.name, anchor.ratio);
	}

	/** Put `name` back at `ratio` of the viewport height. The scroll-restoration primitive. */
	scrollToAnchor(name, ratio) {
		const index = this.items.findIndex((item) => item.name === name);
		if (index < 0) return false;
		const offset = this.offsetOf(index);
		const target = offset - (this.viewport.clientHeight || 0) * (typeof ratio === "number" ? ratio : 0);
		this.viewport.scrollTop = Math.max(0, target);
		// One more pass: the rows now in the window may measure differently from their
		// estimates, which moves the target. Two passes converges in every case seen; a loop
		// would be a loop.
		this.render();
		const corrected = this.offsetOf(this.items.findIndex((item) => item.name === name));
		this.viewport.scrollTop = Math.max(
			0,
			corrected - (this.viewport.clientHeight || 0) * (typeof ratio === "number" ? ratio : 0)
		);
		return true;
	}

	scrollToBottom() {
		this.viewport.scrollTop = this.viewport.scrollHeight;
		this.render();
		this.viewport.scrollTop = this.viewport.scrollHeight;
	}

	atBottom(slackPx = 80) {
		return (
			this.viewport.scrollHeight - this.viewport.scrollTop - this.viewport.clientHeight <= slackPx
		);
	}

	/** The message nearest the top of the viewport, and where its top edge sits. The handoff
	 *  record's scroll anchor comes from here. */
	currentAnchor() {
		const scrollTop = this.viewport.scrollTop;
		let acc = 0;
		for (const item of this.items) {
			const h = this.heightOf(item);
			if (acc + h > scrollTop) {
				const ratio = (acc - scrollTop) / Math.max(1, this.viewport.clientHeight);
				return { name: item.name, ratio: Math.max(0, Math.min(1, ratio)) };
			}
			acc += h;
		}
		return null;
	}

	/** Rows currently in the DOM. The §4.13 acceptance number is measured off this. */
	renderedCount() {
		return this.nodes.size;
	}

	destroy() {
		if (this._raf != null) cancelAnimationFrame(this._raf);
		if (this._observer) this._observer.disconnect();
		this.nodes.clear();
		this.heights.clear();
	}
}
