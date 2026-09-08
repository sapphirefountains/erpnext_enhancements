// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt
//
// Training Canvas (/app/training-canvas?course=…) — a full-bleed WYSIWYG spike.
//
// The classic Training Builder (/app/training-builder) edits a lesson as a list of
// summary cards with the real controls in a right-hand inspector, alongside a
// separate "Preview as learner" pane. This page collapses those two surfaces into
// one: it renders every block with the REAL learner renderer
// (public/js/training/blocks.js -> TR.renderBlock) and lets the author edit the
// text ON that render — so the thing you edit is the thing a learner sees, in the
// learner's own stylesheet.
//
// It is a spike: it proves the direction on the real data path (same
// get_builder_bootstrap load, same save_draft_version autosave, same optimistic
// lock) for the text block types (Rich Text, Callout) and block/lesson headings.
// Media and in-video blocks need a signed draft asset URL this page does not mint,
// so they render as a placeholder that hands off to the classic builder. The
// classic builder remains the complete authoring surface; nothing here replaces it.
//
// Decisions that are load-bearing, not preferences:
//
// 1. **The renderer is the learner's, unmodified.** blocks.js is frappe-free and
//    designed to run outside the player; we build a partial `ctx` (translate only)
//    and mirror training_author._split_lesson to turn the draft's edit shape
//    (content=HTML, data=JSON, callout_tone) into the render shape blocks.js reads.
//    A second renderer would drift from what publishes.
//
// 2. **Saves send the WHOLE block table, every field, block_key carried.** The
//    server replaces the child table by position (_apply_blocks). A dropped or
//    regenerated block_key strands learner watch-progress and orphans checkpoints;
//    a dropped `data`/`callout_tone` blanks interactive content. get_builder_bootstrap
//    was extended to return data/callout_tone precisely so this round-trips.
//
// 3. **The optimistic lock is the version's `modified`.** Hold it, send it, adopt
//    the token the save returns — the next save is rejected against the old one.
//
// Class names come from training_canvas.css and are not invented here.

frappe.pages["training-canvas"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Training Canvas"),
		single_column: true,
	});
	// The hook the stylesheet keys its edge-to-edge overrides off — scoped so they
	// cannot leak into any other desk page.
	$(wrapper).addClass("training-canvas-fullbleed");
	wrapper.training_canvas = new TrainingCanvas(page, wrapper);
};

frappe.pages["training-canvas"].on_page_show = function (wrapper) {
	if (wrapper.training_canvas) wrapper.training_canvas.handle_route();
};

// Only the two the canvas needs: the learner stylesheet and the block renderer.
// video.js / quiz.js / player.js are the runtime; this page renders blocks only.
const TC_ASSETS = [
	"/assets/erpnext_enhancements/css/training/player.css",
	"/assets/erpnext_enhancements/js/training/blocks.js",
];

// Mirrors BLOCK_ALLOWED_FIELDS in api/training_author.py. A field sent that the
// server does not allow is silently dropped (reported back in `rejected`); a field
// omitted from a whole-table replace is blanked. So this list is the contract.
const TC_BLOCK_FIELDS = [
	"block_key",
	"block_type",
	"heading",
	"content",
	"image",
	"file",
	"video_asset",
	"embed_url",
	"poster_image",
	"caption",
	"required_for_completion",
	"min_coverage_percent",
	"checkpoints_enabled",
	"data",
	"callout_tone",
];

// Blocks that need a signed draft asset URL (or the video telemetry runtime) the
// canvas spike does not provide — rendered as a hand-off placeholder.
const TC_MEDIA_TYPES = ["Image", "Video", "PDF", "Downloadable File", "External Embed", "Image Hotspots"];

// The block types whose body is author HTML the canvas edits in place.
const TC_EDITABLE_HTML = { "Rich Text": true, Callout: true };

const TC_SAVE_DEBOUNCE_MS = 1200;

class TrainingCanvas {
	constructor(page, wrapper) {
		this.page = page;
		this.wrapper = wrapper;
		this.$body = $(page.body);
		this.reset();
		this.build_chrome();
		this.page.set_secondary_action(__("Reload"), () => this.reload());
		this.page.add_menu_item(__("Open classic builder"), () => this.open_classic());
	}

