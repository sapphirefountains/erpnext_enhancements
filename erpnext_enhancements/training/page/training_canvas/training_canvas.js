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
// It authors a course completely: a lesson rail (add / reorder / delete / chapters),
// a lesson-settings panel (summary, minutes, gating, quiz settings), content blocks
// edited in place (Rich Text and Callout with a formatting toolbar; Checklist /
// Flashcards / Accordion inline editors; External Embed), block add / reorder / remove,
// and the draft lifecycle (new draft, submit for review, publish). Media that needs a
// signed draft asset URL (Image, PDF, Downloadable File, Video, Image Hotspots) and the
// in-video checkpoint scrubber stay in the classic builder, which this page never
// replaces — a media block renders as a hand-off card.
//
// Decisions that are load-bearing, not preferences:
//
// 1. **The renderer is the learner's, unmodified.** blocks.js is frappe-free; we build
//    a partial `ctx` (translate only) and mirror training_author._split_lesson to turn
//    the draft's edit shape (content=HTML, data=JSON, callout_tone) into the render
//    shape blocks.js reads.
//
// 2. **Saves send the WHOLE block table, every field, block_key carried.** The server
//    replaces the child table by position (_apply_blocks). A dropped or regenerated
//    block_key strands learner watch-progress and orphans checkpoints; a dropped
//    `data`/`callout_tone` blanks interactive content. New blocks mint a stable client
//    `blk-…` key the server keeps; a new lesson carries a `temp_id` the save maps back.
//
// 3. **The optimistic lock is the version's `modified`.** Hold it, send it, adopt the
//    token the save returns — the next save is rejected against the old one.
//
// 4. **Field allowlists are the contract on both sides.** TC_LESSON_FIELDS /
//    TC_BLOCK_FIELDS mirror LESSON_ALLOWED_FIELDS / BLOCK_ALLOWED_FIELDS in
//    api/training_author.py; a field outside them is dropped and comes back in
//    `rejected`, which we surface. Checkpoints and quiz-question bodies are refused by
//    design and are not sent here.
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

// Mirror LESSON_ALLOWED_FIELDS / BLOCK_ALLOWED_FIELDS in api/training_author.py.
const TC_LESSON_FIELDS = [
	"lesson_title",
	"chapter_key",
	"idx_in_chapter",
	"estimated_minutes",
	"summary",
	"allow_questions",
	"requires_submission",
	"has_quiz",
	"quiz_questions_to_ask",
	"quiz_pass_score",
	"quiz_shuffle_questions",
	"quiz_shuffle_options",
];
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

const TC_TEXT_TYPES = { "Rich Text": true, Callout: true };
const TC_INTERACTIVE_TYPES = { Checklist: true, Flashcards: true, Accordion: true };
const TC_MEDIA_TYPES = { Image: true, Video: true, PDF: true, "Downloadable File": true, "Image Hotspots": true };
const TC_ADDABLE = [
	"Rich Text",
	"Callout",
	"Checklist",
	"Flashcards",
	"Accordion",
	"Image",
	"Video",
	"PDF",
	"Downloadable File",
	"External Embed",
	"Image Hotspots",
	"Divider",
];
// The DocType's own Select vocabulary, verbatim and capitalised. It has to be:
// `callout_tone` is a Select on Training Content Block, `_validate_selects` runs
// on child rows, and `save_draft_version` performs a full `lesson.save()` -- so a
// value outside this list does not degrade, it throws, and it takes the WHOLE
// lesson autosave with it. This list was lowercase and carried a phantom "info"
// that the Select did not declare, and line ~1075 stamped it on every new
// Callout, so the first Callout an author created on this page broke saving until
// v1.386.0. The learner-side lowercasing happens on the server
// (`training_author._augment_interactive_block`), not here.
const TC_CALLOUT_TONES = [["Info", __("Info")], ["Tip", __("Tip")], ["Warning", __("Warning")], ["Danger", __("Danger")]];

// The full change_type strings the DocType stores; publish_version rejects anything else.
const TC_MINOR = "Minor Edit (keep completions)";
const TC_MATERIAL = "Material Change (require retake)";

const TC_SAVE_DEBOUNCE_MS = 1200;

class TrainingCanvas {
	constructor(page, wrapper) {
		this.page = page;
		this.wrapper = wrapper;
		this.$body = $(page.body);
		this.reset();
		this.build_chrome();
		this.page.set_secondary_action(__("Reload"), () => this.reload());
		this.page.add_menu_item(__("New draft version"), () => this.new_draft());
		this.page.add_menu_item(__("Submit for review"), () => this.submit_for_review());
		this.page.add_menu_item(__("Publish…"), () => this.publish());
		this.page.add_menu_item(__("Open classic builder"), () => this.open_classic());
	}

