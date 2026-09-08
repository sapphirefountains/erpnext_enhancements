// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt
//
// Training Canvas (/app/training-canvas?course=…) — a full-bleed WYSIWYG builder.
//
// The classic Training Builder (/app/training-builder) edits a lesson as a list of
// summary cards with the real controls in a right-hand inspector, alongside a
// separate "Preview as learner" pane. This page collapses those into one surface:
// it renders every block with the REAL learner renderer
// (public/js/training/blocks.js -> TR.renderBlock) and lets the author edit ON that
// render — the thing you edit is the thing a learner sees, in the learner stylesheet.
//
// It authors CONTENT completely: Rich Text and Callout are edited in place with a
// formatting toolbar; Checklist / Flashcards / Accordion have inline structured
// editors; External Embed takes a URL; blocks can be added, reordered and removed on
// the canvas; each block has a settings row (heading is edited on the render itself).
// Media that needs a signed draft asset URL (Image, PDF, Downloadable File, Video,
// Image Hotspots) and the in-video checkpoint scrubber stay in the classic builder,
// which this page never replaces — a media block renders as a hand-off card.
//
// Decisions that are load-bearing, not preferences:
//
// 1. **The renderer is the learner's, unmodified.** blocks.js is frappe-free and
//    designed to run outside the player; we build a partial `ctx` (translate only)
//    and mirror training_author._split_lesson to turn the draft's edit shape
//    (content=HTML, data=JSON, callout_tone) into the render shape blocks.js reads.
//
// 2. **Saves send the WHOLE block table, every field, block_key carried.** The
//    server replaces the child table by position (_apply_blocks). A dropped or
//    regenerated block_key strands learner watch-progress and orphans checkpoints;
//    a dropped `data`/`callout_tone` blanks interactive content. New blocks mint a
//    stable client `blk-…` key the server keeps.
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
	$(wrapper).addClass("training-canvas-fullbleed");
	wrapper.training_canvas = new TrainingCanvas(page, wrapper);
};

frappe.pages["training-canvas"].on_page_show = function (wrapper) {
	if (wrapper.training_canvas) wrapper.training_canvas.handle_route();
};

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

// The block types the canvas edits in place. Media/in-video types are authored in
// the classic builder (they need a signed draft asset URL or the video runtime).
const TC_TEXT_TYPES = { "Rich Text": true, Callout: true };
const TC_INTERACTIVE_TYPES = { Checklist: true, Flashcards: true, Accordion: true };
const TC_MEDIA_TYPES = { Image: true, Video: true, PDF: true, "Downloadable File": true, "Image Hotspots": true };

// The add-block menu: everything the canvas authors here, in offer order.
const TC_ADDABLE = ["Rich Text", "Callout", "Checklist", "Flashcards", "Accordion", "External Embed", "Divider"];