	reset() {
		this.course = null;
		this.version = null;
		this.chapters = [];
		this.lessons = [];
		this.lesson_name = null;
		this.dirty = {};
		this._saving = false;
		this._conflict = false;
		clearTimeout(this._save_timer);
	}

	// ---------------------------------------------------------------- chrome
	build_chrome() {
		this.$body.empty();
		this.$app = $(`
			<div class="tc-app">
				<div class="tc-bar">
					<select class="tc-lessons form-control" aria-label="${__("Lesson")}"></select>
					<span class="tc-spacer"></span>
					<span class="tc-status" role="status" aria-live="polite">
						<span class="tc-pip"></span><span class="tc-status-text">${__("Saved")}</span>
					</span>
					<button class="btn btn-default btn-sm tc-classic">${__("Classic builder")}</button>
				</div>
				<div class="tc-scroll">
					<div class="tc-sheet">
						<div class="tc-eyebrow"></div>
						<h1 class="tc-title" contenteditable="false" spellcheck="false"></h1>
						<div class="tr-shell"><div class="tc-blocks"></div></div>
						<div class="tc-add"></div>
					</div>
				</div>
			</div>
		`).appendTo(this.$body);

		this.$lessons = this.$app.find(".tc-lessons");
		this.$status = this.$app.find(".tc-status");
		this.$sheet = this.$app.find(".tc-sheet");
		this.$title = this.$app.find(".tc-title");
		this.$blocks = this.$app.find(".tc-blocks");
		this.$add = this.$app.find(".tc-add");

		this.$lessons.on("change", () => this.select_lesson(this.$lessons.val()));
		this.$app.find(".tc-classic").on("click", () => this.open_classic());
		this.$title.on("input", () => {
			const lesson = this.current_lesson();
			if (!lesson || !this.editable()) return;
			lesson.lesson_title = this.$title.text();
			this.dirty_lesson(lesson).lesson_title = lesson.lesson_title;
			this.mark_dirty();
		});
	}

	open_classic() {
		const course = this.course && this.course.name;
		frappe.set_route("training-builder", course ? { course } : {});
	}

	// ----------------------------------------------------------------- route
	handle_route() {
		let course = frappe.utils.get_url_arg("course");
		if (frappe.route_options && frappe.route_options.course) {
			course = course || frappe.route_options.course;
			frappe.route_options = null;
		}
		if (course && (!this.course || this.course.name !== course)) {
			this.load(course);
		} else if (!course && !this.course) {
			this.render_course_picker();
		}
	}

	reload() {
		const course = this.course && this.course.name;
		const keep = this.lesson_name;
		this.reset();
		this.lesson_name = keep;
		if (course) this.load(course);
		else this.render_course_picker();
	}

	// ----------------------------------------------------------------- load
	load_assets() {
		if (this._assets) return this._assets;
		const version = (frappe.boot.versions && frappe.boot.versions.erpnext_enhancements) || "0";
		this._assets = Promise.all(
			TC_ASSETS.map((path) => tc_load_asset(path + "?v=" + encodeURIComponent(version)))
		).catch((error) => {
			this._assets = null;
			throw error;
		});
		return this._assets;
	}

	load(course) {
		this.$blocks.html(`<div class="tc-empty">${__("Loading…")}</div>`);
		Promise.all([
			this.load_assets(),
			frappe.call({
				method: "erpnext_enhancements.api.training_author.get_builder_bootstrap",
				args: { course },
			}),
		])
			.then(([, r]) => {
				this.apply_bootstrap((r && r.message) || {});
				this.render();
			})
			.catch(() => {
				// The endpoint gates on Training Course write permission and throws
				// for anyone without it, so "could not load" and "not allowed" are the
				// same door.
				this.$blocks.html(
					`<div class="tc-empty"><h2>${__("Cannot open this course")}</h2>${__(
						"You need author access to its draft, and the course must exist."
					)}</div>`
				);
			});
	}