	reset() {
		this.course = null;
		this.version = null;
		this.chapters = [];
		this.lessons = [];
		this.lesson_name = null;
		this.dirty = { lessons: {}, deleted: [], chapters: null };
		this.removed_blocks = {};
		this._saving = false;
		this._conflict = false;
		this._temp = 0;
		clearTimeout(this._save_timer);
	}

	// ---------------------------------------------------------------- chrome
	build_chrome() {
		this.$body.empty();
		this.$app = $(`
			<div class="tc-app">
				<div class="tc-bar">
					<button class="tc-icon tc-rail-toggle" title="${__("Lessons")}" aria-label="${__("Toggle lessons")}">☰</button>
					<span class="tc-course-name"></span>
					<span class="tc-spacer"></span>
					<span class="tc-status" role="status" aria-live="polite">
						<span class="tc-pip"></span><span class="tc-status-text">${__("Saved")}</span>
					</span>
					<button class="btn btn-default btn-sm tc-lessonset">⚙ ${__("Lesson")}</button>
					<button class="btn btn-default btn-sm tc-classic">${__("Classic builder")}</button>
				</div>
				<div class="tc-rt-toolbar" hidden></div>
				<div class="tc-main">
					<aside class="tc-rail"></aside>
					<div class="tc-scroll">
						<div class="tc-sheet">
							<div class="tc-lessonsettings" hidden></div>
							<div class="tc-eyebrow"></div>
							<h1 class="tc-title" contenteditable="false" spellcheck="false"></h1>
							<div class="tr-shell"><div class="tc-blocks"></div></div>
						</div>
					</div>
				</div>
			</div>
		`).appendTo(this.$body);

		this.$rail = this.$app.find(".tc-rail");
		this.$status = this.$app.find(".tc-status");
		this.$sheet = this.$app.find(".tc-sheet");
		this.$title = this.$app.find(".tc-title");
		this.$blocks = this.$app.find(".tc-blocks");
		this.$lessonset = this.$app.find(".tc-lessonsettings");
		this.build_rt_toolbar(this.$app.find(".tc-rt-toolbar"));

		this.$app.find(".tc-classic").on("click", () => this.open_classic());
		this.$app.find(".tc-rail-toggle").on("click", () => this.$app.toggleClass("is-rail-collapsed"));
		this.$app.find(".tc-lessonset").on("click", () => this.toggle_lesson_settings());
		this.$title.on("input", () => {
			const lesson = this.current_lesson();
			if (!lesson || !this.editable()) return;
			lesson.lesson_title = this.$title.text();
			this.dirty_lesson(lesson).lesson_title = lesson.lesson_title;
			this.render_rail();
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
		this._assets = Promise.all(TC_ASSETS.map((path) => tc_load_asset(path + "?v=" + encodeURIComponent(version)))).catch(
			(error) => {
				this._assets = null;
				throw error;
			}
		);
		return this._assets;
	}

	load(course) {
		this.$blocks.html(`<div class="tc-empty">${__("Loading…")}</div>`);
		Promise.all([
			this.load_assets(),
			frappe.call({ method: "erpnext_enhancements.api.training_author.get_builder_bootstrap", args: { course } }),
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
		this.video_assets = data.video_assets || [];
		this.lessons = (data.lessons || []).map((lesson) => {
			lesson.blocks = lesson.blocks || [];
			return lesson;
		});
		if (!this.lesson_name || !this.lesson(this.lesson_name)) {
			this.lesson_name = this.lessons.length ? this.lessons[0].name : null;
		}
	}

	lesson(name) {
		return this.lessons.find((l) => (l.name || l.__temp) === name) || null;
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

		this.$app.find(".tc-course-name").text(this.course.course_title || this.course.name || "");
		this.$app.find(".tc-eyebrow").text(this.course.course_title || "");
		this.$app.toggleClass("is-readonly", !this.editable());
		this.$title.attr("contenteditable", this.editable() ? "true" : "false");
		this.render_rail();
		this.render_sheet();
		if (!this.$lessonset.prop("hidden")) this.render_lesson_settings();
		this.paint_status(this.has_dirty() ? "dirty" : "saved");
	}

	render_course_picker() {
		this.$app.find(".tc-course-name").text("");
		this.$title.text(__("Training Canvas"));
		this.$rail.empty();
		this.$blocks.html(
			`<div class="tc-empty"><h2>${__("Open a course to edit")}</h2>${__(
				"Open a Training Course and choose Edit on the canvas, or add ?course=… to the URL."
			)}<br><a href="/app/training-course">${__("Open the course list")}</a></div>`
		);
	}

	render_no_draft() {
		this.$app.find(".tc-course-name").text(this.course.course_title || "");
		this.$title.text("");
		this.$rail.empty();
		this.$blocks.html(
			`<div class="tc-empty"><h2>${__("No open draft")}</h2>${__(
				"This course has no draft version open for editing. Raise one to start editing."
			)}<br><button class="btn btn-primary btn-sm tc-raise" style="margin-top:12px">${__(
				"New draft version"
			)}</button></div>`
		);
		this.$blocks.find(".tc-raise").on("click", () => this.new_draft());
	}

	// ------------------------------------------------------------ lesson rail
	render_rail() {
		this.$rail.empty();
		const $add = $(`<button class="tc-rail-add btn btn-default btn-sm">+ ${__("Lesson")}</button>`);
		$add.on("click", () => this.add_lesson());
		if (!this.editable()) $add.attr("disabled", "disabled");

		const $list = $('<div class="tc-rail-list"></div>');
		const byChapter = {};
		this.lessons.forEach((l) => {
			const key = l.chapter_key || "";
			(byChapter[key] = byChapter[key] || []).push(l);
		});
		const order = this.chapters.map((c) => c.chapter_key).concat([""]);
		const seen = {};
		order.forEach((key) => {
			if (seen[key] || !byChapter[key]) return;
			seen[key] = true;
			const chapter = this.chapters.find((c) => c.chapter_key === key);
			const label = chapter ? chapter.chapter_title : key ? key : __("Unfiled");
			$('<div class="tc-rail-chapter"></div>').text(label).appendTo($list);
			byChapter[key].forEach((lesson) => $list.append(this.lesson_row(lesson)));
		});

		this.$rail.append($add, $list);
		if (window.Sortable && this.editable()) {
			Sortable.create($list[0], {
				handle: ".tc-rail-title",
				draggable: ".tc-rail-row",
				onEnd: () => this.commit_lesson_order($list),
			});
		}
	}

	lesson_row(lesson) {
		const id = lesson.name || lesson.__temp;
		const $row = $(`
			<div class="tc-rail-row ${id === this.lesson_name ? "is-current" : ""}" data-lesson="${frappe.utils.escape_html(id)}">
				<span class="tc-rail-title"></span>
				<button class="tc-rail-del" title="${__("Delete lesson")}" aria-label="${__("Delete lesson")}">🗑</button>
			</div>
		`);
		$row.find(".tc-rail-title").text(lesson.lesson_title || __("Untitled lesson"));
		$row.on("click", (e) => {
			if ($(e.target).closest(".tc-rail-del").length) return;
			this.select_lesson(id);
		});
		$row.find(".tc-rail-del").on("click", () => this.remove_lesson(lesson));
		if (!this.editable()) $row.find(".tc-rail-del").attr("hidden", "hidden");
		return $row;
	}

	add_lesson() {
		if (!this.editable()) return;
		this.save(); // flush the current lesson's edits before creating a new one
		const lesson = {
			__temp: "new-" + ++this._temp,
			lesson_title: __("New lesson"),
			chapter_key: (this.current_lesson() || {}).chapter_key || (this.chapters[0] || {}).chapter_key || "",
			blocks: [],
			quiz: [],
			checkpoints: [],
		};
		this.lessons.push(lesson);
		const patch = this.dirty_lesson(lesson);
		patch.lesson_title = lesson.lesson_title;
		patch.chapter_key = lesson.chapter_key;
		this.lesson_name = lesson.__temp;
		this.mark_dirty();
		this.render();
	}

	remove_lesson(lesson) {
		if (!this.editable()) return;
		frappe.confirm(__("Delete this lesson and everything in it?"), () => {
			this.lessons = this.lessons.filter((l) => l !== lesson);
			if (lesson.name) this.dirty.deleted.push(lesson.name);
			delete this.dirty.lessons[lesson.name || lesson.__temp];
			if (this.lesson_name === (lesson.name || lesson.__temp)) {
				this.lesson_name = this.lessons.length ? this.lessons[0].name || this.lessons[0].__temp : null;
			}
			this.mark_dirty();
			this.render();
		});
	}

	commit_lesson_order($list) {
		if (!this.editable()) return;
		const order = $list.find(".tc-rail-row").map((_, el) => $(el).attr("data-lesson")).get();
		// Reorder the in-memory list to match, then persist through the dedicated
		// endpoint (it renumbers idx_in_chapter without churning the lock token).
		this.lessons.sort((a, b) => order.indexOf(a.name || a.__temp) - order.indexOf(b.name || b.__temp));
		const names = this.lessons.map((l) => l.name).filter(Boolean);
		frappe
			.call({
				method: "erpnext_enhancements.api.training_author.reorder_lessons",
				args: { course_version: this.version.name, order: JSON.stringify(names) },
			})
			.then(() => frappe.show_alert({ message: __("Lesson order saved."), indicator: "green" }, 3))
			.catch(() => this.render_rail());
	}

	// -------------------------------------------------------- lesson settings
	toggle_lesson_settings() {
		if (!this.$lessonset.prop("hidden")) return this.$lessonset.attr("hidden", "hidden");
		this.$lessonset.removeAttr("hidden");
		this.render_lesson_settings();
	}

	render_lesson_settings() {
		const lesson = this.current_lesson();
		this.$lessonset.empty();
		if (!lesson) return;
		const ed = this.editable();
		const field = (label, node) =>
			$('<label class="tc-set"></label>').append($("<span></span>").text(label), node).appendTo(this.$lessonset);
		const num = (val) => (Number.isFinite(Number(val)) ? Number(val) : 0);

		$('<div class="tc-lessonset-head"></div>').text(__("Lesson settings")).appendTo(this.$lessonset);

		const $summary = $('<textarea class="form-control" rows="2"></textarea>').val(lesson.summary || "");
		$summary.on("input", () => this.set_lesson_field(lesson, "summary", $summary.val()));
		field(__("Summary"), $summary);

		if (this.chapters.length) {
			const $ch = $('<select class="form-control"></select>');
			$('<option value=""></option>').text(__("Unfiled")).appendTo($ch);
			this.chapters.forEach((c) => $("<option></option>").attr("value", c.chapter_key).text(c.chapter_title).appendTo($ch));
			$ch.val(lesson.chapter_key || "");
			$ch.on("change", () => { this.set_lesson_field(lesson, "chapter_key", $ch.val()); this.render_rail(); });
			field(__("Chapter"), $ch);
		}

		const $min = $('<input type="number" min="0" class="form-control" />').val(num(lesson.estimated_minutes));
		$min.on("input", () => this.set_lesson_field(lesson, "estimated_minutes", num($min.val())));
		field(__("Estimated minutes"), $min);

		const check = (labelText, fieldName) => {
			const $c = $('<input type="checkbox" />').prop("checked", !!num(lesson[fieldName]));
			$c.on("change", () => this.set_lesson_field(lesson, fieldName, $c.prop("checked") ? 1 : 0));
			field(labelText, $c);
			return $c;
		};
		check(__("Allow learner questions"), "allow_questions");
		check(__("Require a work submission"), "requires_submission");
		const $quiz = check(__("End-of-lesson quiz"), "has_quiz");

		const $quizbox = $('<div class="tc-quizset"></div>');
		const paintQuiz = () => {
			$quizbox.toggle(!!num(lesson.has_quiz));
		};
		const qnum = (label, fieldName) => {
			const $n = $('<input type="number" min="0" class="form-control" />').val(num(lesson[fieldName]));
			$n.on("input", () => this.set_lesson_field(lesson, fieldName, num($n.val())));
			$('<label class="tc-set"></label>').append($("<span></span>").text(label), $n).appendTo($quizbox);
		};
		qnum(__("Questions to ask"), "quiz_questions_to_ask");
		qnum(__("Pass score (%)"), "quiz_pass_score");
		const qcheck = (label, fieldName) => {
			const $c = $('<input type="checkbox" />').prop("checked", !!num(lesson[fieldName]));
			$c.on("change", () => this.set_lesson_field(lesson, fieldName, $c.prop("checked") ? 1 : 0));
			$('<label class="tc-set"></label>').append($("<span></span>").text(label), $c).appendTo($quizbox);
		};
		qcheck(__("Shuffle questions"), "quiz_shuffle_questions");
		qcheck(__("Shuffle options"), "quiz_shuffle_options");
		this.$lessonset.append($quizbox);
		$quiz.on("change", paintQuiz);
		paintQuiz();

		$('<div class="tc-hint"></div>')
			.html(__("Quiz questions themselves, and in-video checkpoints, are edited in the classic builder."))
			.appendTo(this.$lessonset);

		if (!ed) this.$lessonset.find("input, textarea, select").attr("disabled", "disabled");
	}

	set_lesson_field(lesson, field, value) {
		if (!this.editable() || TC_LESSON_FIELDS.indexOf(field) === -1) return;
		lesson[field] = value;
		this.dirty_lesson(lesson)[field] = value;
		this.mark_dirty();
	}

	// ---------------------------------------------------------- lifecycle
	new_draft() {
		if (!this.course) return;
		this.save();
		frappe.confirm(__("Raise a new draft version to edit? It copies the live content."), () => {
			frappe
				.call({ method: "erpnext_enhancements.api.training_author.create_draft_version", args: { course: this.course.name } })
				.then(() => this.reload())
				.catch((e) => frappe.msgprint({ message: (e && e.message) || __("Could not create a draft."), indicator: "red" }));
		});
	}

	submit_for_review() {
		if (!this.version) return;
		this.save();
		frappe
			.call({ method: "erpnext_enhancements.api.training_author.submit_for_review", args: { course_version: this.version.name } })
			.then(() => { frappe.show_alert({ message: __("Submitted for review."), indicator: "green" }, 4); this.reload(); })
			.catch((e) => frappe.msgprint({ message: (e && e.message) || __("Could not submit."), indicator: "red" }));
	}

	publish() {
		if (!this.version) return;
		this.save();
		const dialog = new frappe.ui.Dialog({
			title: __("Publish this version"),
			fields: [
				{
					fieldname: "change_type",
					label: __("Change type"),
					fieldtype: "Select",
					options: [TC_MINOR, TC_MATERIAL].join("\n"),
					default: TC_MINOR,
					reqd: 1,
				},
				{ fieldname: "release_notes", label: __("Release notes"), fieldtype: "Small Text" },
			],
			primary_action_label: __("Publish"),
			primary_action: (values) => {
				dialog.hide();
				frappe
					.call({
						method: "erpnext_enhancements.api.training_author.publish_version",
						args: { course_version: this.version.name, change_type: values.change_type, release_notes: values.release_notes || "" },
					})
					.then(() => { frappe.show_alert({ message: __("Published."), indicator: "green" }, 4); this.reload(); })
					.catch((e) => frappe.msgprint({ message: (e && e.message) || __("Could not publish."), indicator: "red" }));
			},
		});
		dialog.show();
	}

	// --------------------------------------------------------------- sheet
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

	render_ctx() {
		return { t: (text) => __(text) };
	}

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

	interactive_keys(block) {
		const type = block.block_type;
		if (type === "Callout") {
			const tone = (block.callout_tone || "").trim().toLowerCase();
			return ["tip", "warning", "danger", "info"].indexOf(tone) !== -1 ? { tone } : {};
		}
		const data = this.block_data(block);
		if (type === "Checklist") return { items: (data.items || []).map((x) => String(x)).filter((x) => x.trim()) };
		if (type === "Flashcards") {
			return {
				cards: (data.cards || []).filter((c) => c && typeof c === "object").map((c) => ({ front: String(c.front || ""), back: String(c.back || "") })),
			};
		}
		if (type === "Accordion") {
			return {
				panels: (data.panels || []).filter((p) => p && typeof p === "object").map((p) => ({ title: String(p.title || ""), body: frappe.utils.xss_sanitise(String(p.body || "")) })),
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
		const $wrap = $(`
			<div class="tc-blockwrap" data-block-key="${frappe.utils.escape_html(block.block_key || "")}">
				<div class="tc-blocktools">
					<button class="tc-tool" data-act="up" title="${__("Move up")}" aria-label="${__("Move up")}">↑</button>
					<button class="tc-tool" data-act="down" title="${__("Move down")}" aria-label="${__("Move down")}">↓</button>
					<button class="tc-tool" data-act="settings" title="${__("Block settings")}" aria-label="${__("Block settings")}">⚙</button>
					<button class="tc-tool tc-tool-danger" data-act="remove" title="${__("Remove block")}" aria-label="${__("Remove block")}">🗑</button>
				</div>
				<div class="tc-blockmount"></div>
				<div class="tc-blocksettings" hidden></div>
			</div>
		`);
		this.render_block_editor(lesson, block, $wrap.find(".tc-blockmount"));
		$wrap.find('[data-act="up"]').on("click", () => this.move_block(lesson, block, -1));
		$wrap.find('[data-act="down"]').on("click", () => this.move_block(lesson, block, 1));
		$wrap.find('[data-act="remove"]').on("click", () => this.remove_block(lesson, block));
		$wrap.find('[data-act="settings"]').on("click", () => this.toggle_settings(lesson, block, $wrap));
		if (!this.editable()) $wrap.find(".tc-blocktools").attr("hidden", "hidden");
		return $wrap;
	}

	render_block_editor(lesson, block, $mount) {
		$mount.empty();
		const type = block.block_type;
		if (TC_MEDIA_TYPES[type]) return $mount.append(this.media_editor(lesson, block));
		if (type === "External Embed") return $mount.append(this.embed_editor(lesson, block));
		if (TC_INTERACTIVE_TYPES[type]) {
			$mount.append(this.render_learner(block));
			return $mount.append(this.interactive_editor(lesson, block));
		}
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

	wire_inline_edit(lesson, block, node) {
		if (!this.editable()) return;
		node.classList.add("tc-live");
		const editable = !!TC_TEXT_TYPES[block.block_type];
		const heading = node.querySelector(".tr-block-heading");
		if (heading)
			this.make_editable_text(heading, () => {
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
					e.preventDefault();
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

	// ------------------------------------------------------------ media blocks
	num(v) {
		const n = Number(v);
		return Number.isFinite(n) ? n : 0;
	}

	media_editor(lesson, block) {
		const type = block.block_type;
		if (type === "Video") return this.video_editor(lesson, block);
		if (type === "Image Hotspots") return this.hotspots_editor(lesson, block);
		return this.file_editor(lesson, block);
	}

	// Image / PDF / Downloadable File — attach a private file and preview it.
	file_editor(lesson, block) {
		const isImage = block.block_type === "Image";
		const $box = $('<div class="tc-media"></div>');
		$('<div class="tc-embed-label"></div>').text(block.block_type).appendTo($box);
		const $preview = $('<div class="tc-media-preview"></div>').appendTo($box);
		const $btn = $('<button class="btn btn-default btn-sm"></button>');
		const paint = () => {
			const url = isImage ? block.image : block.file;
			$preview.empty();
			if (isImage && url) $('<img class="tc-media-img" alt="">').attr("src", url).appendTo($preview);
			else if (url) $('<a class="tc-media-link" target="_blank" rel="noopener"></a>').attr("href", url).text(url.split("/").pop() || url).appendTo($preview);
			else $('<div class="tc-muted"></div>').text(__("No file attached yet.")).appendTo($preview);
			$btn.text(url ? __("Replace file") : __("Attach file"));
		};
		$btn.on("click", () => this.attach_media(lesson, block, isImage ? "image" : "file", isImage ? "image/*" : block.block_type === "PDF" ? "application/pdf" : ""));
		if (!this.editable()) $btn.attr("disabled", "disabled");
		$box.append($btn);
		paint();
		return $box;
	}

	// Video — pick a registered Training Video Asset (from the bootstrap) and set the
	// poster / coverage gate. Registering a NEW video (the Drive probe) and placing
	// in-video checkpoints stay in the classic builder.
	video_editor(lesson, block) {
		const $box = $('<div class="tc-media"></div>');
		$('<div class="tc-embed-label"></div>').text(__("Video")).appendTo($box);
		const field = (label, node) => $('<label class="tc-set"></label>').append($("<span></span>").text(label), node).appendTo($box);
		const $sel = $('<select class="form-control"></select>');
		$('<option value=""></option>').text(__("— pick a video asset —")).appendTo($sel);
		(this.video_assets || []).forEach((a) => $("<option></option>").attr("value", a.name).text(a.title || a.name).appendTo($sel));
		$sel.val(block.video_asset || "");
		$sel.on("change", () => { block.video_asset = $sel.val(); this.dirty_blocks(lesson); this.mark_dirty(); });
		field(__("Video asset"), $sel);
		const $poster = $('<input type="url" class="form-control" />').val(block.poster_image || "");
		$poster.on("input", () => { block.poster_image = $poster.val(); this.dirty_blocks(lesson); this.mark_dirty(); });
		field(__("Poster image URL"), $poster);
		const $cov = $('<input type="number" min="0" max="100" class="form-control" />').val(this.num(block.min_coverage_percent));
		$cov.on("input", () => { block.min_coverage_percent = this.num($cov.val()); this.dirty_blocks(lesson); this.mark_dirty(); });
		field(__("Coverage to pass (%)"), $cov);
		const $cp = $('<input type="checkbox" />').prop("checked", !!this.num(block.checkpoints_enabled));
		$cp.on("change", () => { block.checkpoints_enabled = $cp.prop("checked") ? 1 : 0; this.dirty_blocks(lesson); this.mark_dirty(); });
		field(__("In-video checkpoints"), $cp);
		$('<div class="tc-hint"></div>').text(__("Register a new video, and place its checkpoints on the timeline, in the classic builder.")).appendTo($box);
		$('<button class="btn btn-default btn-xs" style="margin-top:6px"></button>').text(__("Open classic builder")).on("click", () => this.open_classic()).appendTo($box);
		if (!this.editable()) $box.find("input, select").attr("disabled", "disabled");
		return $box;
	}

	// Image Hotspots — attach the diagram, then place pins (x/y percent + label)
	// with a live learner preview above the row editor.
	hotspots_editor(lesson, block) {
		const $box = $('<div class="tc-media"></div>');
		if (block.image) $box.append(this.render_learner(block));
		else $('<div class="tc-muted"></div>').text(__("Attach a diagram image, then place hotspots on it.")).appendTo($box);
		const $btn = $('<button class="btn btn-default btn-sm"></button>').text(block.image ? __("Replace image") : __("Attach image"));
		$btn.on("click", () => this.attach_media(lesson, block, "image", "image/*"));
		if (!this.editable()) $btn.attr("disabled", "disabled");
		$box.append($btn);
		$box.append(this.list_editor(lesson, block, "hotspots", (row, val, set) => this.hotspot_row(row, val, set)));
		return $box;
	}

	hotspot_row($row, val, set) {
		val = val && typeof val === "object" ? val : { x: 50, y: 50, label: "" };
		const $x = $('<input type="number" min="0" max="100" class="form-control tc-xy" />').val(this.num(val.x));
		const $y = $('<input type="number" min="0" max="100" class="form-control tc-xy" />').val(this.num(val.y));
		const $l = $('<input type="text" class="form-control" />').attr("placeholder", __("Label")).val(val.label || "");
		const upd = () => set({ x: this.num($x.val()), y: this.num($y.val()), label: $l.val() });
		[$x, $y, $l].forEach(($i) => $i.on("input", upd));
		if (!this.editable()) [$x, $y, $l].forEach(($i) => $i.attr("disabled", "disabled"));
		$row.append($('<div class="tc-hotspot-row"></div>').append($("<span>x%</span>"), $x, $("<span>y%</span>"), $y, $l));
	}

	pick_file(accept) {
		return new Promise((resolve) => {
			const input = document.createElement("input");
			input.type = "file";
			if (accept) input.accept = accept;
			input.onchange = () => resolve((input.files && input.files[0]) || null);
			input.click();
		});
	}

	// XMLHttpRequest, not fetch: a large media file needs upload progress, which
	// fetch has no event for. Mirrors the classic builder's upload idiom (private
	// file on Training Lesson).
	upload(file, extra) {
		return new Promise((resolve, reject) => {
			const form = new FormData();
			form.append("file", file, file.name);
			form.append("is_private", "1");
			Object.entries(extra || {}).forEach(([key, value]) => {
				if (value) form.append(key, value);
			});
			const xhr = new XMLHttpRequest();
			xhr.open("POST", "/api/method/upload_file", true);
			xhr.setRequestHeader("X-Frappe-CSRF-Token", frappe.csrf_token);
			xhr.onload = () => {
				let data = null;
				try {
					data = JSON.parse(xhr.responseText);
				} catch (e) {
					data = null;
				}
				const url = data && data.message && data.message.file_url;
				if (xhr.status >= 200 && xhr.status < 300 && url) resolve(data.message);
				else reject(new Error(__("Upload failed.")));
			};
			xhr.onerror = () => reject(new Error(__("Upload failed.")));
			xhr.send(form);
		});
	}

	attach_media(lesson, block, field, accept) {
		if (!this.editable()) return;
		this.pick_file(accept).then((file) => {
			if (!file) return;
			frappe.show_alert({ message: __("Uploading {0}…", [file.name]), indicator: "blue" }, 8);
			this.upload(file, { doctype: "Training Lesson", docname: lesson.name || "" })
				.then((result) => {
					block[field] = result.file_url;
					this.dirty_blocks(lesson);
					this.mark_dirty();
					this.rerender_block(lesson, block);
					frappe.show_alert({ message: __("Attached."), indicator: "green" }, 3);
				})
				.catch((e) => frappe.msgprint({ message: (e && e.message) || __("Upload failed."), indicator: "red" }));
		});
	}

	// ---------------------------------------------------- interactive editors
	interactive_editor(lesson, block) {
		const type = block.block_type;
		if (type === "Checklist") return this.list_editor(lesson, block, "items", (row, val, set) => this.text_row(row, val, set, __("Checklist item")));
		if (type === "Flashcards") return this.list_editor(lesson, block, "cards", (row, val, set) => this.card_row(row, val, set));
		if (type === "Accordion") return this.list_editor(lesson, block, "panels", (row, val, set) => this.panel_row(row, val, set));
		return $("<div></div>");
	}

	list_editor(lesson, block, key, rowFn) {
		const data = this.block_data(block);
		let rows = Array.isArray(data[key]) ? data[key] : [];
		const $ed = $(`<div class="tc-ied" role="group"><div class="tc-ied-rows"></div><button class="tc-ied-add">+ ${__("Add")}</button></div>`);
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
				$('<button class="tc-ied-del" aria-label="' + __("Remove") + '">✕</button>').on("click", () => { rows.splice(i, 1); commit(true); }).appendTo($row);
				$rows.append($row);
			});
		};
		$ed.find(".tc-ied-add").on("click", () => { rows = rows.concat([this.blank_row(key)]); commit(true); });
		if (!this.editable()) $ed.find(".tc-ied-add").attr("disabled", "disabled");
		paint();
		return $ed;
	}

	blank_row(key) {
		if (key === "cards") return { front: "", back: "" };
		if (key === "panels") return { title: "", body: "<p></p>" };
		if (key === "hotspots") return { x: 50, y: 50, label: "" };
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
				const on = (block.callout_tone || "Info").toLowerCase() === val.toLowerCase();
				$(`<button class="${on ? "is-on" : ""}"></button>`).text(label).on("click", () => {
					block.callout_tone = val;
					this.dirty_blocks(lesson);
					this.mark_dirty();
					this.rerender_block(lesson, block);
					$seg.find("button").removeClass("is-on");
				}).appendTo($seg);
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
			$("<button></button>").text(type).on("click", () => { $menu.remove(); this.add_block(lesson, type, index); }).appendTo($menu);
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
		else if (type === "Callout") { block.content = "<p></p>"; block.callout_tone = "Info"; }
		else if (type === "Checklist") block.data = JSON.stringify({ items: [""] });
		else if (type === "Flashcards") block.data = JSON.stringify({ cards: [{ front: "", back: "" }] });
		else if (type === "Accordion") block.data = JSON.stringify({ panels: [{ title: "", body: "<p></p>" }] });
		else if (type === "Image Hotspots") block.data = JSON.stringify({ hotspots: [] });
		lesson.blocks.splice(index, 0, block);
		this.dirty_blocks(lesson);
		this.mark_dirty();
		this.render_sheet();
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
		lesson.blocks = lesson.blocks.filter((b) => b !== block);
		this.dirty_blocks(lesson);
		this.mark_dirty();
		this.render_sheet();
	}

	// --------------------------------------------------------- dirty + save
	dirty_lesson(lesson) {
		const key = lesson.name || lesson.__temp;
		if (!this.dirty.lessons[key]) this.dirty.lessons[key] = lesson.name ? { name: lesson.name } : { temp_id: lesson.__temp };
		return this.dirty.lessons[key];
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
		return Object.keys(this.dirty.lessons).length > 0 || this.dirty.deleted.length > 0 || !!this.dirty.chapters;
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
		this.dirty = { lessons: {}, deleted: [], chapters: null };
		this._saving = true;
		this.paint_status("saving");
		const payload = {
			lessons: Object.values(sent.lessons),
			deleted_lessons: sent.deleted.slice(),
			chapters: sent.chapters || undefined,
		};
		return frappe
			.call({
				method: "erpnext_enhancements.api.training_author.save_draft_version",
				args: { course_version: this.version.name, payload: JSON.stringify(payload), modified: this.version.modified },
			})
			.then((r) => {
				const state = (r && r.message) || {};
				this._saving = false;
				if (state.modified) this.version.modified = state.modified;
				if (state.chapters) this.chapters = state.chapters;
				this.adopt_created(state.created_lessons);
				this.report_rejected(state.rejected);
				this.paint_status(this.has_dirty() ? "dirty" : "saved");
				if (this.has_dirty()) this.mark_dirty();
				return state;
			})
			.catch((error) => {
				this._saving = false;
				// Put the batch back on top of anything typed since.
				Object.entries(sent.lessons).forEach(([k, patch]) => {
					this.dirty.lessons[k] = Object.assign(patch, this.dirty.lessons[k] || {});
				});
				this.dirty.deleted = Array.from(new Set(sent.deleted.concat(this.dirty.deleted)));
				this.dirty.chapters = this.dirty.chapters || sent.chapters || null;
				const message = String((error && error.message) || "");
				if (/modif|stale|out of date|newer|conflict/i.test(message)) this.enter_conflict();
				else this.paint_status("dirty");
				throw error;
			});
	}

	adopt_created(created) {
		if (!created || !created.length) return;
		created.forEach(({ temp_id, name }) => {
			const lesson = this.lessons.find((l) => l.__temp === temp_id);
			if (!lesson) return;
			if (this.lesson_name === lesson.__temp) this.lesson_name = name;
			lesson.name = name;
			delete lesson.__temp;
		});
		this.render_rail();
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
		this.render_rail();
		this.render_sheet();
		if (!this.$lessonset.prop("hidden")) this.render_lesson_settings();
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