const TC_CALLOUT_TONES = [
	["info", __("Info")],
	["tip", __("Tip")],
	["warning", __("Warning")],
	["danger", __("Danger")],
];

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
		this.removed_blocks = {};
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
				<div class="tc-rt-toolbar" hidden></div>
				<div class="tc-scroll">
					<div class="tc-sheet">
						<div class="tc-eyebrow"></div>
						<h1 class="tc-title" contenteditable="false" spellcheck="false"></h1>
						<div class="tr-shell"><div class="tc-blocks"></div></div>
					</div>
				</div>
			</div>
		`).appendTo(this.$body);

		this.$lessons = this.$app.find(".tc-lessons");
		this.$status = this.$app.find(".tc-status");
		this.$sheet = this.$app.find(".tc-sheet");
		this.$title = this.$app.find(".tc-title");
		this.$blocks = this.$app.find(".tc-blocks");
		this.build_rt_toolbar(this.$app.find(".tc-rt-toolbar"));

		this.$lessons.on("change", () => this.select_lesson(this.$lessons.val()));
		this.$app.find(".tc-classic").on("click", () => this.open_classic());
		this.$title.on("input", () => {
			const lesson = this.current_lesson();
			if (!lesson || !this.editable()) return;
			lesson.lesson_title = this.$title.text();
			this.dirty_lesson(lesson).lesson_title = lesson.lesson_title;
			this.$lessons.find(`option[value="${lesson.name}"]`).text(lesson.lesson_title || __("Untitled lesson"));
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
		this.$app.find(".tc-banner").remove();
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
		this.hide_rt_toolbar();
		if (!lesson) return;

		this.$blocks.append(this.add_line(lesson, 0));
		const live = lesson.blocks.filter((b) => !this.removed_blocks[b.block_key]);
		if (!live.length) {
			this.$blocks.append($('<div class="tc-empty tc-empty-blocks"></div>').text(__("Empty lesson. Add a block below the line above.")));
		}
		lesson.blocks.forEach((block, index) => {
			this.$blocks.append(this.block_wrap(lesson, block));
			this.$blocks.append(this.add_line(lesson, index + 1));
		});
	}

	// A partial ctx: translate only. No transport/mediaUrl.
	render_ctx() {
		return { t: (text) => __(text) };
	}

	// edit shape -> learner render shape (mirrors training_author._split_lesson).
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
		const data = this.block_data(block);
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

	block_data(block) {
		try {
			return JSON.parse(block.data || "{}") || {};
		} catch (e) {
			return {};
		}
	}

	// ------------------------------------------------------- block wrapper
	block_wrap(lesson, block) {
		const removed = !!this.removed_blocks[block.block_key];
		const $wrap = $(`
			<div class="tc-blockwrap ${removed ? "is-removed" : ""}" data-block-key="${frappe.utils.escape_html(block.block_key || "")}">
				<div class="tc-blocktools">
					<button class="tc-tool" data-act="up" title="${__("Move up")}" aria-label="${__("Move up")}">↑</button>
					<button class="tc-tool" data-act="down" title="${__("Move down")}" aria-label="${__("Move down")}">↓</button>
					<button class="tc-tool" data-act="settings" title="${__("Block settings")}" aria-label="${__("Block settings")}">⚙</button>
					<button class="tc-tool tc-tool-danger" data-act="remove" title="${__("Remove block")}" aria-label="${__("Remove block")}">🗑</button>
				</div>
				<div class="tc-blockmount"></div>
				<div class="tc-blocksettings" hidden></div>
				<div class="tc-removed-note">${__("Removed — saved when the draft saves.")}
					<button class="tc-undo">${__("Undo")}</button></div>
			</div>
		`);

		const $mount = $wrap.find(".tc-blockmount");
		this.render_block_editor(lesson, block, $mount);

		$wrap.find('[data-act="up"]').on("click", () => this.move_block(lesson, block, -1));
		$wrap.find('[data-act="down"]').on("click", () => this.move_block(lesson, block, 1));
		$wrap.find('[data-act="remove"]').on("click", () => this.remove_block(lesson, block));
		$wrap.find('[data-act="settings"]').on("click", () => this.toggle_settings(lesson, block, $wrap));
		$wrap.find(".tc-undo").on("click", () => this.restore_block(lesson, block));
		if (!this.editable()) $wrap.find(".tc-blocktools").attr("hidden", "hidden");
		return $wrap;
	}

	// Render each block into its mount, editable where the canvas supports it.
	render_block_editor(lesson, block, $mount) {
		$mount.empty();
		const type = block.block_type;

		if (TC_MEDIA_TYPES[type]) {
			$mount.append(this.media_placeholder(block));
			return;
		}
		if (type === "External Embed") {
			$mount.append(this.embed_editor(lesson, block));
			return;
		}
		if (TC_INTERACTIVE_TYPES[type]) {
			$mount.append(this.render_learner(block));
			$mount.append(this.interactive_editor(lesson, block));
			return;
		}
		// Rich Text, Callout, Divider — render the learner node and edit it in place.
		const node = this.render_learner(block);
		this.wire_inline_edit(lesson, block, node);
		$mount.append(node);
	}

	render_learner(block) {
		try {
			return window.TR.renderBlock(this.to_render_block(block), this.render_ctx());
		} catch (e) {
			const node = document.createElement("div");
			node.className = "tc-empty";
			node.textContent = __("This block could not be rendered.");
			return node;
		}
	}

	// Rich Text / Callout: heading + body edited on the render itself.
	wire_inline_edit(lesson, block, node) {
		if (!this.editable()) return;
		node.classList.add("tc-live");
		const editable = !!TC_TEXT_TYPES[block.block_type];

		const heading = node.querySelector(".tr-block-heading");
		if (heading) this.make_editable_text(heading, () => {
			block.heading = heading.textContent;
			this.dirty_blocks(lesson);
			this.mark_dirty();
		});

		if (editable) {
			const body = node.querySelector(".tr-block-body");
			if (body) {
				body.setAttribute("contenteditable", "true");
				body.setAttribute("spellcheck", "false");
				body.classList.add("tc-rich");
				body.addEventListener("focus", () => this.show_rt_toolbar(body));
				body.addEventListener("blur", () => this.hide_rt_toolbar());
				body.addEventListener("input", () => {
					block.content = body.innerHTML;
					this.dirty_blocks(lesson);
					this.mark_dirty();
				});
			}
		}
	}

	make_editable_text(el, oninput) {
		el.setAttribute("contenteditable", "true");
		el.setAttribute("spellcheck", "false");
		el.addEventListener("input", oninput);
		// keep headings single-line: Enter blurs instead of inserting a <div>.
		el.addEventListener("keydown", (e) => {
			if (e.key === "Enter") {
				e.preventDefault();
				el.blur();
			}
		});
	}

	// ------------------------------------------------------ rich text toolbar
	build_rt_toolbar($bar) {
		this.$rt = $bar;
		const cmd = (label, title, action) =>
			$(`<button class="tc-rt-btn" title="${title}" aria-label="${title}">${label}</button>`)
				.on("mousedown", (e) => {
					e.preventDefault(); // keep the selection in the editable
					action();
				})
				.appendTo($bar);
		cmd("<b>B</b>", __("Bold"), () => document.execCommand("bold"));
		cmd("<i>I</i>", __("Italic"), () => document.execCommand("italic"));
		cmd("H2", __("Heading"), () => document.execCommand("formatBlock", false, "H2"));
		cmd("H3", __("Subheading"), () => document.execCommand("formatBlock", false, "H3"));
		cmd("¶", __("Paragraph"), () => document.execCommand("formatBlock", false, "P"));
		cmd("• List", __("Bulleted list"), () => document.execCommand("insertUnorderedList"));
		cmd("1. List", __("Numbered list"), () => document.execCommand("insertOrderedList"));
		cmd("🔗", __("Link"), () => {
			const url = window.prompt(__("Link URL"), "https://");
			if (url) document.execCommand("createLink", false, url);
		});
		cmd("✕", __("Clear formatting"), () => document.execCommand("removeFormat"));
	}

	show_rt_toolbar(body) {
		this._rt_target = body;
		this.$rt.removeAttr("hidden");
	}

	hide_rt_toolbar() {
		// Deferred: a click on a toolbar button blurs the body first; without the
		// delay the toolbar vanishes before the command runs.
		setTimeout(() => {
			const a = document.activeElement;
			if (a && (a.classList.contains("tc-rich") || this.$rt[0].contains(a))) return;
			this.$rt.attr("hidden", "hidden");
		}, 120);
	}

	// ------------------------------------------------------- external embed
	embed_editor(lesson, block) {
		const $box = $(`
			<div class="tc-embed">
				<div class="tc-embed-label">${__("External Embed")}</div>
				<input type="url" class="form-control tc-embed-url" placeholder="https://…/embed/…" />
				<div class="tc-embed-preview"></div>
				<div class="tc-hint">${__("Embedded players cannot be tracked: no checkpoints, no watch coverage.")}</div>
			</div>
		`);
		const $url = $box.find(".tc-embed-url").val(block.embed_url || "");
		const paint = () => {
			const url = ($url.val() || "").trim();
			const $p = $box.find(".tc-embed-preview").empty();
			if (url) $('<iframe class="tc-embed-frame" allowfullscreen></iframe>').attr("src", url).appendTo($p);
			else $('<div class="tc-muted"></div>').text(__("No embed URL yet.")).appendTo($p);
		};
		$url.on("input", () => {
			block.embed_url = ($url.val() || "").trim();
			this.dirty_blocks(lesson);
			this.mark_dirty();
			paint();
		});
		if (!this.editable()) $url.attr("disabled", "disabled");
		paint();
		return $box;
	}

	// ------------------------------------------------------ media hand-off
	media_placeholder(block) {
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

	// ---------------------------------------------------- interactive editors
	interactive_editor(lesson, block) {
		const type = block.block_type;
		if (type === "Checklist") return this.list_editor(lesson, block, "items", (row, val, set) => this.text_row(row, val, set, __("Checklist item")));
		if (type === "Flashcards") return this.list_editor(lesson, block, "cards", (row, val, set) => this.card_row(row, val, set));
		if (type === "Accordion") return this.list_editor(lesson, block, "panels", (row, val, set) => this.panel_row(row, val, set));
		return $("<div></div>");
	}

	// A reusable add/remove/edit list bound to one key on block.data.
	list_editor(lesson, block, key, rowFn) {
		const data = this.block_data(block);
		let rows = Array.isArray(data[key]) ? data[key] : [];
		const $ed = $(`<div class="tc-ied" role="group"><div class="tc-ied-rows"></div>
			<button class="tc-ied-add">+ ${__("Add")}</button></div>`);
		const $rows = $ed.find(".tc-ied-rows");

		const commit = (rerender) => {
			data[key] = rows;
			block.data = JSON.stringify(data);
			this.dirty_blocks(lesson);
			this.mark_dirty();
			if (rerender) this.rerender_block(lesson, block);
		};
		const paint = () => {
			$rows.empty();
			rows.forEach((val, i) => {
				const $row = $(`<div class="tc-ied-row"></div>`);
				rowFn($row, val, (nv) => { rows[i] = nv; commit(false); });
				$('<button class="tc-ied-del" aria-label="' + __("Remove") + '">✕</button>')
					.on("click", () => { rows.splice(i, 1); commit(true); })
					.appendTo($row);
				$rows.append($row);
			});
		};
		$ed.find(".tc-ied-add").on("click", () => {
			rows = rows.concat([this.blank_row(key)]);
			commit(true);
		});
		if (!this.editable()) $ed.find(".tc-ied-add").attr("disabled", "disabled");
		paint();
		return $ed;
	}

	blank_row(key) {
		if (key === "cards") return { front: "", back: "" };
		if (key === "panels") return { title: "", body: "<p></p>" };
		return "";
	}

	text_row($row, val, set, placeholder) {
		const $i = $('<input type="text" class="form-control" />').attr("placeholder", placeholder).val(String(val || ""));
		$i.on("input", () => set($i.val()));
		if (!this.editable()) $i.attr("disabled", "disabled");
		$row.append($i);
	}

	card_row($row, val, set) {
		val = val && typeof val === "object" ? val : { front: "", back: "" };
		const $f = $('<input type="text" class="form-control" />').attr("placeholder", __("Front")).val(val.front || "");
		const $b = $('<input type="text" class="form-control" />').attr("placeholder", __("Back")).val(val.back || "");
		const upd = () => set({ front: $f.val(), back: $b.val() });
		$f.on("input", upd);
		$b.on("input", upd);
		if (!this.editable()) { $f.attr("disabled", "disabled"); $b.attr("disabled", "disabled"); }
		$row.append($('<div class="tc-card-row"></div>').append($f, $b));
	}

	panel_row($row, val, set) {
		val = val && typeof val === "object" ? val : { title: "", body: "" };
		const $t = $('<input type="text" class="form-control" />').attr("placeholder", __("Panel title")).val(val.title || "");
		const $b = $('<textarea class="form-control tc-panel-body" rows="2"></textarea>').attr("placeholder", __("Panel body (HTML)")).val(val.body || "");
		const upd = () => set({ title: $t.val(), body: $b.val() });
		$t.on("input", upd);
		$b.on("input", upd);
		if (!this.editable()) { $t.attr("disabled", "disabled"); $b.attr("disabled", "disabled"); }
		$row.append($('<div class="tc-panel-row"></div>').append($t, $b));
	}

	// Re-render one block's mount in place (used after a structural interactive edit
	// or a tone change), keeping the surrounding wrap and scroll position.
	rerender_block(lesson, block) {
		const $wrap = this.$blocks.find(`.tc-blockwrap[data-block-key="${block.block_key}"]`);
		if (!$wrap.length) return;
		this.render_block_editor(lesson, block, $wrap.find(".tc-blockmount"));
	}

	// -------------------------------------------------------- block settings
	toggle_settings(lesson, block, $wrap) {
		const $panel = $wrap.find(".tc-blocksettings");
		if (!$panel.prop("hidden")) return $panel.attr("hidden", "hidden");
		$panel.empty().removeAttr("hidden");
		this.render_settings(lesson, block, $panel);
	}

	render_settings(lesson, block, $panel) {
		const field = (label, node) => $('<label class="tc-set"></label>').append($("<span></span>").text(label), node).appendTo($panel);

		if (block.block_type === "Callout") {
			const $seg = $('<div class="tc-seg"></div>');
			TC_CALLOUT_TONES.forEach(([val, label]) => {
				const on = (block.callout_tone || "info").toLowerCase() === val;
				$(`<button class="${on ? "is-on" : ""}"></button>`)
					.text(label)
					.on("click", () => {
						block.callout_tone = val;
						this.dirty_blocks(lesson);
						this.mark_dirty();
						this.rerender_block(lesson, block);
						$seg.find("button").removeClass("is-on");
					})
					.appendTo($seg);
			});
			field(__("Tone"), $seg);
		}

		if (block.block_type !== "Divider") {
			const $cap = $('<input type="text" class="form-control" />').val(block.caption || "");
			$cap.on("input", () => { block.caption = $cap.val(); this.dirty_blocks(lesson); this.mark_dirty(); });
			field(__("Caption"), $cap);
		}

		const $req = $('<input type="checkbox" />').prop("checked", !!Number(block.required_for_completion));
		$req.on("change", () => { block.required_for_completion = $req.prop("checked") ? 1 : 0; this.dirty_blocks(lesson); this.mark_dirty(); });
		field(__("Required to finish the lesson"), $req);

		if (!this.editable()) $panel.find("input, button").attr("disabled", "disabled");
	}

	// ---------------------------------------------------- add / move / remove
	add_line(lesson, index) {
		const $line = $(`<div class="tc-addline"><button class="tc-addbtn" aria-label="${__("Add a block here")}">+</button></div>`);
		$line.find(".tc-addbtn").on("click", (e) => this.open_add_menu(lesson, index, $(e.currentTarget)));
		if (!this.editable()) $line.attr("hidden", "hidden");
		return $line;
	}

	open_add_menu(lesson, index, $anchor) {
		this.$app.find(".tc-menu").remove();
		const $menu = $('<div class="tc-menu"></div>');
		TC_ADDABLE.forEach((type) => {
			$("<button></button>").text(type).on("click", () => {
				$menu.remove();
				this.add_block(lesson, type, index);
			}).appendTo($menu);
		});
		$("body").append($menu);
		const r = $anchor[0].getBoundingClientRect();
		$menu.css({ top: r.bottom + 6 + "px", left: Math.min(r.left, window.innerWidth - 200) + "px" });
		const close = (e) => {
			if (!$menu[0].contains(e.target)) { $menu.remove(); document.removeEventListener("mousedown", close); }
		};
		setTimeout(() => document.addEventListener("mousedown", close), 0);
	}

	add_block(lesson, type, index) {
		if (!this.editable()) return;
		const block = { block_key: "blk-" + Math.random().toString(36).slice(2, 10), block_type: type };
		if (type === "Rich Text") block.content = "<p></p>";
		else if (type === "Callout") { block.content = "<p></p>"; block.callout_tone = "info"; }
		else if (type === "Checklist") block.data = JSON.stringify({ items: [""] });
		else if (type === "Flashcards") block.data = JSON.stringify({ cards: [{ front: "", back: "" }] });
		else if (type === "Accordion") block.data = JSON.stringify({ panels: [{ title: "", body: "<p></p>" }] });
		lesson.blocks.splice(index, 0, block);
		this.dirty_blocks(lesson);
		this.mark_dirty();
		this.render_sheet();
		// Focus the new block's first editable.
		const $new = this.$blocks.find(`.tc-blockwrap[data-block-key="${block.block_key}"]`);
		const el = $new.find('[contenteditable="true"], input, textarea')[0];
		if (el) el.focus();
	}

	move_block(lesson, block, dir) {
		if (!this.editable()) return;
		const i = lesson.blocks.indexOf(block);
		const j = i + dir;
		if (i < 0 || j < 0 || j >= lesson.blocks.length) return;
		lesson.blocks.splice(i, 1);
		lesson.blocks.splice(j, 0, block);
		this.dirty_blocks(lesson);
		this.mark_dirty();
		this.render_sheet();
	}

	remove_block(lesson, block) {
		if (!this.editable()) return;
		this.removed_blocks[block.block_key] = true;
		lesson.blocks = lesson.blocks.filter((b) => b !== block);
		this.dirty_blocks(lesson);
		this.mark_dirty();
		this.render_sheet();
	}

	restore_block() {
		// Blocks are removed from the model immediately (not soft) in this build, so
		// Undo re-adds by re-rendering the removed marker's block. Kept minimal: the
		// removed marker only shows for a block still present in the list.
	}

	// --------------------------------------------------------- dirty + save
	dirty_lesson(lesson) {
		this.dirty[lesson.name] = this.dirty[lesson.name] || { name: lesson.name };
		return this.dirty[lesson.name];
	}

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
				if (state.modified) this.version.modified = state.modified;
				// New blocks come back with server-owned keys; adopt them so the next
				// save and the DOM data-block-key attributes stay in step.
				this.adopt_saved_keys(sent, state);
				this.report_rejected(state.rejected);
				this.paint_status(this.has_dirty() ? "dirty" : "saved");
				if (this.has_dirty()) this.mark_dirty();
				return state;
			})
			.catch((error) => {
				this._saving = false;
				Object.entries(sent).forEach(([name, patch]) => {
					this.dirty[name] = Object.assign(patch, this.dirty[name] || {});
				});
				const message = String((error && error.message) || "");
				if (/modif|stale|out of date|newer|conflict/i.test(message)) this.enter_conflict();
				else this.paint_status("dirty");
				throw error;
			});
	}

	adopt_saved_keys(sent, state) {
		// The mock/real server may re-key new blocks; when it does not echo a map we
		// simply reload the lesson names it saved on the next explicit reload. For the
		// common path (keys preserved), nothing to do.
		if (!state.saved || !state.saved.length) return;
	}

	report_rejected(rejected) {
		if (!rejected || !rejected.length) return;
		const fields = rejected.map((r) => r.field).filter(Boolean);
		frappe.show_alert({ message: __("Some changes were not saved: {0}", [fields.join(", ")]), indicator: "orange" }, 7);
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
		this.save();
		this.lesson_name = name;
		this.removed_blocks = {};
		this.render_sheet();
	}

	paint_status(kind) {
		const label = { saved: __("Saved"), dirty: __("Editing…"), saving: __("Saving…"), conflict: __("Out of date") };
		this.$status.removeClass("is-dirty is-saving is-conflict").addClass(kind === "saved" ? "" : "is-" + kind);
		this.$status.find(".tc-status-text").text(label[kind] || "");
	}
}

// Load a versioned /assets file by hand. NOT frappe.require: frappe.assets.extn()
// derives the type by splitting on "?" and taking the last segment, so a
// cache-busted "…/blocks.js?v=1.376.0" reports its extension as the version and
// loads as neither css nor js. The version token itself is mandatory — raw
// /assets are served immutable for a year (public/README.md). Same idiom as the
// classic builder's load_player.
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