	apply_bootstrap(data) {
		this.course = data.course || null;
		this.version = data.version || null;
		this.chapters = data.chapters || [];
		this.lessons = (data.lessons || []).map((lesson) => {
			lesson.blocks = lesson.blocks || [];
			return lesson;
		});
		if (!this.lesson_name || !this.lesson(this.lesson_name)) {
			this.lesson_name = this.lessons.length ? this.lessons[0].name : null;
		}
	}

	lesson(name) {
		return this.lessons.find((l) => l.name === name) || null;
	}

	current_lesson() {
		return this.lesson(this.lesson_name);
	}

	editable() {
		return !!(this.version && this.version.docstatus === 0 && !this._conflict);
	}

	// --------------------------------------------------------------- render
	render() {
		this.$add.empty();
		if (!this.course) return this.render_course_picker();
		if (!this.version) return this.render_no_draft();

		this.$app.find(".tc-eyebrow").text(this.course.course_title || this.course.name || "");
		this.$lessons.empty();
		this.lessons.forEach((lesson) => {
			$("<option></option>")
				.attr("value", lesson.name)
				.text(lesson.lesson_title || __("Untitled lesson"))
				.appendTo(this.$lessons);
		});
		this.$lessons.val(this.lesson_name || "");
		this.$title.attr("contenteditable", this.editable() ? "true" : "false");
		this.render_sheet();
		this.paint_status(this.has_dirty() ? "dirty" : "saved");
	}

	render_course_picker() {
		this.$app.find(".tc-eyebrow").text("");
		this.$title.text(__("Training Canvas"));
		this.$blocks.html(
			`<div class="tc-empty"><h2>${__("Open a course to edit")}</h2>${__(
				"Open a Training Course and choose Edit on the canvas, or add ?course=… to the URL."
			)}<br><a href="/app/training-course">${__("Open the course list")}</a></div>`
		);
	}

	render_no_draft() {
		this.$app.find(".tc-eyebrow").text(this.course.course_title || "");
		this.$title.text("");
		this.$blocks.html(
			`<div class="tc-empty"><h2>${__("No open draft")}</h2>${__(
				"This course has no draft version open for editing. Create one in the classic builder, then come back to the canvas."
			)}<br><button class="btn btn-primary btn-sm tc-open-classic" style="margin-top:12px">${__(
				"Open classic builder"
			)}</button></div>`
		);
		this.$blocks.find(".tc-open-classic").on("click", () => this.open_classic());
	}

	render_sheet() {
		const lesson = this.current_lesson();
		this.$title.text(lesson ? lesson.lesson_title || "" : "");
		this.$blocks.empty();
		if (!lesson) return;
		if (!lesson.blocks.length) {
			this.$blocks.append(
				$('<div class="tc-empty"></div>').text(__("This lesson has no blocks yet. Add one in the classic builder."))
			);
		}
		lesson.blocks.forEach((block) => {
			if (TC_MEDIA_TYPES.indexOf(block.block_type) !== -1) {
				this.$blocks.append(this.placeholder(block));
				return;
			}
			let node;
			try {
				node = window.TR.renderBlock(this.to_render_block(block), this.render_ctx());
			} catch (e) {
				node = document.createElement("div");
				node.className = "tc-empty";
				node.textContent = __("This block could not be rendered.");
			}
			this.$blocks.append(this.decorate(lesson, block, node));
		});
		this.render_add_row();
	}

	render_add_row() {
		this.$add.empty();
		$("<button></button>")
			.text(__("Add or reorder blocks in the classic builder →"))
			.on("click", () => this.open_classic())
			.appendTo(this.$add);
	}

	// A partial ctx: translate only. No transport/mediaUrl — text and the
	// no-media interactive blocks (Checklist/Flashcards/Accordion) render fully;
	// media blocks never reach here (placeholder above).
	render_ctx() {
		return { t: (text) => __(text) };
	}

	// edit shape -> learner render shape, mirroring training_author._split_lesson /
	// the classic builder's preview_lesson. HTML is sanitised the way publish will
	// sanitise it, so the canvas never renders anything the learner would not.
	to_render_block(block) {
		return Object.assign(
			{
				block_key: block.block_key,
				type: block.block_type,
				heading: block.heading || "",
				html: frappe.utils.xss_sanitise(block.content || ""),
				caption: block.caption || "",
				image: block.image || "",
				file: block.file || "",
				embed_url: block.embed_url || "",
				poster: block.poster_image || "",
				duration_s: 0,
				required: 0,
				min_coverage: 0,
				checkpoints_enabled: 0,
			},
			this.interactive_keys(block)
		);
	}

