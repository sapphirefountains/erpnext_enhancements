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
	// Reachable at last. The server has allowlisted `transcript` and round-tripped it
	// on the bootstrap all along, but it was absent HERE -- and `set_lesson_field`
	// silently returns on a field outside this array, so it was unreachable by
	// construction with no error to notice. It is also the precondition for AI
	// checkpoint drafting, which refuses without cue timings.
	"transcript",
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
		this.page.add_menu_item(__("Preview as a learner"), () => this.open_preview());
		this.page.add_menu_item(__("Chapters…"), () => this.open_chapters());
		this.page.add_menu_item(__("New draft version"), () => this.new_draft());
		this.page.add_menu_item(__("Submit for review"), () => this.submit_for_review());
		this.page.add_menu_item(__("Publish…"), () => this.publish());

		// The autosave debounce is 1200ms, so a tab closed a second after the last
		// keystroke loses it. The browser prompt is the only thing between the author
		// and that, and the canvas had none at all.
		this._unload = (event) => {
			if (!this.has_dirty()) return undefined;
			this.save();
			event.preventDefault();
			event.returnValue = "";
			return "";
		};
		$(window).on("beforeunload.training_canvas", this._unload);
		// A tablet locking mid-edit on site is the common case here, and it fires
		// visibilitychange rather than beforeunload. Save, never prompt -- a prompt on
		// a backgrounding tab is one nobody sees.
		$(document).on("visibilitychange.training_canvas", () => {
			if (document.visibilityState === "hidden" && this.has_dirty()) this.save();
		});
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

	// ONE caller, in `video_editor` -- registering a new video from Drive. That is
	// the whole remaining reason to open the classic builder, and keeping this method
	// down to a single call site is what makes that checkable (a test asserts it).
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
		this.readiness = data.readiness || null;
		this.ai_enabled = !!data.ai_enabled;
		this.ai_drafts = { kind: null, lesson: null, block_key: null, items: [], message: "", busy: false };
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
		this.render_readiness();
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
		// Flush FIRST. `.filter(Boolean)` drops any lesson created this session, because
		// it has no `name` until the save comes back -- and reorder_lessons renumbers only
		// what it was given, so dragging a new lesson to the top silently left it where it
		// was. After the flush every lesson has a real name.
		// This caller genuinely wants to give up quietly: the rail has already been
		// re-rendered optimistically, and a failed autosave has surfaced its own error.
		// The swallow lives HERE now rather than inside flush_save, where it was hiding
		// failures from every other caller too.
		this.save_then(__("Saving the lesson order")).then(() => {
			const names = this.lessons.map((l) => l.name).filter(Boolean);
			if (!names.length) return;
			return frappe
				.call({
					method: "erpnext_enhancements.api.training_author.reorder_lessons",
					args: { course_version: this.version.name, order: JSON.stringify(names) },
				})
				.then(() => frappe.show_alert({ message: __("Lesson order saved."), indicator: "green" }, 3))
				.catch(() => this.render_rail());
		}).catch(() => {});
	}

	load_vtt(lesson) {
		// Read LOCALLY, never uploaded. The transcript is a lesson field; a round trip
		// through File storage would leave a second copy nobody maintains beside the one
		// that is actually read. Same reasoning as the classic builder.
		const input = document.createElement("input");
		input.type = "file";
		input.accept = ".vtt,text/vtt";
		input.onchange = () => {
			const file = (input.files && input.files[0]) || null;
			if (!file) return;
			const reader = new FileReader();
			reader.onload = () => {
				const text = String(reader.result || "");
				if (!/-->/.test(text)) {
					// Refuse rather than accept it silently: without cue timings AI
					// checkpoint drafting has nothing to place a question against, and it
					// would refuse later with no clue why.
					frappe.msgprint({
						title: __("That is not a timed transcript"),
						indicator: "red",
						message: __(
							"A .vtt carries cue timings (00:01:02.000 --> 00:01:06.000). Without them checkpoint drafting has nothing to place a question against and will refuse."
						),
					});
					return;
				}
				this.set_lesson_field(lesson, "transcript", text);
				this.render_lesson_settings();
			};
			reader.readAsText(file);
		};
		input.click();
	}

	// -------------------------------------------------------------- chapters
	open_chapters() {
		"use strict";
		// Chapters were unreachable from this page by construction: `this.chapters` was
		// read in four places and written only from the bootstrap, and `dirty.chapters`
		// was read in three and written by NOTHING. The picker in lesson settings hides
		// itself when the array is empty, so a course authored start to finish here had
		// every lesson Unfiled with no way out -- and the only thing that could ever put
		// a chapter in that array was the classic builder, the tool being retired.
		//
		// The server wire was complete the whole time: `_apply_chapters` mints the keys,
		// refuses to orphan a lesson, and hands the generated keys back in `chapters`,
		// which `save()` already adopts. This is the missing client half.
		if (!this.editable()) return;
		const rows = (this.chapters || []).map((c) => ({
			chapter_key: c.chapter_key || "",
			chapter_title: c.chapter_title || "",
		}));

		const dialog = new frappe.ui.Dialog({
			title: __("Chapters"),
			fields: [{ fieldtype: "HTML", fieldname: "list" }],
			primary_action_label: __("Save chapters"),
			primary_action: () => {
				const kept = rows.filter((r) => (r.chapter_title || "").trim());
				// Sent even when empty: an empty array is how the author deletes the last
				// chapter, and the server refuses it if lessons still point at one.
				this.dirty.chapters = kept.map((r) => ({
					chapter_key: r.chapter_key || undefined,
					chapter_title: r.chapter_title.trim(),
				}));
				this.chapters = kept.slice();
				this.mark_dirty();
				dialog.hide();
				this.render_rail();
				this.render_lesson_settings();
			},
		});

		const $wrap = dialog.fields_dict.list.$wrapper;
		const paint = () => {
			$wrap.empty();
			if (!rows.length) {
				$("<p class='text-muted'></p>")
					.text(__("No chapters yet. Every lesson sits under Unfiled until you add one."))
					.appendTo($wrap);
			}
			rows.forEach((row, i) => {
				const $r = $("<div class='tc-chapter-row'></div>").appendTo($wrap);
				const $t = $("<input type='text' class='form-control' />")
					.val(row.chapter_title)
					.attr("placeholder", __("Chapter title"));
				$t.on("input", () => { row.chapter_title = $t.val(); });
				$r.append($t);
				const btn = (label, title, fn) =>
					$("<button class='btn btn-xs btn-default'></button>")
						.text(label).attr("title", title)
						.on("click", () => { fn(); paint(); }).appendTo($r);
				btn("↑", __("Move up"), () => { if (i > 0) rows.splice(i - 1, 0, rows.splice(i, 1)[0]); });
				btn("↓", __("Move down"), () => { if (i < rows.length - 1) rows.splice(i + 1, 0, rows.splice(i, 1)[0]); });
				btn("✕", __("Remove"), () => { rows.splice(i, 1); });
			});
			$("<button class='btn btn-sm btn-default'></button>")
				.text(__("Add chapter"))
				.on("click", () => { rows.push({ chapter_key: "", chapter_title: "" }); paint(); })
				.appendTo($wrap);
		};
		paint();
		dialog.show();
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

		// Rendered ALWAYS. Gating it on `this.chapters.length` is what made chapters
		// unreachable: no chapters meant no control, and the control was the only place
		// they were mentioned, so a canvas-authored course could never leave Unfiled.
		{
			const $ch = $('<select class="form-control"></select>');
			$('<option value=""></option>').text(__("Unfiled")).appendTo($ch);
			this.chapters.forEach((c) => $("<option></option>").attr("value", c.chapter_key).text(c.chapter_title).appendTo($ch));
			$ch.val(lesson.chapter_key || "");
			$ch.on("change", () => { this.set_lesson_field(lesson, "chapter_key", $ch.val()); this.render_rail(); });
			field(__("Chapter"), $ch);
			if (!this.chapters.length && ed) {
				$('<button class="btn btn-xs btn-default tc-add-chapter"></button>')
					.text(__("Add a chapter"))
					.on("click", () => this.open_chapters())
					.appendTo(this.$lessonset);
			}
		}

		// Reachable at last -- see TC_LESSON_FIELDS. The server has allowlisted and
		// round-tripped `transcript` all along; it was missing from the client allowlist,
		// and set_lesson_field silently returns on a field outside it, so there was no
		// error to notice. It is also what AI checkpoint drafting refuses without.
		const $tr = $('<textarea class="form-control" rows="4"></textarea>')
			.val(lesson.transcript || "")
			.attr("placeholder", __("Plain text, or WebVTT cues if you have them."));
		$tr.on("input", () => this.set_lesson_field(lesson, "transcript", $tr.val()));
		field(__("Transcript"), $tr);
		if (ed) {
			$('<button class="btn btn-xs btn-default tc-vtt"></button>')
				.text(__("Load a .vtt"))
				.on("click", () => this.load_vtt(lesson))
				.appendTo(this.$lessonset);
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

		// AI drafting. The drawer is the ONLY surface on this site that can stamp
		// `ai_reviewed_by`, and `publish_version` refuses a course holding an
		// `ai_generated` question without one -- so an AI-authored course that never
		// passes through here can never be published at all.
		if (this.ai_enabled) {
			const $ai = $('<div class="tc-ai-bar"></div>').appendTo(this.$lessonset);
			$('<button class="btn btn-default btn-xs"></button>')
				.text(__("Draft quiz questions with AI"))
				.on("click", () => this.draft_questions(lesson))
				.appendTo($ai);
			$('<div class="tc-muted"></div>')
				.text(__("Drafts are suggestions. Nothing is written until you accept it, and accepting is what records you as the reviewer."))
				.appendTo($ai);
			const $drawer = this.render_ai_drawer(lesson);
			if ($drawer) this.$lessonset.append($drawer);
		}

		$('<div class="tc-hint"></div>')
			.html(__("In-video checkpoints are placed on the video block's timeline. Quiz questions themselves are still listed in the classic builder."))
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
	// ---------------------------------------------------- publish readiness
	//
	// What publish would refuse, shown while there is still time to fix it. The
	// sentences come from the SAME two builders the publish gate reads
	// (`unfinished_block_problems` / `unfinished_checkpoint_problems`), so an author
	// cannot satisfy this panel and then be refused, or satisfy the refusal and
	// wonder why the panel still complains.
	//
	// It is deliberately NOT recomputed in JavaScript. `training_canvas.js` already
	// records the ruling that a third client-side copy of an emptiness test is not a
	// second line of defence, it is the exact mismatch that lets a compliance course
	// lose its teeth unnoticed.
	render_readiness() {
		this.$app.find(".tc-readiness").remove();
		const r = this.readiness;
		if (!r) return;
		const blocks = r.blocks || [];
		const checkpoints = r.checkpoints || [];
		const total = blocks.length + checkpoints.length;
		const $panel = $('<div class="tc-readiness"></div>');
		if (!total) {
			$panel.addClass("is-ready");
			$('<div class="tc-readiness-head"></div>').text(__("Ready to publish")).appendTo($panel);
			this.$rail.append($panel);
			return;
		}
		$('<div class="tc-readiness-head"></div>')
			.text(__("{0} thing(s) to finish before this can be published").format([total]))
			.appendTo($panel);
		const $list = $('<ul class="tc-readiness-list"></ul>').appendTo($panel);
		blocks.forEach((line) => $("<li></li>").text(line).appendTo($list));
		// No "where to fix this" suffix any more: since v1.413.0 every problem
		// `Training Checkpoint.incomplete_reasons()` reports -- no question typed,
		// fewer than two options, none ticked correct -- is fixable in the pin
		// inspector on this page. The line IS the remedy now.
		checkpoints.forEach((line) => $("<li></li>").text(line).appendTo($list));
		if (r.truncated) {
			$('<div class="tc-readiness-more"></div>')
				.text(__("…and more. Publishing lists every one."))
				.appendTo($panel);
		}
		this.$rail.append($panel);
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

	render_ctx() {
		return { t: (text) => __(text) };
	}

	to_render_block(block) {
		return Object.assign(
			{
				block_key: block.block_key,
				type: block.block_type,
				heading: block.heading || "",
				// NOT sanitised here, deliberately. This is an EDITING surface, and on an
				// edit-in-place surface the render path IS the write path: blocks.js assigns
				// this string to innerHTML, wire_inline_edit makes that node contenteditable,
				// and its input handler writes `body.innerHTML` straight back to `block.content`.
				// `frappe.utils.xss_sanitise` ESCAPES rather than sanitises, so running it here
				// meant one keystroke persisted `&lt;p&gt;` into the lesson, compounding on every
				// later edit. Invisible downstream twice over: `_sanitize_content` short-circuits
				// when a value holds no literal < or >, and `sanitize_html` returns early when
				// BeautifulSoup finds no element. Safety is not lost -- `content` is a Text Editor
				// field, so the server runs nh3 on every save and `_split_lesson` sanitises again
				// at publish. A third client-side copy is not a second line of defence, it is the
				// corruption; same fix and same reasoning as visit_wizard.js. The sibling call in
				// interactive_keys() STAYS: Accordion bodies live in `data`, fieldtype Code, which
				// `_sanitize_content` explicitly skips -- there the escape is the only protection,
				// and nothing ever writes back to it.
				html: block.content || "",
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
					<button class="tc-tool" data-act="turn" title="${__("Turn into…")}" aria-label="${__("Turn into")}">⇄</button>
					<button class="tc-tool" data-act="dup" title="${__("Duplicate")}" aria-label="${__("Duplicate")}">⧉</button>
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
		$wrap.find('[data-act="turn"]').on("click", (e) => this.open_turn_menu(lesson, block, $(e.currentTarget)));
		$wrap.find('[data-act="dup"]').on("click", () => this.duplicate_block(lesson, block));
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
	// poster / coverage gate. Checkpoints are placed HERE as of v1.413.0.
	//
	// Registering a NEW video is the one authoring job that still lives only in the
	// classic builder, and the hand-off at the bottom of this editor is the only
	// door to it left after R1 (v1.416.0). Deliberately not replaced by "just make
	// the record in the Desk": `duration_source` is read_only, so a hand-made row
	// cannot be corrected to Manual, and until v1.416.0 it also defaulted to
	// "Probed" -- which made `grading._duration_is_verified` enforce the coverage
	// gate against a duration nobody measured. Waiving beats gating on a guess.
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
		// The timeline. Replaces the standing apology that sent authors to the classic
		// builder to place a pin -- which, until v1.400.0, could not actually be done
		// there either: `add_pin` seeded an empty question against a `reqd` field, so the
		// insert was refused on a four-second autosave.
		$box.append(this.timeline(lesson, block));
		if (this.ai_enabled) {
			$('<button class="btn btn-default btn-xs" style="margin-top:6px"></button>')
				.text(__("Suggest checkpoints with AI"))
				.on("click", () => this.draft_checkpoints(lesson, block))
				.appendTo($box);
			const $d = this.render_ai_drawer(lesson);
			if ($d && this.ai_drafts.kind === "checkpoint") $box.append($d);
		}
		$('<div class="tc-hint"></div>').text(__("Registering a NEW video from Drive is still done in the classic builder — it probes the real length, which watch coverage is measured against.")).appendTo($box);
		$('<button class="btn btn-default btn-xs" style="margin-top:6px"></button>').text(__("Register a video (classic builder)")).on("click", () => this.open_classic()).appendTo($box);
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

	// ------------------------------------------------- turn into / duplicate
	//
	// The whole point of both, and the reason they are one commit: `block_key` is
	// a RELATIONAL IDENTITY, not a detail. Learner watch intervals and in-video
	// checkpoints are filed under it, and `_apply_blocks` replaces the child table
	// wholesale by position, minting a key only where one is blank or duplicated.
	// So the rule is exact and opposite for the two verbs:
	//
	//   Turn into -> KEEP the key. Delete-and-re-add would mint a new one and strand
	//                every learner mid-video, which is what an author would do by
	//                hand without this.
	//   Duplicate -> MINT a fresh one. Two rows sharing a key is the one case the
	//                server rewrites, silently, and the author would never see it.

	//: Where the body of each type lives. `content` is sanitised HTML; `data` is a
	//: JSON blob whose shape differs per type, which is why data->data is a loss.
	block_family(type) {
		if (type === "Rich Text" || type === "Callout") return "content";
		if (["Checklist", "Flashcards", "Accordion", "Image Hotspots"].indexOf(type) >= 0) return "data";
		if (["Image", "Video", "PDF", "Downloadable File", "External Embed"].indexOf(type) >= 0) return "media";
		return "none";
	}

	turn_losses(lesson, block, target) {
		"use strict";
		// Say exactly what goes, in the author's terms, before anything moves.
		const from = this.block_family(block.block_type);
		const to = this.block_family(target);
		const losses = [];

		if (from === "content" && to !== "content" && (block.content || "").trim()) {
			losses.push(__("the written text"));
		}
		if (from === "data" && to !== "data" && (block.data || "").trim()) {
			losses.push(__("the items you have entered"));
		}
		if (from === "data" && to === "data") {
			losses.push(__("the items you have entered (the two types store them differently)"));
		}
		if (from === "media" && to !== "media") {
			losses.push(__("the attached file or link"));
		}

		// Checkpoints hang off `block_key` and are reaped server-side when their
		// block stops being a Video. The canvas has had them on the bootstrap all
		// along and thrown them away; naming them by timestamp is the difference
		// between a warning and a surprise.
		if (block.block_type === "Video" && target !== "Video") {
			const pins = (lesson.checkpoints || []).filter((c) => c && c.block_key === block.block_key);
			if (pins.length) {
				const at = pins
					.map((c) => this.mmss(c.at_seconds))
					.join(', ');
				losses.push(
					__("{0} in-video checkpoint(s), at {1}").format([pins.length, at])
				);
			}
		}
		return losses;
	}

	mmss(seconds) {
		const n = Math.max(0, Math.floor(Number(seconds) || 0));
		return Math.floor(n / 60) + ":" + String(n % 60).padStart(2, "0");
	}

	open_turn_menu(lesson, block, $anchor) {
		if (!this.editable()) return;
		this.$app.find(".tc-menu").remove();
		const $menu = $('<div class="tc-menu"></div>');
		TC_ADDABLE.filter((t) => t !== block.block_type).forEach((type) => {
			$("<button></button>")
				.text(type)
				.on("click", () => {
					$menu.remove();
					this.turn_into(lesson, block, type);
				})
				.appendTo($menu);
		});
		$("body").append($menu);
		const r = $anchor[0].getBoundingClientRect();
		$menu.css({ top: r.bottom + 6 + "px", left: Math.min(r.left, window.innerWidth - 200) + "px" });
		setTimeout(() => $(document).one("click.tcturn", () => $menu.remove()), 0);
	}

	turn_into(lesson, block, target) {
		if (!this.editable() || target === block.block_type) return;
		const losses = this.turn_losses(lesson, block, target);
		const apply = () => {
			const from = this.block_family(block.block_type);
			const to = this.block_family(target);
			// The key is NOT touched. That is the entire contract.
			// But the PINS go, in memory as well as on the server. `_reap_orphan_checkpoints`
			// deletes them on the next save because the block has stopped being a Video;
			// leaving them here would repaint ghost pins on a timeline whose `cp.name` points
			// at a deleted document, and the next edit would 404. Not done in
			// `duplicate_block` -- a duplicate must not carry somebody else's questions, and
			// a test asserts that method never mentions checkpoints at all.
			if (block.block_type === "Video" && target !== "Video") {
				lesson.checkpoints = (lesson.checkpoints || []).filter(
					(cp) => cp && cp.block_key !== block.block_key
				);
			}
			block.block_type = target;
			if (!(from === "content" && to === "content")) block.content = "";
			if (!(from === "data" && to === "data")) block.data = "";
			if (to === "data") block.data = "";
			if (from === "media" && to !== "media") {
				block.image = "";
				block.file = "";
				block.embed_url = "";
			}
			if (target === "Callout" && !block.callout_tone) block.callout_tone = "Info";
			if (target === "Rich Text" && !(block.content || "").trim()) block.content = "<p></p>";
			if (target === "Checklist") block.data = JSON.stringify({ items: [""] });
			if (target === "Flashcards") block.data = JSON.stringify({ cards: [{ front: "", back: "" }] });
			if (target === "Accordion") block.data = JSON.stringify({ panels: [{ title: "", body: "<p></p>" }] });
			if (target === "Image Hotspots") block.data = JSON.stringify({ hotspots: [] });
			this.dirty_blocks(lesson);
			this.mark_dirty();
			this.render_sheet();
		};

		if (!losses.length) return apply();
		frappe.confirm(
			__("Turning this into {0} discards {1}. The block keeps its place and its identity, so anything already watched stays counted.").format([
				target,
				losses.join(__(", and ")),
			]),
			apply
		);
	}

	duplicate_block(lesson, block) {
		if (!this.editable()) return;
		const i = lesson.blocks.indexOf(block);
		if (i < 0) return;
		// A FRESH key, minted here rather than left blank. Leaving it blank works --
		// the controller mints one -- but then the new row has no identity until the
		// save returns, and anything addressing it in between addresses the original.
		const copy = Object.assign({}, block, {
			block_key: "blk-" + Math.random().toString(36).slice(2, 10),
		});
		// Checkpoints are NOT copied. They are separate documents filed under the
		// original key, and a duplicate that silently acquired somebody else's
		// questions would be worse than one that acquired none.
		lesson.blocks.splice(i + 1, 0, copy);
		this.dirty_blocks(lesson);
		this.mark_dirty();
		this.render_sheet();
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

	flush_save() {
		// Settle whatever is pending and RESOLVE WHEN IT HAS LANDED. `save()` returns a
		// bare Promise.resolve() while a save is in flight, so anything chained off it
		// runs against the version before the one just typed. Three callers depend on
		// this being honest: the lesson reorder below, and -- once they land -- the pin
		// writer and the preview, both of which resolve their target through the
		// DATABASE, where a block that exists only in memory is simply absent.
		// Same contract as the classic builder.
		//
		// It no longer SWALLOWS. Both exits used to end `.catch(() => {})`, which made a
		// failed save indistinguishable from a clean one to every caller -- so a pin
		// writer or a preview chained off it would fire after the save had thrown, then
		// fail again resolving a block that was never written, and report the wrong
		// cause. `enter_conflict()` on a stale `modified` is exactly that case. A promise
		// that resolves whether or not the work landed is not a flush, it is a delay.
		clearTimeout(this._save_timer);
		if (this._saving && this._inflight) return this._inflight;
		if (!this.has_dirty()) return Promise.resolve();
		return this.save();
	}

	save_then(label) {
		// Actions that must not run against stale content chain off flush_save(). When
		// the save fails the chained action is correctly abandoned -- but whatever dialog
		// the author was in has already closed, and the only thing on screen is frappe's
		// error about the AUTOSAVE. Nothing connects that to the button they pressed, so
		// the thing they asked for simply never happens and no message says why.
		//
		// Named after the classic builder's own save_then, and for the same reason it
		// was written there.
		return this.flush_save().catch((error) => {
			frappe.msgprint({
				title: __("{0} was not done", [label]),
				indicator: "red",
				message: __(
					"Your unsaved edits could not be stored, so {0} was not attempted — going ahead would have thrown them away. Nothing has been lost: the edits are still here. Resolve the error above and try again.",
					[label]
				),
			});
			throw error;
		});
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
		this._inflight = frappe
			.call({
				method: "erpnext_enhancements.api.training_author.save_draft_version",
				args: { course_version: this.version.name, payload: JSON.stringify(payload), modified: this.version.modified },
			})
			.then((r) => {
				const state = (r && r.message) || {};
				this._saving = false;
				if (state.modified) this.version.modified = state.modified;
				if (state.chapters) this.chapters = state.chapters;
				// Which lessons this save actually covered. `_builder_checkpoints` returns a
				// map keyed by lesson and OMITS a lesson with no checkpoints, so an absent
				// key means either 'saved, now has none' or 'not part of this save'. Only
				// the first should clear the list.
				this._saved_names = state.saved || [];
				this.adopt_created(state.created_lessons);
				this.report_rejected(state.rejected);
				// Recomputed server-side on every save, so the advisory tracks the edit that
				// just landed. Note what does NOT happen here: nothing throws and nothing
				// msgprints. A warning on the save path becomes a toast every four seconds on
				// a 1200ms debounce, which is the complaint this whole work item exists to
				// remove -- the panel is a standing fact on the page, not an interruption.
				if (state.readiness !== undefined) this.readiness = state.readiness;
				this.render_readiness();
				this.adopt_checkpoints(state.checkpoints);
				this.paint_status(this.has_dirty() ? "dirty" : "saved");
				// CHAIN, do not re-arm. Keystrokes typed during an in-flight save land in
				// `this.dirty`, and handing them to `mark_dirty()` puts them behind the
				// 1200ms debounce while this promise resolves -- so a caller awaiting
				// flush_save() is told the work is stored when the last keystrokes are
				// still sitting in a timer. `_inflight` must settle only once the queue
				// has DRAINED, which is the whole contract the pin writer and the preview
				// depend on.
				if (this.has_dirty()) return this.save().then(() => state);
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
		return this._inflight;
	}

	open_preview() {
		// The entire client change for the preview, and that is the point. The classic
		// builder carries roughly 640 lines -- load_player, preview_boot, preview_lesson,
		// preview_outline, preview_transport, preview_checkpoint -- whose whole job is to
		// rebuild, in JavaScript, a payload the server already builds correctly in
		// `_split_lesson`. None of it is ported. `/training_preview?course=…` asks the
		// server for the bytes publish would write.
		//
		// Chained off the save because opening the preview before the flush lands shows
		// the author the PREVIOUSLY saved draft while their screen shows newer text --
		// the silent staleness the classic shipped and only ever fixed for publish. A
		// lesson created this session also has no `lesson_key` until the save returns, so
		// the URL would otherwise carry `undefined` and land on lesson one.
		if (!this.course) return;
		this.save_then(__("Opening the preview")).then(() => {
			const lesson = this.current_lesson() || {};
			const url = "/training_preview?course=" + encodeURIComponent(this.course.name) +
				(lesson.lesson_key ? "&lesson=" + encodeURIComponent(lesson.lesson_key) : "");
			window.open(url, "_blank");
		}).catch(() => {});
	}

	// ------------------------------------------------------- AI drafting
	//
	// The engine already existed -- `api/training_ai.py` has `draft_quiz_questions`,
	// `suggest_checkpoints` and `accept_ai_suggestions` -- and the canvas dialled
	// none of it. That is why this is the blocker for retiring the classic builder
	// rather than a nicety: `publish_version` refuses any course holding an
	// `ai_generated` question with no `ai_reviewed_by`, `accept_ai_suggestions` is
	// the ONLY thing that stamps a reviewer, and the classic's quiz section is
	// read-only with no hand-add anywhere. Production is not blocked today only
	// because the four spec-authored courses have zero quiz rows. The next one will.

	ai_reset() {
		this.ai_drafts = { kind: null, lesson: null, block_key: null, items: [], message: "", busy: false };
	}

	draft_questions(lesson) {
		// Chained off the save for a reason peculiar to this endpoint: `_lesson()`
		// resolves the lesson FROM THE DATABASE and `draft_quiz_questions` refuses
		// below MIN_SOURCE_CHARS (120). So drafting against unflushed edits does not
		// merely use stale text -- it tells the author there is not enough written
		// content in a lesson that is visibly full on their screen.
		this.ai_drafts = { kind: "quiz", lesson: lesson.name, block_key: null, items: [], message: "", busy: true };
		this.render_sheet();
		return this.save_then(__("Drafting questions"))
			.then(() =>
				frappe.call({
					method: "erpnext_enhancements.api.training_ai.draft_quiz_questions",
					args: { lesson: lesson.name },
				})
			)
			.then((r) => this.adopt_drafts((r && r.message) || {}))
			.catch(() => this.ai_failed());
	}

	draft_checkpoints(lesson, block) {
		// `suggest_checkpoints` refuses without a TIMED transcript, and production's
		// only video asset has transcript_source "None" -- so this feature correctly
		// refuses on the only video that exists until somebody uses the .vtt loader
		// the canvas already has. The refusal says so; do not pre-empt it here with a
		// guess about what the server will accept.
		this.ai_drafts = {
			kind: "checkpoint",
			lesson: lesson.name,
			block_key: block.block_key,
			items: [],
			message: "",
			busy: true,
		};
		this.render_sheet();
		return this.save_then(__("Drafting checkpoints"))
			.then(() =>
				frappe.call({
					method: "erpnext_enhancements.api.training_ai.suggest_checkpoints",
					args: { lesson: lesson.name, block_key: block.block_key },
				})
			)
			.then((r) => this.adopt_drafts((r && r.message) || {}))
			.catch(() => this.ai_failed());
	}

	adopt_drafts(payload) {
		this.ai_drafts.busy = false;
		this.ai_drafts.items = (payload.suggestions || []).map((item, i) =>
			Object.assign({ id: "d" + i }, item)
		);
		this.ai_drafts.message = payload.message || "";
		this.render_sheet();
	}

	ai_failed() {
		// The server's own message has already been shown by frappe. Just stop
		// claiming to be busy -- a drawer stuck on "Drafting…" reads as a hang.
		this.ai_reset();
		this.render_sheet();
	}

	accept_draft(lesson, item) {
		// THIS CALL IS THE HUMAN REVIEW. `accept_ai_suggestions` stamps
		// `ai_generated` and `ai_reviewed_by` together -- the pair
		// `_unreviewed_ai_questions` reads -- so it is the only thing that can let an
		// AI-drafted course publish.
		return frappe
			.call({
				method: "erpnext_enhancements.api.training_ai.accept_ai_suggestions",
				args: {
					lesson: lesson.name,
					kind: this.ai_drafts.kind,
					suggestions: JSON.stringify([item]),
				},
			})
			.then(() => {
				item.accepted = true;
				frappe.show_alert({ message: __("Accepted."), indicator: "green" }, 3);
				this.render_sheet();
			});
	}

	render_ai_drawer(lesson) {
		const d = this.ai_drafts || {};
		if (d.lesson !== lesson.name) return null;
		if (!d.items.length && !d.busy && !d.message) return null;
		const $drawer = $('<div class="tc-ai"></div>');
		const $head = $('<div class="tc-ai-head"></div>').appendTo($drawer);
		$("<span></span>")
			.text(d.kind === "checkpoint" ? __("Drafted checkpoints") : __("Drafted questions"))
			.appendTo($head);
		// "Reject all" exists and "Accept all" deliberately does NOT. Accepting IS the
		// human review the publish gate is built on; a button that performs it in bulk
		// without anyone reading anything makes the gate ornamental.
		$('<button type="button" class="btn btn-default btn-xs"></button>')
			.text(__("Reject all"))
			.on("click", () => {
				this.ai_reset();
				this.render_sheet();
			})
			.appendTo($head);
		if (d.busy) {
			$('<div class="tc-muted"></div>').text(__("Drafting…")).appendTo($drawer);
			return $drawer;
		}
		if (d.message) $('<div class="tc-ai-warn"></div>').text(d.message).appendTo($drawer);
		if (!d.items.length) {
			$('<div class="tc-muted"></div>').text(__("Nothing to review.")).appendTo($drawer);
			return $drawer;
		}
		d.items.forEach((item) => $drawer.append(this.ai_card(lesson, item)));
		return $drawer;
	}

	ai_card(lesson, item) {
		const $card = $('<div class="tc-ai-card"></div>');
		if (item.accepted) $card.addClass("is-accepted");
		if (!item.grounding_quote) $card.addClass("is-ungrounded");
		if (item.at_seconds != null) {
			$('<div class="tc-ai-at"></div>').text(this.mmss(item.at_seconds)).appendTo($card);
		}
		$('<div class="tc-ai-q"></div>').text(item.question || "").appendTo($card);
		const $opts = $('<ul class="tc-ai-options"></ul>').appendTo($card);
		(item.options || []).forEach((o) => {
			$("<li></li>")
				.toggleClass("is-correct", !!Number(o.is_correct))
				.text(o.text || o.option_text || "")
				.appendTo($opts);
		});
		if (item.grounding_quote) {
			$('<div class="tc-ai-quote"></div>').text("“" + item.grounding_quote + "”").appendTo($card);
		} else {
			// The server already drops anything it cannot trace back to the lesson, so
			// this is belt and braces -- but an ungrounded suggestion is the one a
			// reviewer must read hardest, and it should not look like the others.
			$('<div class="tc-ai-warn"></div>')
				.text(__("No grounding quote — this may not come from the lesson at all."))
				.appendTo($card);
		}
		if (item.accepted) {
			$('<div class="tc-ai-done"></div>').text(__("Accepted — reviewed by you.")).appendTo($card);
			return $card;
		}
		const $actions = $('<div class="tc-ai-actions"></div>').appendTo($card);
		$('<button type="button" class="btn btn-primary btn-xs"></button>')
			.text(__("Accept"))
			.on("click", () => this.accept_draft(lesson, item))
			.appendTo($actions);
		$('<button type="button" class="btn btn-default btn-xs"></button>')
			.text(__("Reject"))
			.on("click", () => {
				this.ai_drafts.items = this.ai_drafts.items.filter((row) => row.id !== item.id);
				this.render_sheet();
			})
			.appendTo($actions);
		return $card;
	}

	// ------------------------------------------------- in-video checkpoints
	//
	// Ported from the classic builder, and the port is NOT a copy. Twelve members
	// the classic's pin code calls do not exist here -- `save_then` did not until
	// v1.411.0, and `paint_save_state`, `render_canvas`, `render_inspector`,
	// `guard_editable`, `set_block_field`, `pins_for`, `seek_preview`, `_pending_pins`
	// and the module-level `tb_mmss` still do not. Each is named and shimmed below
	// rather than assumed, because the thing that would have broken quietly is the
	// rule that matters most: `checkpoint_key` is SERVER-OWNED. Learner answers are
	// filed under it, and the one place this file may mint anything is the transient
	// "cp-" below, which is thrown away the moment a real key comes back.

	timeline(lesson, block) {
		const duration = Math.max(1, this.num(block.video_duration_seconds));
		const $wrap = $('<div class="tc-timeline-wrap"></div>');
		if (!this.num(block.video_duration_seconds)) {
			$('<div class="tc-muted"></div>')
				.text(__("Pick a video asset first — its duration is what the timeline is measured against."))
				.appendTo($wrap);
			return $wrap;
		}
		const $strip = $('<div class="tc-timeline"></div>').appendTo($wrap);
		$('<div class="tc-timeline-scale"></div>')
			.text("0:00 — " + this.mmss(duration))
			.appendTo($wrap);
		if (this.editable()) {
			$strip.on("click", (e) => {
				// Clicks on a pin stop propagation, so reaching here means empty strip.
				this.add_pin(lesson, block, this.seconds_at(e.clientX, $strip, duration));
			});
			$('<div class="tc-timeline-help"></div>')
				.text(__("Click the strip to place a checkpoint."))
				.appendTo($wrap);
		}
		// Remembered so `refresh_pins` can repaint without re-rendering the sheet.
		this._timeline = { lesson, block, $strip, duration };
		this.paint_pins(lesson, block, $strip, duration);
		$wrap.append($('<div class="tc-pin-inspector"></div>'));
		setTimeout(() => this.render_pin_inspector(), 0);
		return $wrap;
	}

	//: The selected pin's editor. Deliberately compact and deliberately COMPLETE:
	//: a pin you can place but not finish is worse than no pin at all, because an
	//: unfinished checkpoint is what `_require_finished_checkpoints` refuses at publish
	//: and what `grading._unanswered_checkpoints` would hold a learner on forever.
	render_pin_inspector() {
		const strip = this._timeline;
		if (!strip) return;
		const $host = strip.$strip.closest(".tc-timeline-wrap").find(".tc-pin-inspector");
		if (!$host.length) return;
		$host.empty();
		const cp = this.pins_for(strip.lesson, strip.block).find(
			(row) => row.checkpoint_key === this.checkpoint_key
		);
		if (!cp) return;
		const touch = () => this.persist_checkpoint(strip.lesson, cp);

		$('<div class="tc-pin-at"></div>').text(__("Checkpoint at {0}", [this.mmss(cp.at_seconds)])).appendTo($host);

		const $q = $('<textarea class="form-control" rows="2"></textarea>')
			.attr("placeholder", __("What are you asking?"))
			.val(cp.question_text || "");
		$q.on("change", () => { cp.question_text = $q.val(); touch(); });
		$host.append($q);

		const $type = $('<select class="form-control"></select>');
		["Single Choice", "Multiple Choice", "True-False"].forEach((t) =>
			$("<option></option>").attr("value", t).text(t).appendTo($type)
		);
		$type.val(cp.question_type || "Single Choice");
		$type.on("change", () => { cp.question_type = $type.val(); touch(); });
		$host.append($('<label class="tc-set"></label>').append($("<span></span>").text(__("Type")), $type));

		(cp.options || []).forEach((opt, i) => {
			const $row = $('<div class="tc-pin-option"></div>');
			const $ok = $('<input type="checkbox" />').prop("checked", !!Number(opt.is_correct));
			$ok.on("change", () => {
				// Single Choice and True-False allow exactly one correct option, and the
				// controller THROWS on two -- one of the few contradictions that still
				// refuse at save time. Enforce it here so the author never meets that.
				if ($ok.prop("checked") && cp.question_type !== "Multiple Choice") {
					(cp.options || []).forEach((o) => { o.is_correct = 0; });
				}
				opt.is_correct = $ok.prop("checked") ? 1 : 0;
				touch();
				this.render_pin_inspector();
			});
			const $text = $('<input type="text" class="form-control" />')
				.attr("placeholder", __("Option {0}", [i + 1]))
				.val(opt.option_text || "");
			$text.on("change", () => { opt.option_text = $text.val(); touch(); });
			$row.append($ok, $text).appendTo($host);
		});

		const $add = $('<button class="btn btn-default btn-xs"></button>').text(__("Add option"));
		$add.on("click", () => {
			cp.options = cp.options || [];
			cp.options.push({ option_key: "o" + (cp.options.length + 1), option_text: "", is_correct: 0 });
			touch();
			this.render_pin_inspector();
		});
		const $del = $('<button class="btn btn-default btn-xs"></button>').text(__("Delete checkpoint"));
		$del.on("click", () => {
			frappe.confirm(__("Delete this checkpoint? Any answers already recorded against it go with it."), () =>
				this.delete_checkpoint(strip.lesson, cp)
			);
		});
		$host.append($('<div class="tc-pin-actions"></div>').append($add, $del));
		if (!this.editable()) $host.find("input, select, textarea, button").attr("disabled", "disabled");
	}

	//: This block's pins, in time order. Reads `lesson.checkpoints`, which since
	//: v1.412.0 the server refreshes on every save -- so a pin reaped by
	//: `_reap_orphan_checkpoints` is gone from here too rather than lingering as a
	//: ghost whose edit 404s.
	pins_for(lesson, block) {
		return (lesson.checkpoints || [])
			.filter((cp) => cp && cp.block_key === block.block_key)
			.sort((a, b) => (Number(a.at_seconds) || 0) - (Number(b.at_seconds) || 0));
	}

	// The strip has 10px of padding at each end, so a pin's position is 10px plus
	// its share of the remaining width. A bare percentage drifts by up to 20px
	// across the strip -- enough that a pin visibly does not sit on the tick it was
	// dropped at, which reads as the timestamps being wrong. Verbatim from the
	// classic: it is pure arithmetic and there is nothing to adapt.
	offset_for(seconds, duration) {
		const ratio = Math.max(0, Math.min(1, (Number(seconds) || 0) / Math.max(1, duration)));
		return "calc(10px + " + ratio + " * (100% - 20px))";
	}

	seconds_at(clientX, $strip, duration) {
		const rect = $strip[0].getBoundingClientRect();
		const ratio = (clientX - rect.left - 10) / Math.max(1, rect.width - 20);
		return Math.round(Math.max(0, Math.min(1, ratio)) * duration);
	}

	checkpoint_doc(lesson, cp) {
		return {
			doctype: "Training Checkpoint",
			lesson: lesson.name,
			block_key: cp.block_key,
			at_seconds: Number(cp.at_seconds) || 0,
			question_type: cp.question_type,
			question_text: cp.question_text || "",
			explanation: cp.explanation || "",
			pause_video: Number(cp.pause_video) || 0,
			allow_skip: Number(cp.allow_skip) || 0,
			max_attempts: Number(cp.max_attempts) || 0,
			rewind_seconds_on_wrong: Number(cp.rewind_seconds_on_wrong) || 0,
			counts_toward_score: Number(cp.counts_toward_score) || 0,
			options: (cp.options || []).map((option) => ({
				doctype: "Training Answer Option",
				option_key: option.option_key || "",
				option_text: option.option_text || "",
				is_correct: Number(option.is_correct) || 0,
				explanation: option.explanation || "",
			})),
		};
	}

	write_checkpoint(lesson, cp) {
		const doc = this.checkpoint_doc(lesson, cp);
		if (!cp.name) {
			// No checkpoint_key on the way in. The CONTROLLER mints it; the transient
			// "cp-…" this file hangs the pin on is thrown away the moment a real one
			// comes back.
			return frappe
				.call("frappe.client.insert", { doc })
				.then((r) => {
					const saved = (r && r.message) || {};
					cp.name = saved.name;
					cp.checkpoint_key = saved.checkpoint_key || cp.checkpoint_key;
					cp.modified = saved.modified;
					if (this.checkpoint_key && this.checkpoint_key !== cp.checkpoint_key) {
						this.checkpoint_key = cp.checkpoint_key;
					}
					this.after_checkpoint_write();
				})
				.catch((error) => this.checkpoint_write_failed(error));
		}
		const send = (modified) =>
			frappe
				.call("frappe.client.save", {
					// The real key goes back with EVERY save. Omitting it blanks the field
					// and strands every answer already recorded against it.
					doc: { ...doc, name: cp.name, checkpoint_key: cp.checkpoint_key, modified },
				})
				.then((r) => {
					cp.modified = ((r && r.message) || {}).modified;
					this.after_checkpoint_write();
				})
				.catch((error) => this.checkpoint_write_failed(error));
		if (cp.modified) return send(cp.modified);
		// A checkpoint loaded from the bootstrap has no timestamp, so the first edit
		// fetches one -- a real optimistic lock rather than last-write-wins over
		// somebody else's edit.
		return frappe
			.call("frappe.client.get", { doctype: "Training Checkpoint", name: cp.name })
			.then((r) => send(((r && r.message) || {}).modified))
			.catch((error) => this.checkpoint_write_failed(error));
	}

	after_checkpoint_write() {
		this.paint_status("saved");
		// Repaint the PINS, not the sheet. A full re-render tears down the rich-text
		// controls, so a checkpoint save landing seconds after a pin drag would eat
		// whatever the author had started typing in a block.
		this.refresh_pins();
	}

	checkpoint_write_failed(error) {
		this.paint_status("dirty");
		frappe.show_alert({
			message: __("That checkpoint did not save: {0}", [(error && error.message) || __("unknown error")]),
			indicator: "red",
		});
	}

	refresh_pins() {
		const strip = this._timeline;
		if (!strip || !strip.$strip.closest("body").length) return;
		this.paint_pins(strip.lesson, strip.block, strip.$strip, strip.duration);
		this.render_pin_inspector();
	}

	paint_pins(lesson, block, $strip, duration) {
		$strip.find(".tc-pin").remove();
		this.pins_for(lesson, block).forEach((cp) => {
			const unfinished =
				!(cp.question_text || "").trim() ||
				(cp.options || []).length < 2 ||
				!(cp.options || []).some((o) => Number(o.is_correct));
			$(
				'<button type="button" class="tc-pin"></button>'
			)
				.toggleClass("is-unfinished", unfinished)
				.toggleClass("is-active", cp.checkpoint_key === this.checkpoint_key)
				.attr("aria-label", __("Checkpoint at {0}", [this.mmss(cp.at_seconds)]))
				.attr("title", this.mmss(cp.at_seconds))
				.css("left", this.offset_for(cp.at_seconds, duration))
				.on("click", (e) => {
					e.stopPropagation();
					this.checkpoint_key = cp.checkpoint_key;
					this.refresh_pins();
				})
				.appendTo($strip);
		});
	}

	add_pin(lesson, block, at_seconds) {
		if (!this.editable()) return;
		const cp = {
			// Transient until the insert lands. `checkpoint_key` is server-owned for
			// the same reason `lesson_key` is: it is what stored learner answers are
			// filed under. This is the ONLY mint in this file.
			checkpoint_key: "cp-" + frappe.utils.get_random(10),
			block_key: block.block_key,
			at_seconds: Math.max(0, Math.round(at_seconds)),
			question_type: "Single Choice",
			question_text: "",
			explanation: "",
			pause_video: 1,
			allow_skip: 0,
			max_attempts: 2,
			rewind_seconds_on_wrong: 15,
			counts_toward_score: 0,
			options: [
				{ option_key: "a", option_text: "", is_correct: 0 },
				{ option_key: "b", option_text: "", is_correct: 0 },
			],
		};
		lesson.checkpoints = lesson.checkpoints || [];
		lesson.checkpoints.push(cp);
		this.checkpoint_key = cp.checkpoint_key;
		if (!Number(block.checkpoints_enabled)) {
			// A pin on a block with checkpoints switched off never fires and the author
			// has no way of knowing why. Turn it on with the first pin. (The classic's
			// `set_block_field` does not exist here; this is the canvas's own dirty path.)
			block.checkpoints_enabled = 1;
			this.dirty_blocks(lesson);
			this.mark_dirty();
		}
		this.persist_checkpoint(lesson, cp);
		this.refresh_pins();
	}

	persist_checkpoint(lesson, cp) {
		// THE WHOLE POINT OF THIS STEP. `TrainingCheckpoint._validate_block` resolves
		// its block by querying `tabTraining Content Block` for `parent = lesson` and
		// the given `block_key` -- and a canvas block exists only in memory until the
		// 1200ms autosave lands. Writing a pin first throws "No content block on X has
		// the key Y", on the commonest authoring sequence there is: add a Video block,
		// drop a pin on it.
		//
		// So every pin write chains off the save. `save_then` additionally names the
		// abandoned action if the save fails, because otherwise the only thing on
		// screen is frappe's error about the AUTOSAVE and nothing connects it to the
		// pin the author just dropped.
		//
		// No debounce of its own. The classic has one; with the save chained there is
		// no window for it, and a second uncoordinated timer beside TC_SAVE_DEBOUNCE_MS
		// is how two writers end up racing over one row.
		return this.save_then(__("Placing the checkpoint"))
			.then(() => this.write_checkpoint(lesson, cp))
			.catch(() => {});
	}

	delete_checkpoint(lesson, cp) {
		lesson.checkpoints = (lesson.checkpoints || []).filter(
			(row) => row.checkpoint_key !== cp.checkpoint_key
		);
		if (this.checkpoint_key === cp.checkpoint_key) this.checkpoint_key = null;
		this.refresh_pins();
		if (!cp.name) return Promise.resolve();
		return frappe
			.call("frappe.client.delete", { doctype: "Training Checkpoint", name: cp.name })
			.catch((error) => this.checkpoint_write_failed(error));
	}

	adopt_checkpoints(byLesson) {
		// Replace each saved lesson's pin list with what the server says it NOW holds.
		//
		// `_reap_orphan_checkpoints` runs after every `lesson.save()` and deletes pins
		// whose block has stopped being a Video -- which is exactly what `turn_into`
		// does. Without this the client keeps the deleted rows and `turn_losses` then
		// warns "you will lose 2 in-video checkpoints" about checkpoints that were
		// already deleted on the previous save: a confirmation dialog telling the author
		// to weigh a cost they have already paid.
		//
		// Replaced per lesson rather than merged: the server's list is the answer, and a
		// merge would preserve precisely the ghosts this exists to drop. Lessons absent
		// from the payload are left alone -- they were not saved, so nothing was reaped.
		if (!byLesson) return;
		this.lessons.forEach((lesson) => {
			if (!lesson.name) return;
			if (!Object.prototype.hasOwnProperty.call(byLesson, lesson.name)) {
				// Saved and came back with none: every pin it had is gone.
				if ((this._saved_names || []).indexOf(lesson.name) >= 0) lesson.checkpoints = [];
				return;
			}
			lesson.checkpoints = byLesson[lesson.name] || [];
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