	// Mirrors training_author._augment_interactive_block.
	interactive_keys(block) {
		const type = block.block_type;
		if (type === "Callout") {
			const tone = (block.callout_tone || "").trim().toLowerCase();
			return ["tip", "warning", "danger", "info"].indexOf(tone) !== -1 ? { tone } : {};
		}
		let data = {};
		try {
			data = JSON.parse(block.data || "{}") || {};
		} catch (e) {
			data = {};
		}
		if (type === "Checklist") {
			return { items: (data.items || []).map((x) => String(x)).filter((x) => x.trim()) };
		}
		if (type === "Flashcards") {
			return {
				cards: (data.cards || [])
					.filter((c) => c && typeof c === "object")
					.map((c) => ({ front: String(c.front || ""), back: String(c.back || "") })),
			};
		}
		if (type === "Accordion") {
			return {
				panels: (data.panels || [])
					.filter((p) => p && typeof p === "object")
					.map((p) => ({ title: String(p.title || ""), body: frappe.utils.xss_sanitise(String(p.body || "")) })),
			};
		}
		return {};
	}

	placeholder(block) {
		const $node = $(`
			<div class="tc-placeholder">
				<span class="tc-ph-icon">🧩</span>
				<div class="tc-ph-body"><b>${frappe.utils.escape_html(block.block_type)}</b>${
			block.heading ? " — " + frappe.utils.escape_html(block.heading) : ""
		}<br>${__("Media and in-video blocks are edited in the classic builder.")}</div>
				<button class="btn btn-default btn-xs">${__("Edit")}</button>
			</div>
		`);
		$node.find("button").on("click", () => this.open_classic());
		return $node;
	}

	// Layer editing onto the real render. Heading is author free-text on every card
	// type; the body is editable HTML only for Rich Text / Callout.
	decorate(lesson, block, node) {
		node.setAttribute("data-block-key", block.block_key || "");
		const editable = this.editable() && !!TC_EDITABLE_HTML[block.block_type];

		const tag = document.createElement("span");
		tag.className = "tc-tag" + (editable ? "" : " is-ro");
		tag.textContent = block.block_type + (editable ? "" : " · " + __("read-only"));
		node.appendChild(tag);

		const heading = node.querySelector(".tr-block-heading");
		if (heading && this.editable()) {
			heading.setAttribute("contenteditable", "true");
			heading.setAttribute("spellcheck", "false");
			heading.addEventListener("input", () => {
				block.heading = heading.textContent;
				this.dirty_blocks(lesson);
				this.mark_dirty();
			});
		}

		if (editable) {
			node.classList.add("tc-editable");
			const body = node.querySelector(".tr-block-body");
			if (body) {
				body.setAttribute("contenteditable", "true");
				body.setAttribute("spellcheck", "false");
				body.addEventListener("focus", () => node.classList.add("is-editing"));
				body.addEventListener("blur", () => node.classList.remove("is-editing"));
				body.addEventListener("input", () => {
					block.content = body.innerHTML;
					this.dirty_blocks(lesson);
					this.mark_dirty();
				});
			}
		}
		return node;
	}

	// --------------------------------------------------------- dirty + save
	dirty_lesson(lesson) {
		this.dirty[lesson.name] = this.dirty[lesson.name] || { name: lesson.name };
		return this.dirty[lesson.name];
	}

	// Stage the WHOLE block table for this lesson — the server replaces it by
	// position, so a partial list is data loss.
	dirty_blocks(lesson) {
		this.dirty_lesson(lesson).blocks = lesson.blocks.map((block) => {
			const row = {};
			TC_BLOCK_FIELDS.forEach((field) => {
				if (block[field] !== undefined) row[field] = block[field];
			});
			return row;
		});
	}

	has_dirty() {
		return Object.keys(this.dirty).length > 0;
	}

	mark_dirty() {
		this.paint_status("dirty");
		clearTimeout(this._save_timer);
		this._save_timer = setTimeout(() => this.save(), TC_SAVE_DEBOUNCE_MS);
	}

	save() {
		clearTimeout(this._save_timer);
		if (!this.editable() || this._saving || !this.has_dirty()) return Promise.resolve();
		const sent = this.dirty;
		this.dirty = {};
		this._saving = true;
		this.paint_status("saving");
		return frappe
			.call({
				method: "erpnext_enhancements.api.training_author.save_draft_version",
				args: {
					course_version: this.version.name,
					payload: JSON.stringify({ lessons: Object.values(sent) }),
					modified: this.version.modified,
				},
			})
			.then((r) => {
				const state = (r && r.message) || {};
				this._saving = false;
				// Adopting the returned token is mandatory: the next save is rejected
				// against the old one.
				if (state.modified) this.version.modified = state.modified;
				this.report_rejected(state.rejected);
				this.paint_status(this.has_dirty() ? "dirty" : "saved");
				if (this.has_dirty()) this.mark_dirty();
				return state;
			})
			.catch((error) => {
				this._saving = false;
				// Put the unsaved edits back; a field the author changed since is
				// newer and overwrites on merge.
				Object.entries(sent).forEach(([name, patch]) => {
					this.dirty[name] = Object.assign(patch, this.dirty[name] || {});
				});
				const message = String((error && error.message) || "");
				if (/modif|stale|out of date|newer|conflict/i.test(message)) this.enter_conflict();
				else this.paint_status("dirty");
				throw error;
			});
	}

	report_rejected(rejected) {
		if (!rejected || !rejected.length) return;
		// Not an error — but the only signal a field was silently dropped. Surface it.
		const fields = rejected.map((r) => r.field).filter(Boolean);
		frappe.show_alert(
			{ message: __("Some changes were not saved: {0}", [fields.join(", ")]), indicator: "orange" },
			7
		);
	}

	enter_conflict() {
		this._conflict = true;
		this.paint_status("conflict");
		if (this.$app.find(".tc-banner").length) return;
		const $banner = $(`
			<div class="tc-banner" role="alert">
				<span>${__("This draft changed somewhere else. Reload to get the latest — your unsaved edits here will be lost.")}</span>
				<button class="btn btn-primary btn-xs">${__("Reload")}</button>
			</div>
		`);
		$banner.find("button").on("click", () => this.reload());
		this.$app.find(".tc-bar").after($banner);
	}

	// -------------------------------------------------------------- status
	select_lesson(name) {
		if (name === this.lesson_name) return;
		// Flush any pending edit before leaving the lesson.
		this.save();
		this.lesson_name = name;
		this.render_sheet();
	}

	paint_status(kind) {
		const label = { saved: __("Saved"), dirty: __("Editing…"), saving: __("Saving…"), conflict: __("Out of date") };
		this.$status
			.removeClass("is-dirty is-saving is-conflict")
			.addClass(kind === "saved" ? "" : "is-" + kind);
		this.$status.find(".tc-status-text").text(label[kind] || "");
	}
}

// Load a versioned /assets file by hand. NOT frappe.require: frappe.assets.extn()
// derives the type by splitting on "?" and taking the last segment, so a
// cache-busted "…/blocks.js?v=1.376.0" reports its extension as the version and
// loads as neither css nor js. The version token itself is mandatory — raw
// /assets are served immutable for a year, so an edit never reaches a cached
// browser without it (public/README.md). Same idiom as the classic builder.
function tc_load_asset(url) {
	return new Promise((resolve, reject) => {
		const is_css = url.split("?")[0].endsWith(".css");
		const selector = is_css ? `link[data-tc-asset="${url}"]` : `script[data-tc-asset="${url}"]`;
		if (document.querySelector(selector)) return resolve();
		const el = document.createElement(is_css ? "link" : "script");
		el.setAttribute("data-tc-asset", url);
		if (is_css) {
			el.rel = "stylesheet";
			el.href = url;
		} else {
			el.async = false;
			el.src = url;
		}
		el.onload = () => resolve();
		el.onerror = () => reject(new Error(__("Could not load {0}", [url])));
		document.head.appendChild(el);
	});
}
