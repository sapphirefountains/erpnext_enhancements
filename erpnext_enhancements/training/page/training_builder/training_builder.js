// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt
//
// Training Builder (/app/training-builder?course=…) — the authoring surface for
// the open draft of a Training Course Version.
//
// This is desk JS. `frappe.*` is available here and used freely, which is the
// opposite of the rule in public/js/training/*.js — those run for Website Users
// with desk_access = 0 and must never touch a desk global. The distinction is
// load-bearing in this file specifically, because it *hosts* those files: the
// preview constructs the real TR.Player. Nothing may leak from this side into
// theirs.
//
// Four things here are decisions, not preferences:
//
// 1. **The preview drives the REAL player.** `TR.Player(root, boot, transport)`
//    takes an injected transport for exactly this reason. A second, builder-only
//    renderer would mean every player fix had to be made twice and the author
//    would be reviewing something no learner ever sees. The preview therefore
//    implements every `transport.<name>` the player calls — the authoritative
//    list is whatever `grep -o 'transport\.\w*' public/js/training/*.js` returns,
//    not whatever is remembered here.
//
// 2. **The preview grades for real and writes nothing.** Wrong answers are
//    wrong, coverage is computed, gates refuse — all against in-memory state
//    keyed off the draft the author is looking at. No attempt row, no heartbeat,
//    no progress. An author testing their own course must not appear in the
//    compliance record, and a draft has no published payload to grade against
//    anyway.
//
// 3. **The autosave patch is allowlisted on BOTH sides.** The client sends only
//    what it knows the server takes (`TB_LESSON_FIELDS` / `TB_BLOCK_FIELDS`
//    below, which mirror `LESSON_ALLOWED_FIELDS` / `BLOCK_ALLOWED_FIELDS` in
//    api/training_author.py), and it renders the server's `rejected` list rather
//    than swallowing it. A field that reaches a save the server silently drops
//    is worse than a save that fails: autosave reports success and the work is
//    gone at the next reload. Checkpoints are the live example — the server
//    refuses a `checkpoints` key on purpose, so pins are written as the separate
//    documents they are (see persist_checkpoint) rather than smuggled into the
//    patch.
//
// 4. **Class names come from training_builder.css and are not invented here.**
//    That file is the registry and says so in its own header. A class the JS
//    makes up renders as an unstyled div, which reads as a layout bug rather
//    than as a missing join.
//
// Autosave, optimistic locking and the upload idiom are lifted from
// sapphire_maintenance/page/visit_wizard/visit_wizard.js rather than reinvented,
// including its re-merge-on-failure path: a failed batch keeps its edits and
// loses to any newer in-flight change on the same field.

frappe.pages["training-builder"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Training Builder"),
		single_column: true,
	});
	wrapper.training_builder = new TrainingBuilder(page, wrapper);
};

frappe.pages["training-builder"].on_page_show = function (wrapper) {
	if (wrapper.training_builder) wrapper.training_builder.handle_route();
};

// The learner runtime, loaded on demand for the preview. Raw /assets are served
// immutable for a year, so each carries the app version — which this repo bumps
// on every single change, making it a perfect cache key.
const TB_PLAYER_ASSETS = [
	"/assets/erpnext_enhancements/css/training/player.css",
	"/assets/erpnext_enhancements/js/training/blocks.js",
	"/assets/erpnext_enhancements/js/training/video.js",
	"/assets/erpnext_enhancements/js/training/quiz.js",
	"/assets/erpnext_enhancements/js/training/player.js",
];

// Mirrors the server's allowlist. See note 3 in the header: a field here that
// the server does not allow is silently dropped, so this list is a claim about
// the server, not a wish. Anything the server reports back as rejected is
// surfaced immediately rather than discovered on the next reload.
const TB_LESSON_FIELDS = [
	"lesson_title",
	"chapter_key",
	"idx_in_chapter",
	"estimated_minutes",
	"summary",
	"transcript",
	"allow_questions",
	"has_quiz",
	"quiz_questions_to_ask",
	"quiz_pass_score",
	"quiz_shuffle_questions",
	"quiz_shuffle_options",
];

// `block_key` travels with every block and is NEVER regenerated — a fresh key
// resets every in-flight learner to the start of the lesson. `video_duration_seconds`
// is absent on purpose: it is read-only and derived from the Training Video Asset,
// and a hand-typed value is how a coverage gate ends up scored against the wrong
// denominator.
const TB_BLOCK_FIELDS = [
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
];

const TB_BLOCK_TYPES = [
	"Rich Text",
	"Image",
	"PDF",
	"Video",
	"External Embed",
	"Callout",
	"Downloadable File",
	"Divider",
];

// The Training Course Version Select options, in full. These MUST match
// `training_course_version.py`'s MINOR_EDIT / MATERIAL_CHANGE exactly: the parenthetical
// is part of the stored value, not a label. Two dialogs here sent the bare
// "Minor Edit" / "Material Change" instead, which `publish_version` rejects outright
// (`if change_type not in (MINOR_EDIT, MATERIAL_CHANGE)`) — so publishing from the
// builder could never succeed, and creating a draft failed the moment the author chose
// a change type rather than leaving it blank. The Course form had them right all along,
// which is why the bug survived: the same flow worked from the other entry point.
const TB_MINOR_EDIT = "Minor Edit (keep completions)";
const TB_MATERIAL_CHANGE = "Material Change (require retake)";

const TB_AUTOSAVE_MS = 4000;

// Two pins inside this many seconds read as one interruption to a learner and
// the second is usually a mis-drop.
const TB_PIN_MIN_GAP = 5;

// A checkpoint in the last few seconds fires after the learner has mentally
// finished, and often after the video element has already ended.
const TB_PIN_TAIL_GUARD = 10;

// "Test this checkpoint" plays from this far before the pin: enough run-up to
// see the frame it lands on, short enough not to be a coffee break.
const TB_TEST_LEAD_SECONDS = 10;

class TrainingBuilder {
	constructor(page, wrapper) {
		this.page = page;
		this.wrapper = wrapper;
		this.$body = $(page.body);
		this.reset();
		this.build_chrome();
		this.bind_unload_guard();
	}

	reset() {
		this.course = null;
		this.version = null;
		this.chapters = [];
		this.lessons = [];
		this.categories = [];
		this.video_assets = [];
		this.can_publish = false;
		this.ai_enabled = false;

		this.lesson_name = null;
		this.block_key = null;
		this.checkpoint_key = null;

		// The dirty accumulator. Keyed by lesson name so two edits to the same
		// field collapse, and so a failed flush can be re-merged under any newer
		// change without replaying stale values over it.
		this.dirty = { lessons: {}, chapters: null, deleted_lessons: [] };
		this.removed_lessons = {};
		this.removed_blocks = {};

		this.ai_drafts = { kind: null, lesson: null, block_key: null, items: [], message: "", busy: false };
		this._pending_pins = new Map();
		this.preview = null;
		this.local_media = {};
		this.editors = [];

		this._save_timer = null;
		this._saving = false;
		this._conflict = false;
		this._saved_at = null;
	}

	// ----- chrome ---------------------------------------------------------

	build_chrome() {
		this.$topbar = $(`
			<div class="tb-topbar">
				<button type="button" class="tb-outline-toggle">☰ ${__("Lessons")}</button>
				<div class="tb-topbar-title"></div>
				<span class="tb-badge"></span>
				<span class="tb-save-state"></span>
				<button type="button" class="tb-inspector-toggle">${__("Inspector")}</button>
			</div>
		`).appendTo(this.$body);

		this.$shell = $(`
			<div class="tb-shell">
				<section class="tb-outline">
					<div class="tb-pane-head">
						<span>${__("Lessons")}</span>
						<button type="button" class="tb-icon-btn" data-act="add-lesson"
							title="${__("Add a lesson")}" aria-label="${__("Add a lesson")}">+</button>
					</div>
					<div class="tb-pane-body"></div>
				</section>
				<section class="tb-canvas">
					<div class="tb-pane-head">
						<span data-role="canvas-title">${__("Blocks")}</span>
						<button type="button" class="tb-icon-btn" data-act="preview"
							title="${__("Preview as learner")}">▶ ${__("Preview")}</button>
					</div>
					<div class="tb-pane-body"></div>
				</section>
				<section class="tb-inspector">
					<div class="tb-pane-head">
						<span>${__("Inspector")}</span>
						<button type="button" class="tb-icon-btn" data-act="close-inspector"
							aria-label="${__("Close")}">×</button>
					</div>
					<div class="tb-pane-body"></div>
				</section>
			</div>
		`).appendTo(this.$body);

		this.$scrim = $('<div class="tb-scrim"></div>').appendTo(this.$body);

		this.$outline = this.$shell.find(".tb-outline");
		this.$canvas = this.$shell.find(".tb-canvas");
		this.$inspector = this.$shell.find(".tb-inspector");

		this.$topbar.find(".tb-outline-toggle").on("click", () => this.$outline.toggleClass("is-open"));
		this.$topbar.find(".tb-inspector-toggle").on("click", () => this.toggle_inspector(true));
		this.$inspector.find('[data-act="close-inspector"]').on("click", () => this.toggle_inspector(false));
		this.$scrim.on("click", () => this.toggle_inspector(false));
		this.$outline.find('[data-act="add-lesson"]').on("click", () => this.add_lesson());
		this.$canvas.find('[data-act="preview"]').on("click", () => this.toggle_preview());

		this.page.set_secondary_action(__("Reload"), () => this.reload(), "refresh");
		this.page.add_menu_item(__("Save now"), () => this.flush_save());
		this.page.add_menu_item(__("Chapters…"), () => this.manage_chapters());
		this.page.add_menu_item(__("New Draft Version"), () => this.new_draft_version());
		this.page.add_menu_item(__("Submit for Review"), () => this.submit_for_review());
		this.page.add_menu_item(__("Publish…"), () => this.publish());

		// Global keyboard affordances. `C` drops a checkpoint pin at the video's
		// current time; it is deliberately a bare key because an author placing
		// twenty pins should not need a chord, and it is ignored the moment focus
		// is in anything that accepts text.
		this._on_key = (event) => this.on_key(event);
		document.addEventListener("keydown", this._on_key);
	}

	toggle_inspector(open) {
		this.$inspector.toggleClass("is-open", !!open);
		this.$scrim.toggleClass("is-open", !!open);
	}

	on_key(event) {
		if (!this.$shell || !this.$shell.is(":visible")) return;
		const target = event.target || {};
		const tag = (target.tagName || "").toLowerCase();
		if (tag === "input" || tag === "textarea" || tag === "select" || target.isContentEditable) return;
		if (event.metaKey || event.ctrlKey || event.altKey) return;
		if (event.key === "c" || event.key === "C") {
			if (this.drop_pin_at_playhead()) event.preventDefault();
		}
	}

	bind_unload_guard() {
		// A tab closed four seconds after the last keystroke is inside the
		// autosave debounce, so the browser prompt is the only thing standing
		// between the author and losing it.
		this._on_unload = (event) => {
			if (!this.has_dirty()) return undefined;
			this.flush_save();
			event.preventDefault();
			event.returnValue = "";
			return "";
		};
		window.addEventListener("beforeunload", this._on_unload);

		// visibilitychange fires where pagehide does not (mobile Safari), and a
		// tablet locking mid-edit is the common case on site.
		this._on_hide = () => {
			if (document.visibilityState === "hidden") this.flush_save();
		};
		document.addEventListener("visibilitychange", this._on_hide);
	}

	// ----- routing + loading ----------------------------------------------

	handle_route() {
		let course = frappe.utils.get_url_arg("course");
		if (frappe.route_options && frappe.route_options.course) {
			// Cleared whichever branch wins. It used to be cleared only when the
			// query string was absent, so arriving by URL left `course` sitting in
			// route_options — and Frappe applies whatever is there as a filter on
			// the NEXT list view the user opens.
			course = course || frappe.route_options.course;
			frappe.route_options = null;
		}
		if (course && (!this.course || this.course.name !== course)) {
			this.load(course);
		} else if (!course && !this.course) {
			this.render_course_picker();
		}
	}

	reload(select_lesson) {
		// The Reload button used to throw away unsaved edits and pending checkpoint
		// writes without a word. It is next to Save now in the same menu, and the
		// two did opposite things with no warning between them.
		if (this.has_dirty() && !this._reload_confirmed) {
			frappe.confirm(
				__("You have edits that have not been saved yet. Reloading discards them. Continue?"),
				() => {
					this._reload_confirmed = true;
					this.reload(select_lesson);
					this._reload_confirmed = false;
				}
			);
			return;
		}
		const course = this.course && this.course.name;
		const keep = select_lesson || this.lesson_name;
		this.teardown_preview();
		this.reset();
		// Survives the reset so a reload after an edit lands the author back on
		// the lesson they were working on rather than on lesson one.
		this.lesson_name = keep;
		if (course) this.load(course);
		else this.render_course_picker();
	}

	render_course_picker() {
		this.$topbar.find(".tb-topbar-title").text(__("Training Builder"));
		this.$canvas.find(".tb-pane-body").html(
			`<div class="tb-empty">${__("Open a Training Course and press Open Builder to edit its draft.")}<br>
			<a href="/app/training-course">${__("Open the course list")}</a></div>`
		);
		this.$outline.find(".tb-pane-body").empty();
		this.$inspector.find(".tb-pane-body").empty();
	}

	load(course) {
		this.$canvas.find(".tb-pane-body").html(`<div class="tb-empty">${__("Loading…")}</div>`);
		frappe
			.call({
				method: "erpnext_enhancements.api.training_author.get_builder_bootstrap",
				args: { course },
			})
			.then((r) => {
				const data = (r && r.message) || {};
				this.apply_bootstrap(data);
				this.render();
			})
			.catch(() => {
				// The endpoint gates on write permission and throws for anyone
				// else, so "could not load" and "not allowed" are the same door.
				this.$canvas.find(".tb-pane-body").html(
					`<div class="tb-denied">${__("This course could not be opened for editing.")}</div>`
				);
			});
	}

	apply_bootstrap(data) {
		this.course = data.course || null;
		this.version = data.version || null;
		this.chapters = data.chapters || [];
		this.lessons = data.lessons || [];
		this.categories = data.categories || [];
		this.video_assets = data.video_assets || [];
		this.can_publish = !!data.can_publish;
		this.ai_enabled = !!data.ai_enabled;

		// Blocks and quiz/checkpoint rows are edited in place on these objects,
		// so normalise the arrays once instead of guarding every read site.
		this.lessons.forEach((lesson) => {
			lesson.blocks = lesson.blocks || [];
			lesson.quiz = lesson.quiz || [];
			lesson.checkpoints = lesson.checkpoints || [];
			// Remembered so "was this block already live?" has an answer that does
			// not depend on the server telling us. See confirm_block_delete.
			lesson.blocks.forEach((block) => (block.__existing = true));
		});

		if (!this.lesson_name || !this.lesson(this.lesson_name)) {
			this.lesson_name = this.lessons.length ? this.lessons[0].name : null;
		}
		this.block_key = null;
		this.checkpoint_key = null;
	}

	lesson(name) {
		return this.lessons.find((row) => row.name === (name || this.lesson_name)) || null;
	}

	current_lesson() {
		return this.lesson(this.lesson_name);
	}

	current_block() {
		const lesson = this.current_lesson();
		if (!lesson || !this.block_key) return null;
		return lesson.blocks.find((block) => block.block_key === this.block_key) || null;
	}

	editable() {
		return !!(this.version && Number(this.version.docstatus) === 0);
	}

	// ----- dirty tracking + autosave --------------------------------------

	lesson_patch(lesson) {
		const store = this.dirty.lessons;
		store[lesson.name] = store[lesson.name] || { name: lesson.name };
		return store[lesson.name];
	}

	set_lesson_field(lesson, field, value) {
		if (!this.guard_editable()) return;
		if (TB_LESSON_FIELDS.indexOf(field) === -1) {
			// Refusing here rather than sending it is the point: a field the
			// server will not take must never reach a save that then reports
			// success. This is a programming error, not a user-facing one.
			frappe.throw(__("Field {0} is not saveable from the builder.", [field]));
		}
		lesson[field] = value;
		this.lesson_patch(lesson)[field] = value;
		this.schedule_save();
	}

	set_block_field(lesson, block, field, value) {
		if (!this.guard_editable()) return;
		if (TB_BLOCK_FIELDS.indexOf(field) === -1) {
			frappe.throw(__("Block field {0} is not saveable from the builder.", [field]));
		}
		block[field] = value;
		// Blocks go over the wire whole. A child table is replaced by position on
		// save, so sending a single changed cell would be a claim about the other
		// rows that the builder is not in a position to make.
		this.lesson_patch(lesson).blocks = this.live_blocks(lesson);
		this.schedule_save();
	}

	mark_blocks_dirty(lesson) {
		if (!this.guard_editable()) return;
		this.lesson_patch(lesson).blocks = this.live_blocks(lesson);
		this.schedule_save();
	}

	// Checkpoints do NOT go through the draft patch. `save_draft_version` refuses
	// a `checkpoints` key by design and says so in its refusal reason: Training
	// Checkpoint is a top-level DocType, not a child table of the lesson, so it
	// carries its own permissions and its own timestamp. Sending them anyway
	// would earn a rejection notice on every pin drag.
	//
	// So each pin is written as the document it actually is, debounced on the
	// same rhythm as the lesson autosave. The insert is what mints
	// `checkpoint_key` — never this file, because that key is what a learner's
	// stored answers are filed under.
	persist_checkpoint(lesson, cp) {
		if (!this.guard_editable()) return;
		clearTimeout(cp.__timer);
		this.paint_save_state("dirty");
		// Registered so has_dirty() and the beforeunload guard can see it. A pin
		// dragged two seconds before the tab closes is inside the debounce, and
		// without this it goes without a prompt and without a trace.
		this._pending_pins.set(cp, lesson);
		cp.__timer = setTimeout(() => {
			this._pending_pins.delete(cp);
			this.write_checkpoint(lesson, cp);
		}, TB_AUTOSAVE_MS);
	}

	flush_pins() {
		const pending = Array.from(this._pending_pins.entries());
		this._pending_pins.clear();
		pending.forEach(([cp, lesson]) => {
			clearTimeout(cp.__timer);
			this.write_checkpoint(lesson, cp);
		});
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
			// No checkpoint_key on the way in. The controller mints it; the
			// transient "cp-…" this file used to hang the pin on the canvas is
			// thrown away the moment a real one comes back.
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
					// The real key goes back with every save. Omitting it would
					// blank the field and strand every answer already recorded
					// against it — the single most expensive mistake available here.
					doc: { ...doc, name: cp.name, checkpoint_key: cp.checkpoint_key, modified },
				})
				.then((r) => {
					cp.modified = ((r && r.message) || {}).modified;
					this.after_checkpoint_write();
				})
				.catch((error) => this.checkpoint_write_failed(error));

		// A checkpoint loaded from the bootstrap has no timestamp, so the first
		// edit fetches one. That round trip buys a real optimistic lock on the
		// checkpoint rather than a last-write-wins on somebody else's edit.
		if (cp.modified) return send(cp.modified);
		return frappe
			.call("frappe.client.get", { doctype: "Training Checkpoint", name: cp.name })
			.then((r) => send(((r && r.message) || {}).modified))
			.catch((error) => this.checkpoint_write_failed(error));
	}

	after_checkpoint_write() {
		this._saved_at = new Date();
		this.paint_save_state("saved");
		// Repaint the pins, NOT the canvas. A full re-render tears down the Text
		// Editor controls, so a checkpoint save landing four seconds after a pin
		// drag would eat whatever the author had started typing in a block.
		this.refresh_pins();
	}

	refresh_pins() {
		const strip = this._timeline;
		if (!strip || !strip.$strip.closest("body").length) return;
		this.paint_pins(strip.lesson, strip.block, strip.$strip, strip.duration);
	}

	checkpoint_write_failed(error) {
		this.paint_save_state("dirty");
		frappe.show_alert({
			message: __("That checkpoint did not save: {0}", [(error && error.message) || __("unknown error")]),
			indicator: "red",
		});
	}

	delete_checkpoint(lesson, cp) {
		clearTimeout(cp.__timer);
		this._pending_pins.delete(cp);
		lesson.checkpoints = (lesson.checkpoints || []).filter(
			(row) => row.checkpoint_key !== cp.checkpoint_key
		);
		if (this.checkpoint_key === cp.checkpoint_key) this.checkpoint_key = null;
		this.render_canvas();
		this.render_inspector();
		if (!cp.name) return Promise.resolve();
		return frappe
			.call("frappe.client.delete", { doctype: "Training Checkpoint", name: cp.name })
			.catch((error) => this.checkpoint_write_failed(error));
	}

	guard_editable() {
		if (this.editable()) return true;

		// Two quite different states land here, and for a while both got the same
		// sentence: "This version is published and frozen."
		//
		// That sentence is a lie in the commoner of the two. `get_builder_bootstrap`
		// loads the open draft and nothing else, so `this.version` is null whenever a
		// course has no draft — which is where EVERY course sits the moment it is
		// published, because publishing consumes the draft. An author coming back to
		// make a second edit was told they were looking at a frozen version and to go
		// find a different one, so they went looking for a version switcher. There
		// isn't one; there was nothing loaded at all.
		//
		// Both branches now offer the way out rather than describing it, because the
		// action ("New Draft Version") was only ever reachable from the page's ⋯ menu
		// and from an empty state in a pane the author was not necessarily looking at.
		const draft_action = {
			label: __("New Draft Version"),
			action: () => {
				frappe.hide_msgprint();
				this.new_draft_version();
			},
		};

		if (this.version) {
			frappe.msgprint({
				title: __("Published version"),
				indicator: "orange",
				message: __(
					"v{0} is published and frozen — learners are reading it right now, so it cannot be edited in place. A new draft copies it and keeps every lesson key, so anyone part-way through stays where they are.",
					[this.version.version_number]
				),
				primary_action: draft_action,
			});
		} else {
			frappe.msgprint({
				title: __("No open draft"),
				indicator: "orange",
				message: __(
					"This course has no draft to edit. Publishing turns the draft into the live version, so the next round of edits starts a new one — it copies the live content and keeps every lesson key, so learners part-way through stay where they are."
				),
				primary_action: draft_action,
			});
		}
		return false;
	}

	live_blocks(lesson) {
		return (lesson.blocks || [])
			.filter((block) => !this.removed_blocks[block.block_key])
			.map((block) => {
				const out = {};
				TB_BLOCK_FIELDS.forEach((field) => {
					if (block[field] !== undefined) out[field] = block[field];
				});
				return out;
			});
	}

	has_dirty() {
		return !!(
			Object.keys(this.dirty.lessons).length ||
			this.dirty.chapters ||
			this.dirty.deleted_lessons.length ||
			this._pending_pins.size
		);
	}

	schedule_save() {
		this.pull_editors();
		this.paint_save_state("dirty");
		clearTimeout(this._save_timer);
		this._save_timer = setTimeout(() => this.flush_save(), TB_AUTOSAVE_MS);
	}

	save_then(label) {
		// Actions that must not run against stale content chain off flush_save().
		// When the save fails the chained call is correctly abandoned — but the
		// dialog has already closed, and the only thing on screen is frappe's error
		// about the *autosave*. Nothing connects that to the button the author
		// pressed, so a lesson they just named simply never appears and there is no
		// message saying why. Name the abandoned action in its own right.
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

	flush_save() {
		clearTimeout(this._save_timer);
		this.pull_editors();
		this.flush_pins();
		if (!this.version || !this.editable() || this._conflict || !this.has_dirty()) {
			return Promise.resolve();
		}
		if (this._saving) {
			// A second flush while one is in flight would race the `modified`
			// token. Let the in-flight one land; its .then re-schedules.
			//
			// What this used to return was `Promise.resolve()` — indistinguishable,
			// to a caller, from "everything is saved". `publish()` chains straight
			// off it, so pressing Publish within a few seconds of typing froze a
			// version that was missing the last thing the author wrote, and a
			// published version cannot be edited: the only fix is a new version.
			// Returning the in-flight chain makes the promise mean what every
			// caller already assumed it meant.
			this._pending_flush = true;
			return this._inflight || Promise.resolve();
		}

		const sent = this.dirty;
		const payload = {
			lessons: Object.values(sent.lessons),
			chapters: sent.chapters || undefined,
			deleted_lessons: sent.deleted_lessons.slice(),
		};
		this.dirty = { lessons: {}, chapters: null, deleted_lessons: [] };
		this._saving = true;
		this.paint_save_state("saving");

		this._inflight = frappe
			.call({
				method: "erpnext_enhancements.api.training_author.save_draft_version",
				args: {
					course_version: this.version.name,
					payload: JSON.stringify(payload),
					modified: this.version.modified,
				},
			})
			.then((r) => {
				const state = (r && r.message) || {};
				this._saving = false;
				// Adopting the new token is mandatory, not housekeeping: the next
				// save is rejected against the old one.
				if (state.modified) this.version.modified = state.modified;
				if (state.chapters) this.chapters = state.chapters;
				// Drop anything the server really deleted before the next patch can
				// name it. `state.deleted` was returned from the first day and read
				// by nobody.
				if (state.deleted && state.deleted.length) {
					this.forget_deleted(state.deleted);
					this.render();
				}
				this._saved_at = new Date();
				this.paint_save_state("saved");
				this.report_rejected(state);
				if (this._pending_flush) {
					this._pending_flush = false;
					// Chained, not fired and forgotten, so `_inflight` settles only
					// once the whole queue has drained — that is what lets a caller
					// waiting on flush_save() know its edits are really stored.
					return this.flush_save().then(() => state);
				}
				return state;
			})
			.catch((error) => {
				this._saving = false;
				// Keep the edits. Anything typed since the request went out is
				// newer and wins on a per-field basis; anything else replays.
				this.remerge(sent);
				const message = String((error && error.message) || "");
				if (/modif|stale|conflict|newer/i.test(message)) {
					this.enter_conflict();
				} else {
					this.paint_save_state("dirty");
				}
				throw error;
			});
		return this._inflight;
	}

	remerge(sent) {
		const merged = { lessons: {}, chapters: null, deleted_lessons: [] };
		Object.entries(sent.lessons).forEach(([name, patch]) => {
			merged.lessons[name] = { ...patch };
		});
		Object.entries(this.dirty.lessons).forEach(([name, patch]) => {
			merged.lessons[name] = { ...(merged.lessons[name] || {}), ...patch };
		});
		merged.chapters = this.dirty.chapters || sent.chapters || null;
		merged.deleted_lessons = Array.from(
			new Set(sent.deleted_lessons.concat(this.dirty.deleted_lessons))
		);
		this.dirty = merged;
	}

	// `rejected` is a list of {scope, target, field, reason}. It is not an error
	// and the save around it succeeded — it is the difference between autosave
	// losing an author's work quietly and the builder being able to say which
	// field went and why. So it is shown, never logged and swallowed.
	report_rejected(state) {
		const rejected = state.rejected || [];
		if (!rejected.length) return;
		const rows = rejected
			.map(
				(row) =>
					"<li><b>" +
					frappe.utils.escape_html(row.field || "") +
					"</b> — " +
					frappe.utils.escape_html(row.reason || __("not saveable from the builder")) +
					"</li>"
			)
			.join("");
		frappe.msgprint({
			title: __("Some changes were not saved"),
			indicator: "orange",
			message:
				`<p>${__("Everything else saved. These did not, and are still on screen — reload before relying on them:")}</p><ul>${rows}</ul>`,
		});
	}

	enter_conflict() {
		this._conflict = true;
		this.paint_save_state("conflict");
		frappe.msgprint({
			title: __("Someone else edited this draft"),
			indicator: "red",
			message: __(
				"This draft changed in another tab or by another author, so your last edits were refused rather than written over theirs. Reload to see the current version — anything you typed since the last save will be lost."
			),
			primary_action: {
				label: __("Reload"),
				action: () => {
					frappe.hide_msgprint();
					this._conflict = false;
					this.reload();
				},
			},
		});
	}

	paint_save_state(state) {
		const $el = this.$topbar.find(".tb-save-state");
		$el.removeClass("is-saving is-dirty is-saved is-conflict");
		if (state === "saving") $el.addClass("is-saving").text(__("Saving…"));
		else if (state === "dirty") $el.addClass("is-dirty").text(__("Unsaved changes"));
		else if (state === "conflict") $el.addClass("is-conflict").text(__("Not saved — reload"));
		else if (state === "saved") {
			// Local wall-clock, deliberately. `frappe.datetime` would render it in
			// the site's timezone, and "Saved 09:04" against a clock reading 12:04
			// reads as "it did not save".
			const at = this._saved_at || new Date();
			const time =
				String(at.getHours()).padStart(2, "0") + ":" + String(at.getMinutes()).padStart(2, "0");
			$el.addClass("is-saved").text(__("Saved {0}", [time]));
		} else $el.text("");
	}

	// Text Editors do not fire a change event per keystroke, so their value is
	// pulled at every save boundary rather than pushed. Without this an author
	// who types and immediately hits Preview loses the paragraph.
	pull_editors() {
		this.editors.forEach((entry) => {
			try {
				const value = entry.group.get_value(entry.field);
				if (value === entry.block[entry.target]) return;
				entry.block[entry.target] = value;
				const patch = this.dirty.lessons[entry.lesson] || { name: entry.lesson };
				this.dirty.lessons[entry.lesson] = patch;
				patch.blocks = this.live_blocks(this.lesson(entry.lesson));
			} catch (e) {
				/* the control was torn down with its card */
			}
		});
	}

	drop_editors() {
		// Pull before destroying. A Text Editor's value only ever arrives by
		// being read, so tearing the control down first silently discards
		// whatever was typed since the last save boundary.
		this.pull_editors();
		this.editors.forEach((entry) => {
			try {
				entry.group.$wrapper.remove();
			} catch (e) {
				/* already detached */
			}
		});
		this.editors = [];
	}

	// ----- render ----------------------------------------------------------

	render() {
		this.$topbar
			.find(".tb-topbar-title")
			.text(this.course ? this.course.course_title || this.course.name : __("Training Builder"));
		this.paint_badge();
		this.paint_save_state(this.has_dirty() ? "dirty" : this._saved_at ? "saved" : "");
		this.paint_edit_affordances();

		if (!this.version) {
			this.render_no_draft();
			return;
		}
		this.render_outline();
		this.render_canvas();
		this.render_inspector();
	}

	paint_edit_affordances() {
		// The "+" that adds a lesson lives in the outline pane's HEAD, and
		// `render_no_draft` only empties the pane BODY — so on a course with no
		// draft the button sat there looking perfectly available, and clicking it
		// was how most authors met the guard. Offering a control and then refusing
		// it is worse than not offering it: disable it and say why on hover.
		const editable = this.editable();
		// Preview needs a lesson to render. With no draft (or a draft with no
		// lessons yet) it opened a pane that could only ever be empty.
		const previewable = !!(this.version && (this.lessons || []).length);
		this.$canvas
			.find('[data-act="preview"]')
			.prop("disabled", !previewable)
			.attr(
				"title",
				previewable
					? __("Preview as learner")
					: this.version
						? __("Add a lesson first — there is nothing to preview yet.")
						: __("This course has no open draft to preview.")
			);

		this.$outline
			.find('[data-act="add-lesson"]')
			.prop("disabled", !editable)
			.attr(
				"title",
				editable
					? __("Add a lesson")
					: this.version
						? __("v{0} is published. Create a new draft version to edit it.", [
								this.version.version_number,
							])
						: __("This course has no open draft. Create one to start editing.")
			);
	}

	paint_badge() {
		const $badge = this.$topbar.find(".tb-badge").removeClass("is-draft is-published");
		if (!this.version) {
			$badge.text(__("No open draft"));
			return;
		}
		const published = Number(this.version.docstatus) === 1;
		$badge
			.addClass(published ? "is-published" : "is-draft")
			.text(
				published
					? __("v{0} — published", [this.version.version_number])
					: this.version.submitted_for_review
						? __("v{0} — in review", [this.version.version_number])
						: __("v{0} — draft", [this.version.version_number])
			);
	}

	render_no_draft() {
		this.$outline.find(".tb-pane-body").empty();
		this.$inspector.find(".tb-pane-body").empty();
		// Deliberately not created implicitly: create_draft_version clones the
		// live version and refuses when one is already open, so raising one as a
		// side effect of opening a page is a decision the author has to make.
		const $body = this.$canvas.find(".tb-pane-body").empty();
		$(`<div class="tb-empty">
				${__("This course has no open draft version.")}<br>
				${__("A new draft copies the live content, keeping every lesson and block key so learners part-way through stay where they are.")}
			</div>`).appendTo($body);
		$(`<div class="tb-add-block"><button type="button">${__("New Draft Version")}</button></div>`)
			.on("click", "button", () => this.new_draft_version())
			.appendTo($body);
	}

	// ----- outline rail ----------------------------------------------------

	render_outline() {
		const $body = this.$outline.find(".tb-pane-body").empty();
		if (!this.lessons.length) {
			$body.html(`<div class="tb-empty">${__("No lessons yet.")}</div>`);
			return;
		}

		const groups = new Map();
		(this.chapters || []).forEach((chapter) => groups.set(chapter.chapter_key, []));
		this.lessons.forEach((lesson) => {
			const key = lesson.chapter_key || "";
			if (!groups.has(key)) groups.set(key, []);
			groups.get(key).push(lesson);
		});

		groups.forEach((lessons, key) => {
			if (!lessons.length) return;
			const chapter = (this.chapters || []).find((row) => row.chapter_key === key);
			const $chapter = $('<div class="tb-chapter"></div>').appendTo($body);
			$(`<div class="tb-chapter-head">
					<span>${frappe.utils.escape_html(chapter ? chapter.chapter_title : __("Unfiled"))}</span>
					<span class="tb-chapter-count">${lessons.length}</span>
				</div>`).appendTo($chapter);
			const $list = $('<ul class="tb-lesson-list"></ul>').appendTo($chapter);
			lessons.forEach((lesson) => $list.append(this.lesson_row(lesson)));
			this.make_sortable($list[0], () => this.commit_lesson_order(), "data-lesson");
		});
	}

	lesson_row(lesson) {
		const removed = !!this.removed_lessons[lesson.name];
		const $row = $(`
			<li class="tb-lesson ${lesson.name === this.lesson_name ? "is-selected" : ""} ${removed ? "is-removed" : ""}"
				data-lesson="${frappe.utils.escape_html(lesson.name)}" tabindex="0">
				<span class="tb-drag-handle" role="button" tabindex="0"
					aria-label="${__("Reorder {0}. Use the up and down arrows.", [frappe.utils.escape_html(lesson.lesson_title || "")])}">⠿</span>
				<span style="min-width:0;flex:1;">
					<span class="tb-lesson-title">${frappe.utils.escape_html(lesson.lesson_title || __("Untitled lesson"))}</span>
					<span class="tb-lesson-meta">${this.lesson_meta(lesson)}</span>
					<span class="tb-removed-note">${__("Removed")}
						<button type="button" class="tb-undo">${__("Undo")}</button></span>
				</span>
				<button type="button" class="tb-icon-btn" data-act="remove-lesson"
					aria-label="${__("Remove lesson")}">🗑</button>
			</li>
		`);
		$row.on("click", (event) => {
			if ($(event.target).closest("button").length) return;
			this.select_lesson(lesson.name);
		});
		$row.find(".tb-undo").on("click", () => this.restore_lesson(lesson));
		$row.find('[data-act="remove-lesson"]').on("click", () => this.remove_lesson(lesson));
		this.bind_keyboard_reorder($row.find(".tb-drag-handle")[0], $row[0], () =>
			this.commit_lesson_order()
		);
		return $row;
	}

	lesson_meta(lesson) {
		const bits = [];
		if (lesson.estimated_minutes) bits.push(__("{0} min", [lesson.estimated_minutes]));
		bits.push(__("{0} blocks", [(lesson.blocks || []).length]));
		if (lesson.has_quiz) bits.push(__("quiz"));
		if ((lesson.checkpoints || []).length) {
			bits.push(__("{0} checkpoints", [lesson.checkpoints.length]));
		}
		return frappe.utils.escape_html(bits.join(" · "));
	}

	select_lesson(name) {
		this.flush_save();
		this.lesson_name = name;
		this.block_key = null;
		this.checkpoint_key = null;
		this.teardown_preview();
		this.$outline.removeClass("is-open");
		this.render_outline();
		this.render_canvas();
		this.render_inspector();
	}

	add_lesson() {
		if (!this.guard_editable()) return;
		const dialog = new frappe.ui.Dialog({
			title: __("Add Lesson"),
			fields: [
				{ fieldname: "lesson_title", fieldtype: "Data", label: __("Title"), reqd: 1 },
				{
					fieldname: "chapter_key",
					fieldtype: "Select",
					label: __("Chapter"),
					options: [{ value: "", label: __("Unfiled") }].concat(
						(this.chapters || []).map((c) => ({ value: c.chapter_key, label: c.chapter_title }))
					),
				},
			],
			primary_action_label: __("Add"),
			primary_action: (values) => {
				dialog.hide();
				// Inserted server-side rather than optimistically: `lesson_key` is
				// generated in the controller and is the identity every learner's
				// stored progress hangs off. A client-invented key that later
				// disagrees is unrecoverable.
				//
				// A lesson entry with no `name` is an insert; `temp_id` is how the
				// real name is matched back out of `created_lessons`.
				this.save_then(__("Adding the lesson")).then(() =>
					frappe
						.call({
							method: "erpnext_enhancements.api.training_author.save_draft_version",
							args: {
								course_version: this.version.name,
								payload: JSON.stringify({
									lessons: [
										{
											temp_id: "new-" + frappe.utils.get_random(8),
											lesson_title: values.lesson_title,
											chapter_key: values.chapter_key || "",
											idx_in_chapter: this.lessons.length + 1,
										},
									],
								}),
								modified: this.version.modified,
							},
						})
						.then((r) => {
							const created = ((r && r.message) || {}).created_lessons || [];
							const name = created.length ? created[0].name || created[0] : null;
							// Reload rather than splice: the server minted the
							// lesson_key and the row name, and guessing either is
							// how the rail and the database drift apart.
							this.reload(name);
						})
				)
					// Already reported by save_then; marked handled so a failed save is
					// not also an unhandled rejection in the console.
					.catch(() => {});
			},
		});
		dialog.show();
	}

	remove_lesson(lesson) {
		if (!this.guard_editable()) return;
		this.removed_lessons[lesson.name] = true;
		if (this.dirty.deleted_lessons.indexOf(lesson.name) === -1) {
			this.dirty.deleted_lessons.push(lesson.name);
		}
		// Move off the lesson being deleted. Only the rail row used to change: the
		// canvas kept painting its blocks and the inspector kept its Title/Summary
		// enabled, so the obvious next action — tidy up the thing you are looking
		// at — queued a patch against a lesson the next autosave was about to
		// delete. The server then 404s on that name and rolls back the WHOLE
		// payload, `remerge` puts the doomed patch straight back, and the retry
		// loop repeats every four seconds for the rest of the session, taking
		// every other lesson's edits down with it.
		if (this.lesson_name === lesson.name) {
			const survivor = (this.lessons || []).find(
				(row) => row.name !== lesson.name && !this.removed_lessons[row.name]
			);
			this.lesson_name = survivor ? survivor.name : null;
			this.block_key = null;
			this.checkpoint_key = null;
		}
		this.schedule_save();
		this.render();
	}

	forget_deleted(names) {
		// A lesson the server has actually deleted must leave the client's world
		// entirely. Leaving it in `this.lessons` let a later edit address a dead
		// name, and left "Undo" offering to restore something that no longer
		// exists — it un-greyed the row and did nothing else.
		const gone = new Set(names || []);
		if (!gone.size) return;
		this.lessons = (this.lessons || []).filter((row) => !gone.has(row.name));
		gone.forEach((name) => {
			delete this.removed_lessons[name];
			delete this.dirty.lessons[name];
		});
		this.dirty.deleted_lessons = (this.dirty.deleted_lessons || []).filter(
			(name) => !gone.has(name)
		);
		if (gone.has(this.lesson_name)) {
			this.lesson_name = this.lessons.length ? this.lessons[0].name : null;
			this.block_key = null;
			this.checkpoint_key = null;
		}
	}

	restore_lesson(lesson) {
		delete this.removed_lessons[lesson.name];
		this.dirty.deleted_lessons = this.dirty.deleted_lessons.filter((name) => name !== lesson.name);
		this.schedule_save();
		this.render_outline();
	}

	commit_lesson_order() {
		if (!this.guard_editable()) return;
		// Always the WHOLE rail, never just the chapter that was dragged in.
		// `reorder_lessons` takes an ordered list of lesson names and has no way
		// to know a partial list is partial — it would read a drag inside chapter
		// three as "these four lessons are now the entire course".
		const order = this.$outline
			.find(".tb-lesson[data-lesson]")
			.map((_, el) => $(el).attr("data-lesson"))
			.get();
		frappe
			.call({
				method: "erpnext_enhancements.api.training_author.reorder_lessons",
				args: { course_version: this.version.name, order: JSON.stringify(order) },
			})
			.then(() => {
				const index = new Map(order.map((name, i) => [name, i]));
				this.lessons.sort((a, b) => (index.get(a.name) ?? 0) - (index.get(b.name) ?? 0));
				this.render_outline();
			});
	}

	// ----- reordering ------------------------------------------------------

	// Sortable is already on the page — Frappe uses it for grid rows and Kanban.
	// Vendoring a second copy would double the payload and give the desk two
	// drag implementations that behave subtly differently.
	make_sortable(container, commit, attr) {
		if (!window.Sortable || !this.editable()) return;
		new window.Sortable(container, {
			handle: ".tb-drag-handle",
			animation: 150,
			ghostClass: "is-dragging",
			// The insertion line is drawn on the card being passed rather than
			// with a placeholder node, which is what tb-drop-before/after are for.
			onMove: (event) => {
				$(container).find(".tb-drop-before,.tb-drop-after").removeClass("tb-drop-before tb-drop-after");
				$(event.related).addClass(event.willInsertAfter ? "tb-drop-after" : "tb-drop-before");
				return true;
			},
			onEnd: () => {
				$(container).find(".tb-drop-before,.tb-drop-after").removeClass("tb-drop-before tb-drop-after");
				commit($(container).children("[" + attr + "]").map((_, el) => $(el).attr(attr)).get());
			},
		});
	}

	// Drag-only is an accessibility failure and this repo has already been bitten
	// by touch-drag. Arrow keys on a focused handle do the same job, and the move
	// is announced because a silently reordered list tells a screen-reader user
	// nothing happened.
	bind_keyboard_reorder(handle, node, commit) {
		if (!handle || !this.editable()) return;
		$(handle).on("keydown", (event) => {
			let moved = false;
			if (event.key === "ArrowUp" && node.previousElementSibling) {
				node.parentNode.insertBefore(node, node.previousElementSibling);
				moved = true;
			} else if (event.key === "ArrowDown" && node.nextElementSibling) {
				node.parentNode.insertBefore(node.nextElementSibling, node);
				moved = true;
			} else if (event.key === "Home") {
				node.parentNode.insertBefore(node, node.parentNode.firstChild);
				moved = true;
			} else if (event.key === "End") {
				node.parentNode.appendChild(node);
				moved = true;
			}
			if (!moved) return;
			event.preventDefault();
			handle.focus();
			const attr = node.hasAttribute("data-lesson") ? "data-lesson" : "data-block";
			const order = $(node.parentNode)
				.children("[" + attr + "]")
				.map((_, el) => $(el).attr(attr))
				.get();
			const position = order.indexOf($(node).attr(attr)) + 1;
			this.announce(__("Moved to position {0} of {1}.", [position, order.length]));
			commit(order);
		});
	}

	announce(message) {
		if (!this.$live) {
			this.$live = $('<div class="tb-muted" aria-live="polite" role="status"></div>').appendTo(
				this.$topbar
			);
		}
		this.$live.text(message);
	}

	// ----- block canvas ----------------------------------------------------

	render_canvas() {
		this.drop_editors();
		clearInterval(this._playhead_timer);
		const lesson = this.current_lesson();
		const $head = this.$canvas.find('[data-role="canvas-title"]');
		const $body = this.$canvas.find(".tb-pane-body").empty();
		$head.text(lesson ? lesson.lesson_title || __("Untitled lesson") : __("Blocks"));

		if (!lesson) {
			$body.html(`<div class="tb-empty">${__("Pick a lesson on the left.")}</div>`);
			return;
		}

		const $list = $('<div class="tb-block-list"></div>').appendTo($body);
		if (!lesson.blocks.length) {
			$('<div class="tb-empty"></div>').text(__("No blocks yet. Add one below.")).appendTo($list);
		}
		lesson.blocks.forEach((block) => $list.append(this.block_card(lesson, block)));
		this.make_sortable($list[0], (order) => this.commit_block_order(lesson, order), "data-block");

		const $add = $('<div class="tb-add-block"></div>').appendTo($body);
		TB_BLOCK_TYPES.forEach((type) => {
			$("<button type='button'></button>")
				.text("+ " + __(type))
				.on("click", () => this.add_block(lesson, type))
				.appendTo($add);
		});
	}

	block_card(lesson, block) {
		const removed = !!this.removed_blocks[block.block_key];
		const $card = $(`
			<article class="tb-block ${block.block_key === this.block_key ? "is-selected" : ""} ${removed ? "is-removed" : ""}"
				data-block="${frappe.utils.escape_html(block.block_key || "")}">
				<span class="tb-drag-handle" role="button" tabindex="0"
					aria-label="${__("Reorder this block. Use the up and down arrows.")}">⠿</span>
				<div class="tb-block-main">
					<div class="tb-block-top">
						<span class="tb-block-type">${frappe.utils.escape_html(block.block_type || "")}</span>
						<span class="tb-block-title">${frappe.utils.escape_html(block.heading || "")}</span>
					</div>
					<div class="tb-block-body"></div>
					<div class="tb-removed-note">${__("Removed — saved when the draft saves.")}
						<button type="button" class="tb-undo">${__("Undo")}</button></div>
				</div>
				<div class="tb-block-actions">
					<button type="button" class="tb-icon-btn tb-preview-here" data-act="preview-here"
						title="${__("Start preview here")}" aria-label="${__("Start preview here")}">▶</button>
					<button type="button" class="tb-icon-btn" data-act="remove"
						title="${__("Remove block")}" aria-label="${__("Remove block")}">🗑</button>
				</div>
			</article>
		`);

		$card.on("click", (event) => {
			if ($(event.target).closest("button, .frappe-control, .ql-editor").length) return;
			this.select_block(block.block_key);
		});
		$card.find(".tb-undo").on("click", () => this.restore_block(lesson, block));
		$card.find('[data-act="remove"]').on("click", () => this.confirm_block_delete(lesson, block));
		$card.find('[data-act="preview-here"]').on("click", () => this.start_preview(block.block_key));
		this.bind_keyboard_reorder($card.find(".tb-drag-handle")[0], $card[0], (order) =>
			this.commit_block_order(lesson, order)
		);

		this.render_block_body(lesson, block, $card.find(".tb-block-body"));
		return $card;
	}

	render_block_body(lesson, block, $body) {
		const type = block.block_type;

		if (type === "Rich Text" || type === "Callout") {
			// A real Text Editor in the card, not a textarea: it inherits the desk
			// sanitizer, the mention behaviour and the paste handling, and the
			// author edits the thing they are looking at rather than markup.
			const group = new frappe.ui.FieldGroup({
				fields: [
					{
						fieldname: "content",
						fieldtype: "Text Editor",
						label: "",
						max_height: "220px",
						change: () => this.schedule_save(),
					},
				],
				body: $body[0],
			});
			group.make();
			group.set_value("content", block.content || "");
			if (!this.editable()) group.fields_dict.content.$wrapper.find(".ql-editor").attr("contenteditable", "false");
			this.editors.push({ group, field: "content", block, target: "content", lesson: lesson.name });
			return;
		}

		if (type === "Video") {
			this.render_video_block(lesson, block, $body);
			return;
		}

		if (type === "Image") {
			const url = this.media_for(block, block.image);
			if (url) $("<img alt='' loading='lazy'>").attr("src", url).appendTo($body);
			else $('<div class="tb-muted"></div>').text(__("No image attached yet.")).appendTo($body);
			return;
		}

		if (type === "PDF" || type === "Downloadable File") {
			$('<div class="tb-muted"></div>')
				.text(block.file ? block.file : __("No file attached yet."))
				.appendTo($body);
			return;
		}

		if (type === "External Embed") {
			$('<div class="tb-muted"></div>')
				.text(block.embed_url || __("No embed URL set."))
				.appendTo($body);
			$('<div class="tb-hint"></div>')
				.text(__("Embedded players cannot be tracked: no checkpoints and no watch coverage on this block."))
				.appendTo($body);
			return;
		}

		if (type === "Divider") {
			$("<hr>").appendTo($body);
			return;
		}

		$('<div class="tb-muted"></div>').text(__("Nothing to preview for this block type.")).appendTo($body);
	}

	add_block(lesson, type) {
		if (!this.guard_editable()) return;
		const block = {
			// A block_key is minted here because a brand-new block has no learner
			// progress to lose and the canvas needs an identity immediately. The
			// server owns keys for everything it has ever published — see the
			// note on TB_BLOCK_FIELDS — and this one is only ever *added*.
			block_key: "blk-" + frappe.utils.get_random(10),
			block_type: type,
			heading: "",
			content: "",
			required_for_completion: type === "Divider" ? 0 : 1,
			min_coverage_percent: type === "Video" ? 80 : 0,
			checkpoints_enabled: 0,
			__existing: false,
		};
		lesson.blocks.push(block);
		this.block_key = block.block_key;
		this.mark_blocks_dirty(lesson);
		this.render_canvas();
		this.render_inspector();
	}

	confirm_block_delete(lesson, block) {
		if (!this.guard_editable()) return;
		// A block that has ever been published carries learner progress keyed off
		// its block_key. Deleting it does not delete that progress — it orphans
		// it, and a completion that was graded against a gate nobody can see any
		// more is unauditable. Retiring keeps the row and the key and only stops
		// it gating, so in-flight learners are unaffected.
		const was_live = block.__existing && Number(this.version.version_number) > 1;
		if (!was_live) {
			this.remove_block(lesson, block);
			return;
		}
		const dialog = new frappe.ui.Dialog({
			title: __("This block is already live"),
			fields: [
				{
					fieldtype: "HTML",
					options: `<div class="tb-warn">${__(
						"Learners part-way through this course have progress recorded against this block. Deleting it orphans that progress and their completion can no longer be explained. Retiring keeps the block and its key, and stops it counting toward finishing the lesson."
					)}</div>`,
				},
			],
			primary_action_label: __("Retire block"),
			primary_action: () => {
				dialog.hide();
				this.retire_block(lesson, block);
			},
			secondary_action_label: __("Delete anyway"),
			secondary_action: () => {
				dialog.hide();
				this.remove_block(lesson, block);
			},
		});
		dialog.show();
	}

	retire_block(lesson, block) {
		// There is no `is_retired` flag on Training Content Block, so retirement
		// is expressed with the fields that exist: the block stays visible and
		// stops gating anything. Flagged in the handover — a real flag would let
		// the player hide it from new learners while honouring old completions.
		this.set_block_field(lesson, block, "required_for_completion", 0);
		this.set_block_field(lesson, block, "min_coverage_percent", 0);
		this.set_block_field(lesson, block, "checkpoints_enabled", 0);
		frappe.show_alert({ message: __("Block retired — it no longer gates the lesson."), indicator: "blue" });
		this.render_canvas();
		this.render_inspector();
	}

	remove_block(lesson, block) {
		this.removed_blocks[block.block_key] = true;
		// Checkpoints hang off block_key, so a pin on a removed block is an
		// orphan the moment the save lands. They are separate documents, so
		// dropping them from the array is not enough — they have to be deleted.
		(lesson.checkpoints || [])
			.filter((cp) => cp.block_key === block.block_key)
			.forEach((cp) => this.delete_checkpoint(lesson, cp));
		this.mark_blocks_dirty(lesson);
		this.render_canvas();
	}

	restore_block(lesson, block) {
		delete this.removed_blocks[block.block_key];
		this.mark_blocks_dirty(lesson);
		this.render_canvas();
	}

	commit_block_order(lesson, order) {
		const index = new Map(order.map((key, i) => [key, i]));
		lesson.blocks.sort((a, b) => (index.get(a.block_key) ?? 0) - (index.get(b.block_key) ?? 0));
		this.mark_blocks_dirty(lesson);
	}

	select_block(key) {
		this.block_key = key;
		this.checkpoint_key = null;
		this.$canvas.find(".tb-block").removeClass("is-selected");
		this.$canvas.find(`.tb-block[data-block="${key}"]`).addClass("is-selected");
		this.render_inspector();
		if (window.matchMedia("(max-width: 1100px)").matches) this.toggle_inspector(true);
	}

	// ----- video block: asset, upload, timeline ---------------------------

	video_asset(block) {
		return (this.video_assets || []).find((row) => row.name === block.video_asset) || null;
	}

	block_duration(block) {
		const asset = this.video_asset(block);
		return (
			Number(block.video_duration_seconds) ||
			Number(asset && asset.duration_seconds) ||
			Number(this.local_media[block.block_key] && this.local_media[block.block_key].duration) ||
			0
		);
	}

	media_for(block, fallback) {
		const local = this.local_media[block.block_key];
		if (local && local.url) return local.url;
		return block.preview_url || fallback || "";
	}

	render_video_block(lesson, block, $body) {
		const asset = this.video_asset(block);
		const duration = this.block_duration(block);

		$('<div class="tb-muted"></div>')
			.text(asset ? asset.title : block.video_asset || __("No video asset linked."))
			.appendTo($body);

		if (!duration) {
			$('<div class="tb-warn"></div>')
				.text(
					__(
						"This video has no known length. Watch coverage is a fraction of it, so a checkpoint cannot be placed and a gate cannot be scored until it is set."
					)
				)
				.appendTo($body);
			return;
		}

		this.render_timeline(lesson, block, $body, duration);
	}

	render_timeline(lesson, block, $body, duration) {
		const $strip = $(`
			<div class="tb-timeline" tabindex="0"
				aria-label="${__("Checkpoint timeline. Press C to place one at the current time.")}">
				<div class="tb-timeline-track"></div>
				<div class="tb-timeline-cursor" style="left:10px;"></div>
				<div class="tb-timeline-scale"></div>
			</div>
		`).appendTo($body);

		const $scale = $strip.find(".tb-timeline-scale");
		const steps = 5;
		for (let i = 0; i <= steps; i++) {
			$('<span class="tb-timeline-tick"></span>')
				.css("left", (i / steps) * 100 + "%")
				.text(tb_mmss((duration * i) / steps))
				.appendTo($scale);
		}

		this._timeline = { lesson, block, $strip, duration };
		this.paint_pins(lesson, block, $strip, duration);
		this.track_playhead($strip, duration);

		$('<div class="tb-hint"></div>')
			.text(
				__(
					"Click the strip to place a checkpoint, or press C while the preview is playing. Drag a pin to move it — the preview seeks as you drag so you can see the frame it lands on."
				)
			)
			.appendTo($body);

		$strip.on("click", (event) => {
			if ($(event.target).closest(".tb-timeline-pin").length) return;
			if (!this.guard_editable()) return;
			this.add_pin(lesson, block, this.seconds_at(event.clientX, $strip, duration));
		});
	}

	// The strip has 10px of padding at each end, so a pin's position is 10px plus
	// its share of the remaining width. A bare percentage would drift by up to
	// 20px across the strip — enough that a pin visibly does not sit on the tick
	// it was dropped at, which reads as the timestamps being wrong.
	offset_for(seconds, duration) {
		const ratio = Math.max(0, Math.min(1, (Number(seconds) || 0) / Math.max(1, duration)));
		return "calc(10px + " + ratio + " * (100% - 20px))";
	}

	track_playhead($strip, duration) {
		clearInterval(this._playhead_timer);
		const $cursor = $strip.find(".tb-timeline-cursor");
		this._playhead_timer = setInterval(() => {
			if (!$strip.closest("body").length) {
				clearInterval(this._playhead_timer);
				return;
			}
			const at = this.preview_video ? this.preview_video.video.currentTime : 0;
			$cursor.css("left", this.offset_for(at, duration));
		}, 250);
	}

	seconds_at(clientX, $strip, duration) {
		const rect = $strip[0].getBoundingClientRect();
		const ratio = (clientX - rect.left - 10) / Math.max(1, rect.width - 20);
		return Math.round(Math.max(0, Math.min(1, ratio)) * duration);
	}

	paint_pins(lesson, block, $strip, duration) {
		$strip.find(".tb-timeline-pin, .tb-timeline-time").remove();
		const pins = this.pins_for(lesson, block);
		pins.forEach((cp) => {
			const left = this.offset_for(cp.at_seconds, duration);
			const unreviewed = cp.ai_generated && !cp.ai_reviewed_by;
			const $pin = $(`<button type="button" class="tb-timeline-pin ${unreviewed ? "is-unreviewed" : ""} ${cp.checkpoint_key === this.checkpoint_key ? "is-active" : ""}"
					aria-label="${__("Checkpoint at {0}", [tb_mmss(cp.at_seconds)])}"></button>`)
				.css("left", left)
				.appendTo($strip);
			$(`<span class="tb-timeline-time"></span>`)
				.css("left", left)
				.text(tb_mmss(cp.at_seconds))
				.appendTo($strip);

			$pin.on("click", (event) => {
				event.stopPropagation();
				this.select_checkpoint(cp.checkpoint_key);
			});
			this.bind_pin_drag($pin, lesson, block, cp, $strip, duration);
		});
	}

	pins_for(lesson, block) {
		return (lesson.checkpoints || [])
			.filter((cp) => cp.block_key === block.block_key)
			.sort((a, b) => Number(a.at_seconds) - Number(b.at_seconds));
	}

	bind_pin_drag($pin, lesson, block, cp, $strip, duration) {
		if (!this.editable()) return;
		$pin.on("pointerdown", (event) => {
			event.preventDefault();
			event.stopPropagation();
			$pin.addClass("is-dragging");
			$pin[0].setPointerCapture(event.pointerId);
			const move = (moveEvent) => {
				cp.at_seconds = this.seconds_at(moveEvent.clientX, $strip, duration);
				this.paint_pins(lesson, block, $strip, duration);
				// Live-seek. Placing a pin without seeing the frame is how a
				// checkpoint ends up asking about a slide that has not appeared
				// yet — the author lands on a timestamp, not on a moment.
				this.seek_preview(cp.at_seconds);
			};
			const up = () => {
				$pin.removeClass("is-dragging");
				$pin[0].removeEventListener("pointermove", move);
				$pin[0].removeEventListener("pointerup", up);
				this.persist_checkpoint(lesson, cp);
				this.render_inspector();
			};
			$pin[0].addEventListener("pointermove", move);
			$pin[0].addEventListener("pointerup", up);
		});
	}

	drop_pin_at_playhead() {
		const lesson = this.current_lesson();
		const block = this.current_block();
		if (!lesson || !block || block.block_type !== "Video") return false;
		const at = this.preview_video ? Math.round(this.preview_video.video.currentTime) : 0;
		this.add_pin(lesson, block, at);
		return true;
	}

	add_pin(lesson, block, at_seconds) {
		if (!this.guard_editable()) return;
		const cp = {
			// Transient until the save lands. checkpoint_key is server-owned for
			// the same reason lesson_key is: it is what stored learner answers
			// are filed under.
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
			// A pin on a block with checkpoints switched off never fires, and the
			// author has no way of knowing why. Turn it on with the first pin.
			this.set_block_field(lesson, block, "checkpoints_enabled", 1);
		}
		this.persist_checkpoint(lesson, cp);
		this.render_canvas();
		this.render_inspector();
	}

	select_checkpoint(key) {
		this.checkpoint_key = key;
		this.render_canvas();
		this.render_inspector();
		if (window.matchMedia("(max-width: 1100px)").matches) this.toggle_inspector(true);
	}

	current_checkpoint() {
		const lesson = this.current_lesson();
		if (!lesson || !this.checkpoint_key) return null;
		return (lesson.checkpoints || []).find((cp) => cp.checkpoint_key === this.checkpoint_key) || null;
	}

	// Inline, and shown on the pin's own panel rather than raised at save time:
	// an author who has placed twelve pins and is told at the end that two of
	// them clash has to go and find them again.
	checkpoint_problems(lesson, block, cp) {
		const problems = [];
		const duration = this.block_duration(block);
		this.pins_for(lesson, block).forEach((other) => {
			if (other.checkpoint_key === cp.checkpoint_key) return;
			if (Math.abs(Number(other.at_seconds) - Number(cp.at_seconds)) < TB_PIN_MIN_GAP) {
				problems.push(
					__("Another checkpoint sits at {0}, within {1} seconds of this one.", [
						tb_mmss(other.at_seconds),
						TB_PIN_MIN_GAP,
					])
				);
			}
		});
		if (duration && Number(cp.at_seconds) > duration - TB_PIN_TAIL_GUARD) {
			problems.push(
				__("This is in the last {0} seconds — it may fire after the video has ended.", [
					TB_PIN_TAIL_GUARD,
				])
			);
		}
		if (!(cp.options || []).some((option) => Number(option.is_correct))) {
			problems.push(__("No option is marked correct, so every answer will be graded wrong."));
		}
		if (!(cp.question_text || "").trim()) problems.push(__("The question is empty."));
		return problems;
	}

	// ----- inspector -------------------------------------------------------

	render_inspector() {
		const $body = this.$inspector.find(".tb-pane-body").empty();
		const lesson = this.current_lesson();
		if (!lesson) return;

		const cp = this.current_checkpoint();
		if (cp) this.render_checkpoint_section($body, lesson, cp);

		const block = this.current_block();
		if (block && !cp) this.render_block_section($body, lesson, block);

		this.render_lesson_section($body, lesson);
		this.render_quiz_section($body, lesson);
		this.render_ai_drawer($body, lesson);
	}

	section($parent, title) {
		const $section = $('<div class="tb-section"></div>').appendTo($parent);
		$('<div class="tb-section-head"></div>').text(title).appendTo($section);
		return $section;
	}

	field($parent, label, type, value, onchange, options) {
		const $field = $('<div class="tb-field"></div>').appendTo($parent);
		const id = "tb-" + frappe.utils.get_random(6);
		$("<label></label>").attr("for", id).text(label).appendTo($field);
		let $input;
		if (type === "select") {
			$input = $('<select class="tb-select"></select>').attr("id", id);
			// Options are either bare values or {value, label}. Chapters are the
			// reason: chapter_key is a generated token and showing it to an
			// author is showing them the plumbing.
			(options || []).forEach((option) => {
				const value = option && option.value !== undefined ? option.value : option;
				const label = (option && option.label) || value;
				$("<option></option>")
					.attr("value", value)
					.text(label || __("(none)"))
					.appendTo($input);
			});
			$input.val(value == null ? "" : String(value));
		} else if (type === "textarea") {
			$input = $('<textarea class="tb-textarea"></textarea>').attr("id", id).val(value || "");
		} else {
			$input = $('<input class="tb-input">')
				.attr("id", id)
				.attr("type", type || "text")
				.val(value == null ? "" : value);
		}
		$input.prop("disabled", !this.editable());
		$input.on("change", (event) => onchange(event.target.value));
		$input.appendTo($field);
		return $field;
	}

	check($parent, label, value, onchange) {
		const $label = $('<label class="tb-check"></label>').appendTo($parent);
		const $box = $('<input type="checkbox">')
			.prop("checked", !!Number(value))
			.prop("disabled", !this.editable())
			.appendTo($label);
		$("<span></span>").text(label).appendTo($label);
		$box.on("change", (event) => onchange(event.target.checked ? 1 : 0));
		return $label;
	}

	render_lesson_section($body, lesson) {
		const $section = this.section($body, __("Lesson"));
		this.field($section, __("Title"), "text", lesson.lesson_title, (v) =>
			this.set_lesson_field(lesson, "lesson_title", v)
		);
		this.field($section, __("Summary"), "textarea", lesson.summary, (v) =>
			this.set_lesson_field(lesson, "summary", v)
		);
		this.field($section, __("Estimated minutes"), "number", lesson.estimated_minutes, (v) =>
			this.set_lesson_field(lesson, "estimated_minutes", Number(v) || 0)
		);
		this.field(
			$section,
			__("Chapter"),
			"select",
			lesson.chapter_key,
			(v) => this.set_lesson_field(lesson, "chapter_key", v),
			[{ value: "", label: __("Unfiled") }].concat(
				(this.chapters || []).map((c) => ({ value: c.chapter_key, label: c.chapter_title }))
			)
		);
		this.check($section, __("Learners may ask questions"), lesson.allow_questions, (v) =>
			this.set_lesson_field(lesson, "allow_questions", v)
		);

		const $transcript = this.section($body, __("Transcript"));
		this.field($transcript, __("WebVTT or plain text"), "textarea", lesson.transcript, (v) =>
			this.set_lesson_field(lesson, "transcript", v)
		);
		$('<div class="tb-hint"></div>')
			.text(
				__(
					"Checkpoint drafting needs timings, so it refuses anything but a .vtt. Without them a model invents timestamps that look entirely plausible."
				)
			)
			.appendTo($transcript);
		const $vtt = $(`<button type="button" class="tb-icon-btn">${__("Upload .vtt")}</button>`)
			.appendTo($transcript)
			.on("click", () => this.upload_vtt(lesson));
		$vtt.prop("disabled", !this.editable());
	}

	render_block_section($body, lesson, block) {
		const $section = this.section($body, __("Block — {0}", [block.block_type]));
		this.field($section, __("Heading"), "text", block.heading, (v) =>
			this.set_block_field(lesson, block, "heading", v)
		);
		this.field($section, __("Caption"), "text", block.caption, (v) =>
			this.set_block_field(lesson, block, "caption", v)
		);

		if (block.block_type === "External Embed") {
			this.field($section, __("Embed URL"), "text", block.embed_url, (v) =>
				this.set_block_field(lesson, block, "embed_url", v)
			);
		}

		if (block.block_type === "Video") {
			this.render_video_controls($section, lesson, block);
		}

		if (["Image", "PDF", "Downloadable File"].indexOf(block.block_type) !== -1) {
			const $upload = $(`<button type="button" class="tb-icon-btn">${__("Upload file")}</button>`)
				.appendTo($section)
				.on("click", () => this.upload_attachment(lesson, block));
			$upload.prop("disabled", !this.editable());
		}

		const $gates = this.section($body, __("Gating"));
		this.check($gates, __("Required to finish the lesson"), block.required_for_completion, (v) =>
			this.set_block_field(lesson, block, "required_for_completion", v)
		);
		if (block.block_type === "Video") {
			this.field($gates, __("Minimum watch coverage %"), "number", block.min_coverage_percent, (v) =>
				this.set_block_field(lesson, block, "min_coverage_percent", Number(v) || 0)
			);
			this.check($gates, __("In-video checkpoints"), block.checkpoints_enabled, (v) =>
				this.set_block_field(lesson, block, "checkpoints_enabled", v)
			);
		}
	}

	// What is actually wrong with this video, in the place the author is looking.
	//
	// A failed copy used to be invisible here: the asset carried `status = Error`
	// and a traceback, and the builder said only "This video asset is Error" — so a
	// broken video and one that simply had not finished copying looked identical,
	// and neither said what to do. The commonest cause is the most fixable: the
	// file was never shared with the service account that does the copying.
	render_asset_state($section, lesson, block) {
		const asset = this.video_asset(block);
		if (!asset) return;

		if (asset.status === "Error") {
			const $box = $('<div class="tb-warn"></div>').appendTo($section);
			$("<div></div>")
				.text(__("The copy from Drive failed, so this video will not play for a learner."))
				.appendTo($box);
			if (asset.last_error) {
				// Drive answers 404 rather than 403 for a file the service account
				// cannot see, so "File not found" almost always means "not shared".
				// Saying that here saves the hour it took to work out the first time.
				const notFound = /404|not found/i.test(asset.last_error);
				$("<div></div>")
					.text(
						notFound
							? __("Drive says the file was not found. That usually means it is not shared with the service account rather than missing — check sharing on the file or the shared drive it lives in.")
							: asset.last_error
					)
					.appendTo($box);
			}
			$(`<button type="button" class="btn btn-xs">${__("Retry the copy")}</button>`)
				.appendTo($box)
				.on("click", (event) => this.retry_video_copy(asset, $(event.currentTarget)));
			return;
		}

		if (!asset.copied) {
			$('<div class="tb-muted"></div>')
				.text(
					__("Not copied into the bucket yet. Publishing queues the copy; a learner sees nothing here until it finishes.")
				)
				.appendTo($section);
		}

		// The quiet one, and the one that matters most. `Manual` means the length
		// was typed rather than read from Drive, and evaluate_gates WAIVES the
		// coverage gate in that case rather than scoring against a guess. An author
		// who thinks they set an 80% gate deserves to know it is not being applied.
		if (asset.duration_source !== "Probed") {
			$('<div class="tb-warn"></div>')
				.text(
					__("This video's length was entered by hand, not read from Drive, so the watch-coverage gate is waived for it. Re-add it from Drive to have the real length probed.")
				)
				.appendTo($section);
		}
	}

	retry_video_copy(asset, $button) {
		$button.prop("disabled", true).text(__("Copying…"));
		frappe
			.call({
				method: "erpnext_enhancements.api.training_author.retry_video_copy",
				args: { video_asset: asset.name },
			})
			.then((r) => {
				const result = (r && r.message) || {};
				if (result.ok) {
					frappe.show_alert({ message: __("Copied. The video is ready."), indicator: "green" });
					this.reload();
					return;
				}
				$button.prop("disabled", false).text(__("Retry the copy"));
				frappe.msgprint({
					title: __("Still failing"),
					indicator: "red",
					message: result.last_error || result.reason || __("The copy did not succeed."),
				});
			})
			.catch(() => $button.prop("disabled", false).text(__("Retry the copy")));
	}

	register_drive_video(lesson, block) {
		if (!this.guard_editable()) return;
		const dialog = new frappe.ui.Dialog({
			title: __("Add a video from Drive"),
			fields: [
				{
					fieldname: "drive_file_id",
					fieldtype: "Data",
					label: __("Drive link or file id"),
					reqd: 1,
					description: __(
						"Paste the address of the video itself. A folder link or the shared drive's own link will not work, and both fail later with an unhelpful 'file not found'."
					),
				},
				{ fieldname: "title", fieldtype: "Data", label: __("Title"),
				  description: __("Optional — Drive's own name is used when this is blank.") },
			],
			primary_action_label: __("Register"),
			primary_action: (values) => {
				dialog.hide();
				frappe
					.call({
						method: "erpnext_enhancements.api.training_author.register_video_asset",
						args: { drive_file_id: values.drive_file_id, title: values.title || null },
						freeze: true,
						freeze_message: __("Reading the video from Drive…"),
					})
					.then((r) => {
						const result = (r && r.message) || {};
						if (!result.video_asset) return;
						this.set_block_field(lesson, block, "video_asset", result.video_asset);
						// `duration_probed` is the honest signal and the only one worth
						// interrupting for: a false means the service account could not
						// read the file, the length is a placeholder, and the coverage
						// gate will be waived rather than enforced.
						if (result.duration_probed) {
							frappe.show_alert({ message: __("Registered, and its length was read from Drive."), indicator: "green" });
						} else {
							frappe.msgprint({
								title: __("Registered, but Drive could not be read"),
								indicator: "orange",
								message: __(
									"The video is linked, but its length could not be read from Drive — which usually means the file is not shared with the service account that copies it. Until that is fixed the video will not play for a learner and the watch-coverage gate is waived."
								),
							});
						}
						this.reload();
					});
			},
		});
		dialog.show();
	}

	render_video_controls($section, lesson, block) {
		const $picker = $('<div class="tb-field"></div>').appendTo($section);
		$("<label></label>").text(__("Video asset")).appendTo($picker);
		const control = frappe.ui.form.make_control({
			parent: $picker[0],
			df: {
				fieldname: "video_asset",
				fieldtype: "Link",
				options: "Training Video Asset",
				placeholder: __("Pick a registered video"),
				change: () => {
					const value = control.get_value();
					if (value !== block.video_asset) this.set_block_field(lesson, block, "video_asset", value);
					this.render_canvas();
				},
			},
			render_input: true,
		});
		control.set_value(block.video_asset || "");
		control.$input && control.$input.prop("disabled", !this.editable());

		// The Link picker only chooses an ALREADY registered asset, so until this
		// existed there was no way to register one from the builder at all — the
		// author was told to go and do it, with no door. The Desk form is the
		// wrong door anyway: it lets a duration be typed and still labelled
		// "Probed", which is the one combination that makes the coverage gate
		// divide by a number nobody checked.
		const $add = $(`<button type="button" class="tb-icon-btn">${__("Add from Drive…")}</button>`)
			.appendTo($section)
			.on("click", () => this.register_drive_video(lesson, block));
		$add.prop("disabled", !this.editable());

		const $upload = $(`<button type="button" class="tb-icon-btn">${__("Upload video")}</button>`)
			.appendTo($section)
			.on("click", () => this.upload_video(lesson, block));
		$upload.prop("disabled", !this.editable());

		this.render_asset_state($section, lesson, block);

		if (this.ai_enabled) {
			// `has_transcript` rides along on the video asset for exactly this:
			// greying the action out is a better answer than letting the author
			// press it and collect a refusal.
			const timed = !!(asset && asset.has_transcript) || /-->/.test(lesson.transcript || "");
			$(`<button type="button" class="tb-icon-btn">✨ ${__("Draft checkpoints with AI")}</button>`)
				.prop("disabled", !this.editable() || !timed)
				.on("click", () => this.draft_checkpoints(lesson, block))
				.appendTo($section);
			$('<div class="tb-hint"></div>')
				.text(
					timed
						? __("Suggestions are grounded in the transcript and none of them is saved until you accept it.")
						: __("Needs a .vtt with cue timings. Without timings a model invents timestamps that look entirely plausible, so the endpoint refuses.")
				)
				.appendTo($section);
		}
	}

	render_checkpoint_section($body, lesson, cp) {
		const block = (lesson.blocks || []).find((row) => row.block_key === cp.block_key);
		const $section = this.section($body, __("Checkpoint at {0}", [tb_mmss(cp.at_seconds)]));

		const problems = block ? this.checkpoint_problems(lesson, block, cp) : [];
		problems.forEach((problem) => $('<div class="tb-warn"></div>').text(problem).appendTo($section));

		this.field($section, __("At (seconds)"), "number", cp.at_seconds, (v) => {
			cp.at_seconds = Math.max(0, Number(v) || 0);
			this.persist_checkpoint(lesson, cp);
			this.render_canvas();
			this.render_inspector();
		});
		this.field($section, __("Question"), "textarea", cp.question_text, (v) => {
			cp.question_text = v;
			this.persist_checkpoint(lesson, cp);
		});
		this.field(
			$section,
			__("Type"),
			"select",
			cp.question_type,
			(v) => {
				cp.question_type = v;
				this.persist_checkpoint(lesson, cp);
			},
			["Single Choice", "Multiple Choice", "True-False"]
		);

		const $options = this.section($body, __("Options"));
		(cp.options || []).forEach((option, index) => {
			const $row = $('<div class="tb-field"></div>').appendTo($options);
			const $input = $('<input class="tb-input">')
				.val(option.option_text || "")
				.prop("disabled", !this.editable())
				.on("change", (event) => {
					option.option_text = event.target.value;
					this.persist_checkpoint(lesson, cp);
				});
			$row.append($input);
			this.check($row, __("Correct"), option.is_correct, (v) => {
				if (v && cp.question_type !== "Multiple Choice") {
					(cp.options || []).forEach((other) => (other.is_correct = 0));
				}
				option.is_correct = v;
				this.persist_checkpoint(lesson, cp);
				this.render_inspector();
			});
			$(`<button type="button" class="tb-icon-btn" aria-label="${__("Remove option")}">×</button>`)
				.prop("disabled", !this.editable())
				.on("click", () => {
					cp.options.splice(index, 1);
					this.persist_checkpoint(lesson, cp);
					this.render_inspector();
				})
				.appendTo($row);
		});
		$(`<button type="button" class="tb-icon-btn">+ ${__("Option")}</button>`)
			.prop("disabled", !this.editable())
			.on("click", () => {
				cp.options = cp.options || [];
				cp.options.push({
					option_key: String.fromCharCode(97 + cp.options.length),
					option_text: "",
					is_correct: 0,
				});
				this.persist_checkpoint(lesson, cp);
				this.render_inspector();
			})
			.appendTo($options);

		const $behaviour = this.section($body, __("Behaviour"));
		this.field($behaviour, __("Explanation"), "textarea", cp.explanation, (v) => {
			cp.explanation = v;
			this.persist_checkpoint(lesson, cp);
		});
		this.check($behaviour, __("Pause the video"), cp.pause_video, (v) => {
			cp.pause_video = v;
			this.persist_checkpoint(lesson, cp);
		});
		this.check($behaviour, __("Allow skipping"), cp.allow_skip, (v) => {
			cp.allow_skip = v;
			this.persist_checkpoint(lesson, cp);
		});
		this.check($behaviour, __("Counts toward the score"), cp.counts_toward_score, (v) => {
			cp.counts_toward_score = v;
			this.persist_checkpoint(lesson, cp);
		});
		this.field($behaviour, __("Attempts"), "number", cp.max_attempts, (v) => {
			cp.max_attempts = Number(v) || 0;
			this.persist_checkpoint(lesson, cp);
		});
		this.field($behaviour, __("Rewind on wrong (s)"), "number", cp.rewind_seconds_on_wrong, (v) => {
			cp.rewind_seconds_on_wrong = Number(v) || 0;
			this.persist_checkpoint(lesson, cp);
		});

		const $actions = this.section($body, __("Try it"));
		$(`<button type="button" class="tb-icon-btn">▶ ${__("Test this checkpoint")}</button>`)
			.on("click", () => this.test_checkpoint(lesson, cp))
			.appendTo($actions);
		$('<div class="tb-hint"></div>')
			.text(__("Plays from {0} seconds before the pin and runs the real learner overlay.", [TB_TEST_LEAD_SECONDS]))
			.appendTo($actions);
		$(`<button type="button" class="tb-icon-btn">${__("Delete checkpoint")}</button>`)
			.prop("disabled", !this.editable())
			.on("click", () => this.delete_checkpoint(lesson, cp))
			.appendTo($actions);
	}

	render_quiz_section($body, lesson) {
		const $section = this.section($body, __("Quiz"));
		this.check($section, __("This lesson has a quiz"), lesson.has_quiz, (v) =>
			this.set_lesson_field(lesson, "has_quiz", v)
		);
		if (!Number(lesson.has_quiz)) return;

		this.field($section, __("Questions to ask"), "number", lesson.quiz_questions_to_ask, (v) =>
			this.set_lesson_field(lesson, "quiz_questions_to_ask", Number(v) || 0)
		);
		this.field($section, __("Pass score %"), "number", lesson.quiz_pass_score, (v) =>
			this.set_lesson_field(lesson, "quiz_pass_score", Number(v) || 0)
		);
		this.check($section, __("Shuffle questions"), lesson.quiz_shuffle_questions, (v) =>
			this.set_lesson_field(lesson, "quiz_shuffle_questions", v)
		);
		this.check($section, __("Shuffle options"), lesson.quiz_shuffle_options, (v) =>
			this.set_lesson_field(lesson, "quiz_shuffle_options", v)
		);

		const $list = $('<div class="tb-ai-list"></div>').appendTo($section);
		// The accepted pool only. Drafts live in the drawer below and are never
		// listed here — an author must not be able to mistake a suggestion for
		// something the quiz will actually ask.
		(lesson.quiz || []).forEach((question) => {
			const unreviewed = question.ai_generated && !question.ai_reviewed_by;
			const $card = $('<div class="tb-ai-card"></div>').appendTo($list);
			$('<div class="tb-ai-question"></div>')
				.text(tb_plain(question.question_text || question.text || ""))
				.appendTo($card);
			if (unreviewed) {
				$('<div class="tb-warn"></div>')
					.text(__("AI-drafted and not reviewed. This blocks Submit for Review."))
					.appendTo($card);
			}
		});
		if (!(lesson.quiz || []).length) {
			$('<div class="tb-ai-empty"></div>').text(__("No questions in the pool yet.")).appendTo($list);
		}

		if (this.ai_enabled) {
			$(`<button type="button" class="tb-icon-btn">✨ ${__("Draft questions with AI")}</button>`)
				.prop("disabled", !this.editable())
				.on("click", () => this.draft_quiz(lesson))
				.appendTo($section);
		}
	}

	// ----- AI drafts drawer ------------------------------------------------

	render_ai_drawer($body, lesson) {
		const drafts = this.ai_drafts;
		const $drawer = $('<div class="tb-ai-drawer"></div>').appendTo($body);
		// Drafts belong to the lesson they were drafted from. Rendering them
		// under a different one is how a question gets accepted onto the wrong
		// pool, and the drawer gives no clue that it happened.
		if (drafts.lesson && drafts.lesson !== lesson.name) return;
		if (!drafts.items.length && !drafts.busy && !drafts.message) return;
		$drawer.addClass("is-open");

		const $head = $('<div class="tb-ai-head"></div>').appendTo($drawer);
		$("<span></span>")
			.text(drafts.kind === "checkpoint" ? __("Drafted checkpoints") : __("Drafted questions"))
			.appendTo($head);
		// "Reject all" exists and "Accept all" deliberately does not. Accepting is
		// the human review the publish gate is built on; a button that performs it
		// in bulk without anyone reading anything makes the gate ornamental.
		$(`<button type="button" class="tb-icon-btn tb-ai-close">${__("Reject all")}</button>`)
			.on("click", () => {
				this.ai_drafts = { kind: null, lesson: null, block_key: null, items: [], message: "", busy: false };
				this.render_inspector();
			})
			.appendTo($head);

		if (drafts.busy) {
			$('<div class="tb-ai-busy"></div>').text(__("Drafting…")).appendTo($drawer);
			return;
		}

		if (drafts.message) $('<div class="tb-warn"></div>').text(drafts.message).appendTo($drawer);
		if (!drafts.items.length) {
			$('<div class="tb-ai-empty"></div>').text(__("Nothing to review.")).appendTo($drawer);
			return;
		}

		const $list = $('<div class="tb-ai-list"></div>').appendTo($drawer);
		drafts.items.forEach((item) => $list.append(this.ai_card(lesson, item)));
	}

	ai_card(lesson, item) {
		const $card = $('<div class="tb-ai-card"></div>');
		if (item.accepted) $card.addClass("is-accepted");
		if (!item.grounding_quote) $card.addClass("is-ungrounded");

		$('<div class="tb-ai-question"></div>').text(item.question || "").appendTo($card);

		const $options = $('<ul class="tb-ai-options"></ul>').appendTo($card);
		(item.options || []).forEach((option) => {
			$("<li></li>")
				.toggleClass("is-correct", !!Number(option.is_correct))
				.text(option.text || option.option_text || "")
				.appendTo($options);
		});

		if (item.grounding_quote) {
			const $quote = $('<div class="tb-ai-quote"></div>').text("“" + item.grounding_quote + "”").appendTo($card);
			$(`<button type="button" class="tb-undo">${__("Jump to source")}</button>`)
				.on("click", () => this.jump_to_source(item))
				.appendTo($quote);
		} else {
			$('<div class="tb-warn"></div>')
				.text(__("No grounding quote — this may not come from the lesson at all."))
				.appendTo($card);
		}

		if (item.accepted) return $card;

		const $actions = $('<div class="tb-ai-actions"></div>').appendTo($card);
		$(`<button type="button" class="tb-icon-btn">${__("Accept")}</button>`)
			.on("click", () => this.accept_draft(lesson, item))
			.appendTo($actions);
		$(`<button type="button" class="tb-icon-btn">${__("Edit")}</button>`)
			.on("click", () => this.edit_draft(lesson, item))
			.appendTo($actions);
		$(`<button type="button" class="tb-icon-btn">${__("Reject")}</button>`)
			.on("click", () => {
				this.ai_drafts.items = this.ai_drafts.items.filter((row) => row.id !== item.id);
				this.render_inspector();
			})
			.appendTo($actions);
		return $card;
	}

	jump_to_source(item) {
		// Two kinds of source. A checkpoint's is a moment in the video, so seek
		// the preview to it; a quiz question's is a sentence in a block, so find
		// and flash it.
		if (item.at_seconds != null) {
			this.seek_preview(Number(item.at_seconds));
			return;
		}
		const needle = (item.grounding_quote || "").slice(0, 40).toLowerCase();
		if (!needle) return;
		const $hit = this.$canvas
			.find(".tb-block")
			.filter((_, el) => ($(el).text() || "").toLowerCase().indexOf(needle) !== -1)
			.first();
		if (!$hit.length) {
			frappe.show_alert({
				message: __("That sentence is not in this lesson's blocks — check the suggestion carefully."),
				indicator: "orange",
			});
			return;
		}
		$hit[0].scrollIntoView({ behavior: "smooth", block: "center" });
		$hit.addClass("is-selected");
		setTimeout(() => $hit.removeClass("is-selected"), 1600);
	}

	draft_quiz(lesson) {
		this.ai_drafts = { kind: "quiz", lesson: lesson.name, block_key: null, items: [], message: "", busy: true };
		this.render_inspector();
		frappe
			.call({
				method: "erpnext_enhancements.api.training_ai.draft_quiz_questions",
				args: { lesson: lesson.name, count: 5 },
			})
			.then((r) => {
				const payload = (r && r.message) || {};
				this.ai_drafts.busy = false;
				// The endpoint drops anything it could not ground in the lesson and
				// explains why in `message`. Showing an empty drawer instead would
				// read as the feature being broken.
				this.ai_drafts.message = payload.message || "";
				this.ai_drafts.items = (payload.suggestions || []).map((item) => ({
					...item,
					id: item.id || frappe.utils.get_random(8),
				}));
				this.render_inspector();
			})
			.catch(() => {
				this.ai_drafts.busy = false;
				this.render_inspector();
			});
	}

	draft_checkpoints(lesson, block) {
		this.ai_drafts = {
			kind: "checkpoint",
			lesson: lesson.name,
			block_key: block.block_key,
			items: [],
			message: "",
			busy: true,
		};
		this.render_inspector();
		frappe
			.call({
				method: "erpnext_enhancements.api.training_ai.suggest_checkpoints",
				args: { lesson: lesson.name, block_key: block.block_key, count: 3 },
			})
			.then((r) => {
				const payload = (r && r.message) || {};
				this.ai_drafts.busy = false;
				// The endpoint drops anything it could not ground in the lesson and
				// explains why in `message`. Showing an empty drawer instead would
				// read as the feature being broken.
				this.ai_drafts.message = payload.message || "";
				this.ai_drafts.items = (payload.suggestions || []).map((item) => ({
					...item,
					id: item.id || frappe.utils.get_random(8),
				}));
				this.render_inspector();
			})
			.catch(() => {
				// The endpoint refuses without a timed transcript and says so in
				// its own message; frappe has already shown it.
				this.ai_drafts.busy = false;
				this.render_inspector();
			});
	}

	edit_draft(lesson, item) {
		const dialog = new frappe.ui.Dialog({
			title: __("Edit before accepting"),
			fields: [
				{ fieldname: "question", fieldtype: "Small Text", label: __("Question"), default: item.question },
				{
					fieldname: "options",
					fieldtype: "Small Text",
					label: __("Options — one per line, prefix the correct one with *"),
					default: (item.options || [])
						.map((option) => (Number(option.is_correct) ? "*" : "") + (option.text || option.option_text || ""))
						.join("\n"),
				},
				{
					fieldname: "explanation",
					fieldtype: "Small Text",
					label: __("Explanation"),
					default: item.explanation,
				},
			],
			primary_action_label: __("Save and accept"),
			primary_action: (values) => {
				dialog.hide();
				item.question = values.question;
				item.explanation = values.explanation;
				item.options = String(values.options || "")
					.split("\n")
					.map((line) => line.trim())
					.filter(Boolean)
					.map((line) => ({
						text: line.replace(/^\*/, "").trim(),
						is_correct: line.startsWith("*") ? 1 : 0,
					}));
				// Edited, so it is no longer verbatim model output — but it is
				// still AI-drafted provenance and accept_ai_suggestions is still
				// the only thing entitled to stamp the reviewer.
				this.accept_draft(lesson, item);
			},
		});
		dialog.show();
	}

	accept_draft(lesson, item) {
		frappe
			.call({
				method: "erpnext_enhancements.api.training_ai.accept_ai_suggestions",
				args: {
					lesson: lesson.name,
					kind: this.ai_drafts.kind,
					suggestions: JSON.stringify([item]),
				},
			})
			.then((r) => {
				const created = (r && r.message && r.message.created) || 0;
				if (!created) return;
				item.accepted = true;
				frappe.show_alert({ message: __("Accepted — reviewed by you."), indicator: "green" });
				// Reload rather than splice the row in: the server minted the
				// key, the row name and the provenance stamps, and guessing any
				// of them here is how the pool and the drawer drift apart.
				this.save_then(__("Reloading after the accepted draft")).then(() => this.reload()).catch(() => {});
			});
	}

	// ----- uploads ---------------------------------------------------------

	// XMLHttpRequest, not fetch: a 300 MB video needs upload.onprogress, and
	// fetch has no upload progress event at all. A silent progress-free upload
	// of that size is indistinguishable from a hang.
	upload(file, extra, onprogress) {
		return new Promise((resolve, reject) => {
			const form = new FormData();
			form.append("file", file, file.name);
			form.append("is_private", "1");
			Object.entries(extra || {}).forEach(([key, value]) => form.append(key, value));

			const xhr = new XMLHttpRequest();
			xhr.open("POST", "/api/method/upload_file", true);
			xhr.setRequestHeader("X-Frappe-CSRF-Token", frappe.csrf_token);
			xhr.upload.onprogress = (event) => {
				if (event.lengthComputable && onprogress) onprogress(event.loaded / event.total);
			};
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

	pick_file(accept) {
		return new Promise((resolve) => {
			const input = document.createElement("input");
			input.type = "file";
			if (accept) input.accept = accept;
			input.onchange = () => resolve((input.files && input.files[0]) || null);
			input.click();
		});
	}

	upload_attachment(lesson, block) {
		const accept = block.block_type === "Image" ? "image/*" : block.block_type === "PDF" ? "application/pdf" : "";
		this.pick_file(accept).then((file) => {
			if (!file) return;
			const bar = tb_progress(__("Uploading {0}", [file.name]));
			this.upload(file, { doctype: "Training Lesson", docname: lesson.name }, bar.set)
				.then((result) => {
					bar.done();
					const target = block.block_type === "Image" ? "image" : "file";
					this.set_block_field(lesson, block, target, result.file_url);
					this.render_canvas();
				})
				.catch((error) => {
					bar.done();
					frappe.show_alert({ message: error.message, indicator: "red" });
				});
		});
	}

	upload_video(lesson, block) {
		this.pick_file("video/*").then((file) => {
			if (!file) return;
			const bar = tb_progress(__("Uploading {0}", [file.name]));
			// The local object URL is what makes the preview playable *now*.
			// A Training Video Asset still has to be registered for coverage
			// gating, because the gate divides by a server-probed duration and
			// nothing the browser measured here is authoritative.
			const local = URL.createObjectURL(file);
			const probe = document.createElement("video");
			probe.preload = "metadata";
			probe.onloadedmetadata = () => {
				this.local_media[block.block_key] = { url: local, duration: Math.round(probe.duration || 0) };
				this.render_canvas();
			};
			probe.src = local;

			this.upload(file, { doctype: "Training Lesson", docname: lesson.name }, bar.set)
				.then((result) => {
					bar.done();
					this.set_block_field(lesson, block, "file", result.file_url);
					frappe.msgprint({
						title: __("Video uploaded"),
						indicator: "orange",
						message: __(
							"The file is attached and plays in the preview. Register it as a Training Video Asset so its real length is probed — watch coverage is a fraction of that length, and a wrong one lets an 80% gate pass on half a watch."
						),
					});
				})
				.catch((error) => {
					bar.done();
					frappe.show_alert({ message: error.message, indicator: "red" });
				});
		});
	}

	upload_vtt(lesson) {
		this.pick_file(".vtt,text/vtt").then((file) => {
			if (!file) return;
			// Read locally rather than uploaded: the transcript is a lesson field,
			// and a round trip through File storage would leave a copy nobody
			// maintains alongside the one that is actually read.
			const reader = new FileReader();
			reader.onload = () => {
				const text = String(reader.result || "");
				if (!/-->/.test(text)) {
					frappe.msgprint({
						title: __("That is not a timed transcript"),
						indicator: "red",
						message: __(
							"A .vtt has cue timings (00:01:02.000 --> 00:01:06.000). Without them checkpoint drafting has nothing to place a question against and will refuse."
						),
					});
					return;
				}
				this.set_lesson_field(lesson, "transcript", text);
				this.render_inspector();
			};
			reader.readAsText(file);
		});
	}

	// ----- preview as learner ---------------------------------------------

	toggle_preview() {
		if (this.preview) this.teardown_preview(true);
		else this.start_preview(null);
	}

	start_preview(block_key) {
		const lesson = this.current_lesson();
		if (!lesson) return;
		this.flush_save();
		this.preview_from_block = block_key || null;

		clearInterval(this._playhead_timer);
		const $body = this.$canvas.find(".tb-pane-body").empty();
		const $shell = $('<div class="tb-preview-shell"></div>').appendTo($body);
		$(`<button type="button" class="tb-icon-btn">${__("Back to the blocks")}</button>`)
			.on("click", () => this.teardown_preview(true))
			.appendTo($shell);
		$('<div class="tb-preview-banner"></div>')
			.text(
				block_key
					? __("Preview — starting at this block. Answers are graded for real; nothing is recorded.")
					: __("Preview as a learner. Answers are graded for real; no progress is recorded.")
			)
			.appendTo($shell);
		const $root = $('<div class="tb-preview-root"></div>').appendTo($shell);

		this.load_player()
			.then(() => {
				if (!window.TR || typeof window.TR.Player !== "function") {
					$root.html(`<div class="tb-denied">${__("The learner player did not load.")}</div>`);
					return;
				}
				const boot = this.preview_boot(lesson);
				const transport = this.preview_transport(lesson);
				this.preview = new window.TR.Player($root[0], boot, transport);
				// "Start preview here." The player renders the whole lesson, so
				// landing on the right block is a scroll rather than a different
				// payload — testing checkpoint four must not cost six minutes of
				// video, but it must also not be a different lesson.
				if (block_key) {
					setTimeout(() => {
						const target = $root[0].querySelector(`[data-block-key="${block_key}"]`);
						if (target) target.scrollIntoView({ block: "start" });
					}, 120);
				}
			})
			.catch(() => {
				$root.html(`<div class="tb-denied">${__("The learner player did not load.")}</div>`);
			})
			.catch((error) => {
				// Without this the pane just stayed blank forever: the promise
				// rejected, nothing was listening, and the author had no way to tell
				// a broken preview from a lesson that renders to nothing.
				$root.html(
					`<div class="tb-denied">${__("The learner player could not be loaded, so this preview is not available.")}<br>${frappe.utils.escape_html(String((error && error.message) || ""))}</div>`
				);
			});
	}

	teardown_preview(rerender) {
		if (this.preview && typeof this.preview.destroy === "function") {
			try {
				this.preview.destroy();
			} catch (e) {
				/* already gone */
			}
		}
		if (this.preview_video && typeof this.preview_video.destroy === "function") {
			try {
				this.preview_video.destroy();
			} catch (e) {
				/* already gone */
			}
		}
		this.preview = null;
		this.preview_video = null;
		if (rerender) this.render_canvas();
	}

	load_player() {
		// Deliberately NOT frappe.require. `frappe.assets.extn()` works out an
		// asset's type by splitting the URL on "?" and taking the LAST segment, so
		// a cache-busted "/assets/.../player.js?v=1.218.0" reports its extension as
		// "0" and matches neither the js branch nor the css branch. The file is
		// then silently never loaded, `window.TR` never appears, and every Preview
		// fails — 100% of the time, since the bust token is always present.
		//
		// Dropping the token is not the fix: raw /assets paths are served immutable
		// for a year with no content hash (see public/README.md), so without it an
		// edit never reaches a browser that already cached the old copy. This repo
		// has shipped that bug twice. So load them by hand and keep the token.
		//
		// `async = false` on a dynamically inserted script preserves execution
		// order between these scripts, which matters: player.js expects blocks,
		// video and quiz to have registered themselves on window.TR first.
		if (this._player_promise) return this._player_promise;
		const version = (frappe.boot.versions && frappe.boot.versions.erpnext_enhancements) || "0";
		this._player_promise = Promise.all(
			TB_PLAYER_ASSETS.map((path) => tb_load_asset(path + "?v=" + encodeURIComponent(version)))
		).catch((error) => {
			// Cleared so a retry can genuinely retry rather than replaying a
			// rejected promise for the rest of the session.
			this._player_promise = null;
			throw error;
		});
		return this._player_promise;
	}

	// There were compatibility shims here. They patched `TR.Video.mount` into
	// existence and re-shaped `TR.Quiz.mount`'s arguments, and their own comment
	// conceded they were "not a substitute for fixing them" in the runtime.
	//
	// Nothing was ever fixed, and the shims are why nobody noticed: the preview
	// showed the author a working lesson while every learner got "the video player
	// did not load" and a quiz with no questions in it. A preview that repairs the
	// runtime on its way past is worse than no preview at all, because it removes
	// the one place the break would have been seen.
	//
	// Both are fixed at source now. `Video.mount` lives in video.js beside the
	// constructor it builds, and player.js calls `TR.Quiz.mount` with the
	// signature quiz.js documents. This preview drives exactly what a learner gets,
	// which is the only thing that makes it worth having.


	preview_boot(lesson) {
		return {
			courses: [],
			settings: { heartbeat_interval_seconds: 10, max_playback_rate: 1.25 },
			resume: null,
			view: "lesson",
			start: { course: this.course.name, lesson_key: lesson.lesson_key },
			route_base: "/app/training-builder",
			// The builder owns the address bar. `history: false` also stops the
			// player calling replaceState, which a desk route would fight over.
			history: false,
			translate: (text) => __(text),
		};
	}

	// The player's payload, assembled from the draft. This mirrors
	// `training_author._split_lesson` and is the ONLY place in the builder that
	// builds a learner payload — that function's own docstring explains why the
	// count of such places matters. It is safe to include answers here because
	// nothing leaves the browser: the author already has them on screen, and a
	// draft has no published payload for the server to serve instead.
	preview_lesson(lesson) {
		const blocks = (lesson.blocks || [])
			.filter((block) => !this.removed_blocks[block.block_key])
			.map((block) => ({
				block_key: block.block_key,
				type: block.block_type,
				heading: block.heading || "",
				// The draft's raw `content` becomes the player's `html`. Sanitised
				// with the desk sanitizer because publish sanitises server-side and
				// the preview must not be the one place that does not.
				html: frappe.utils.xss_sanitise(block.content || ""),
				image: block.image || "",
				file: block.file || "",
				video_asset: block.video_asset || "",
				duration_s: this.block_duration(block),
				embed_url: block.embed_url || "",
				poster: block.poster_image || "",
				caption: block.caption || "",
				required: Number(block.required_for_completion) || 0,
				min_coverage: Number(block.min_coverage_percent) || 0,
				checkpoints_enabled: Number(block.checkpoints_enabled) || 0,
			}));

		const counts = {};
		(lesson.checkpoints || []).forEach((cp) => {
			counts[cp.block_key] = (counts[cp.block_key] || 0) + 1;
		});

		return {
			lesson_key: lesson.lesson_key,
			title: lesson.lesson_title || "",
			summary: lesson.summary || "",
			minutes: Number(lesson.estimated_minutes) || 0,
			allow_questions: Number(lesson.allow_questions) || 0,
			blocks,
			quiz: {
				enabled: Number(lesson.has_quiz) || 0,
				questions_to_ask: Number(lesson.quiz_questions_to_ask) || 0,
				pass_score: Number(lesson.quiz_pass_score) || 0,
				shuffle_questions: Number(lesson.quiz_shuffle_questions) || 0,
				shuffle_options: Number(lesson.quiz_shuffle_options) || 0,
				questions: [],
			},
			checkpoint_count: counts,
		};
	}

	preview_outline() {
		return this.lessons
			.filter((row) => !this.removed_lessons[row.name])
			.map((row) => ({
				lesson_key: row.lesson_key,
				title: row.lesson_title || "",
				chapter_title: (this.chapters.find((c) => c.chapter_key === row.chapter_key) || {}).chapter_title || "",
				minutes: Number(row.estimated_minutes) || 0,
				has_quiz: Number(row.has_quiz) || 0,
				// Nothing is locked in preview: an author has to be able to open
				// lesson nine without completing eight, and the sequencing rule is
				// the server's to enforce for a real learner.
				locked: 0,
				status: "not_started",
			}));
	}

	/*
	 * The preview transport.
	 *
	 * EVERY method the player, video, quiz and block code calls has to be here.
	 * A missing one is a TypeError in the middle of a preview, and the list is
	 * not stable across future player changes — regenerate it, do not trust this
	 * comment:
	 *
	 *     grep -oh 'transport\.\w*' public/js/training/*.js | sort -u
	 *
	 * As of writing: answerCheckpoint, heartbeat, heartbeatBeacon, mediaUrl,
	 * openCheckpoint, startQuiz, submitQuiz — plus getLesson and completeLesson,
	 * which the player reaches through its own `call()` wrapper rather than by
	 * naming the property.
	 *
	 * Everything returns a Promise except heartbeatBeacon, which is synchronous
	 * because it stands in for sendBeacon on pagehide.
	 */
	preview_transport(lesson) {
		// In-memory only. Nothing here is written anywhere, which is the whole
		// distinction between "preview" and "an author quietly completing their
		// own compliance course".
		const state = {
			covered: {},
			answered: {},
			attempts: {},
			quiz_runs: 0,
			quiz_best: 0,
			run: 0,
			drawn: [],
		};
		const self = this;

		const answerKey = () => {
			const map = {};
			(lesson.quiz || []).forEach((question) => {
				const id = question.question || question.name;
				map[id] = {
					type: question.question_type || question.type || "Single Choice",
					points: Number(question.points) || 1,
					explanation: question.explanation || "",
					correct: (question.options || [])
						.filter((option) => Number(option.is_correct))
						.map((option) => option.option_key),
					accepted_text: String(question.correct_text_answers || "")
						.split("\n")
						.map((line) => line.trim().toLowerCase())
						.filter(Boolean),
				};
			});
			return map;
		};

		return {
			getLesson: (args) => {
				const key = (args && args.lesson_key) || lesson.lesson_key;
				const target = this.lessons.find((row) => row.lesson_key === key) || lesson;
				return Promise.resolve({
					attempt: "preview",
					status: "In Progress",
					course: {
						name: this.course.name,
						title: this.course.course_title,
						summary: this.course.summary || "",
						min_video_coverage: Number(this.course.min_video_coverage) || 0,
						passing_score: Number(this.course.passing_score) || 0,
					},
					outline: this.preview_outline(),
					lesson: this.preview_lesson(target),
					progress: { lessons: {} },
					resume: null,
				});
			},

			// Coverage is computed rather than stubbed at 100%, so a gate an
			// author set at 95% actually refuses in preview the way it will for
			// a learner who skimmed.
			heartbeat: (payload) => {
				const block_key = payload && payload.block_key;
				const seen = (state.covered[block_key] = state.covered[block_key] || {});
				(payload.ranges || payload.played || []).forEach((range) => {
					for (let s = Math.floor(range[0]); s < Math.ceil(range[1]); s++) seen[s] = 1;
				});
				const duration = Math.max(1, Number(payload && payload.duration) || 1);
				const coverage = Math.min(100, (Object.keys(seen).length / duration) * 100);
				return Promise.resolve({
					coverage,
					credited: Object.keys(seen).length,
					flags: [],
					next_checkpoint_at: null,
				});
			},

			// Synchronous by contract. Nothing to send, so simply claim the beat
			// so video.js can drop it from its replay queue.
			heartbeatBeacon: () => true,

			openCheckpoint: (args) => {
				const pending = (lesson.checkpoints || [])
					.filter((cp) => cp.block_key === args.block_key && !state.answered[cp.checkpoint_key])
					.sort((a, b) => Number(a.at_seconds) - Number(b.at_seconds))[0];
				if (!pending) return Promise.resolve(null);
				// NOTE: the flat shape is what video.js reads (`cp.checkpoint_key`).
				// The real endpoint wraps it as {enabled, checkpoint} — a Phase-2
				// mismatch reported in the handover. The preview follows the
				// player, because the player is the thing being previewed.
				return Promise.resolve(this.preview_checkpoint(pending));
			},

			answerCheckpoint: (args) => {
				const cp = (lesson.checkpoints || []).find(
					(row) => row.checkpoint_key === args.checkpoint_key
				);
				if (!cp) return Promise.resolve({ correct: false });
				const correct = (cp.options || [])
					.filter((option) => Number(option.is_correct))
					.map((option) => option.option_key)
					.sort();
				const given = (args.option_keys || []).slice().sort();
				const ok = correct.length === given.length && correct.every((key, i) => key === given[i]);
				const used = (state.attempts[cp.checkpoint_key] = (state.attempts[cp.checkpoint_key] || 0) + 1);
				const exhausted = ok || (Number(cp.max_attempts) > 0 && used >= Number(cp.max_attempts));
				if (exhausted) state.answered[cp.checkpoint_key] = 1;
				return Promise.resolve({
					correct: ok,
					exhausted: exhausted && !ok,
					attempts_used: used,
					explanation: cp.explanation || "",
				});
			},

			startQuiz: () => {
				state.run += 1;
				const pool = (lesson.quiz || []).slice();
				if (Number(lesson.quiz_shuffle_questions)) tb_shuffle(pool);
				const ask = Number(lesson.quiz_questions_to_ask) || pool.length;
				state.drawn = pool.slice(0, ask);
				return Promise.resolve({
					enabled: true,
					run: state.run,
					pass_score: Number(lesson.quiz_pass_score) || Number(self.course.passing_score) || 0,
					attempts_left: null,
					questions: state.drawn.map((question) => {
						const options = (question.options || []).map((option) => ({
							option_key: option.option_key,
							text: option.option_text || option.text || "",
						}));
						if (Number(lesson.quiz_shuffle_options)) tb_shuffle(options);
						return {
							question: question.question || question.name,
							type: question.question_type || "Single Choice",
							text: tb_plain(question.question_text || question.text || ""),
							points: Number(question.points) || 1,
							options,
						};
					}),
				});
			},

			// Graded for real, against the draft's own answer key, so an author
			// sees the wrong answers and the explanations a learner will see.
			submitQuiz: (args) => {
				const key = answerKey();
				let earned = 0;
				let total = 0;
				const per_question = [];
				state.drawn.forEach((question) => {
					const id = question.question || question.name;
					const entry = key[id] || { correct: [], points: 1, accepted_text: [] };
					const given = ((args.answers || {})[id] || []).slice();
					total += entry.points;
					let ok;
					if (entry.type === "Short Answer") {
						ok = entry.accepted_text.indexOf(String(given[0] || "").trim().toLowerCase()) !== -1;
					} else {
						const want = entry.correct.slice().sort();
						const got = given.slice().sort();
						ok = want.length === got.length && want.every((k, i) => k === got[i]);
					}
					if (ok) earned += entry.points;
					per_question.push({
						question: id,
						correct: ok,
						text: tb_plain(question.question_text || question.text || ""),
						explanation: entry.explanation || "",
					});
				});
				const score = total ? (earned / total) * 100 : 0;
				const pass = Number(lesson.quiz_pass_score) || Number(self.course.passing_score) || 0;
				state.quiz_runs += 1;
				state.quiz_best = Math.max(state.quiz_best, score);
				return Promise.resolve({
					score,
					passed: score >= pass,
					per_question,
					run: state.quiz_runs,
					best: state.quiz_best,
					attempts_left: null,
				});
			},

			// Refuses for the same reasons the server would, and records nothing.
			completeLesson: () => {
				const reasons = [];
				const courseMin = Number(self.course.min_video_coverage) || 0;
				(lesson.blocks || []).forEach((block) => {
					if (!Number(block.required_for_completion)) return;
					if (block.block_type !== "Video") return;
					const need = Number(block.min_coverage_percent) || courseMin;
					if (!need) return;
					const seen = Object.keys(state.covered[block.block_key] || {}).length;
					const duration = Math.max(1, this.block_duration(block));
					const have = (seen / duration) * 100;
					if (have < need) {
						reasons.push(
							__("Watch more of {0} — {1}% of {2}% so far.", [
								block.heading || __("the video"),
								Math.round(have),
								need,
							])
						);
					}
				});
				if (Number(lesson.has_quiz)) {
					const need = Number(lesson.quiz_pass_score) || 0;
					if (!state.quiz_runs || state.quiz_best < need) {
						reasons.push(__("Pass the quiz to finish this lesson."));
					}
				}
				return Promise.resolve({
					ok: reasons.length === 0,
					reasons,
					next_lesson_key: null,
					status: "In Progress",
				});
			},

			// No signed URL is available to an author: get_media_url is gated on a
			// learner attempt, and the builder bootstrap carries no URL for a
			// Training Video Asset. So the preview plays whatever it can reach —
			// a file just uploaded in this session, or the block's attachment.
			// Flagged in the handover: an author-side media endpoint is missing.
			mediaUrl: (args) => {
				const block = (lesson.blocks || []).find((row) => row.block_key === args.block_key);
				if (!block) return Promise.resolve("");
				return Promise.resolve(
					this.media_for(block, block.file || block.image || block.embed_url || "")
				);
			},
		};
	}

	preview_checkpoint(cp) {
		// Answer-free, exactly as `_checkpoint_payload` serves it. The author can
		// see the answers in the inspector; the preview must still show them what
		// a learner sees, or "test this checkpoint" tests nothing.
		return {
			checkpoint_key: cp.checkpoint_key,
			block_key: cp.block_key,
			at_seconds: Number(cp.at_seconds) || 0,
			at: Number(cp.at_seconds) || 0,
			question_type: cp.question_type,
			question_text: cp.question_text,
			pause_video: Number(cp.pause_video) || 0,
			allow_skip: Number(cp.allow_skip) || 0,
			max_attempts: Number(cp.max_attempts) || 0,
			rewind_seconds_on_wrong: Number(cp.rewind_seconds_on_wrong) || 0,
			options: (cp.options || []).map((option) => ({
				option_key: option.option_key,
				text: option.option_text || "",
			})),
		};
	}

	seek_preview(seconds) {
		if (!this.preview_video || !this.preview_video.video) return;
		try {
			this.preview_video.video.currentTime = Math.max(0, seconds);
		} catch (e) {
			/* the element has no source yet */
		}
	}

	// Plays from a short run-up through the REAL TR.Video, so the overlay, the
	// pause, the rewind-on-wrong and the attempt counter are the learner's, not
	// a mock-up of them.
	test_checkpoint(lesson, cp) {
		const block = (lesson.blocks || []).find((row) => row.block_key === cp.block_key);
		if (!block) return;
		const from = Math.max(0, Number(cp.at_seconds) - TB_TEST_LEAD_SECONDS);

		this.teardown_preview();
		const $body = this.$canvas.find(".tb-pane-body").empty();
		const $shell = $('<div class="tb-preview-shell"></div>').appendTo($body);
		$('<div class="tb-preview-banner"></div>')
			.text(__("Testing the checkpoint at {0} — playing from {1}.", [tb_mmss(cp.at_seconds), tb_mmss(from)]))
			.appendTo($shell);
		$(`<button type="button" class="tb-icon-btn">${__("Back to the blocks")}</button>`)
			.on("click", () => this.teardown_preview(true))
			.appendTo($shell);
		const $root = $('<div class="tb-preview-root"></div>').appendTo($shell);

		this.load_player()
			.catch((error) => {
				$root.html(
					`<div class="tb-denied">${__("The learner player could not be loaded, so this checkpoint cannot be tested.")}<br>${frappe.utils.escape_html(String((error && error.message) || ""))}</div>`
				);
				throw error;
			})
			.then(() => {
			if (!window.TR || typeof window.TR.Video !== "function") {
				$root.html(`<div class="tb-denied">${__("The learner player did not load.")}</div>`);
				return;
			}
			const transport = this.preview_transport(lesson);
			// Only the checkpoint under test is offered, so the run-up cannot
			// stumble into an earlier pin and answer a different question.
			transport.openCheckpoint = () => Promise.resolve(this.preview_checkpoint(cp));
			const video = new window.TR.Video(
				$root[0],
				{
					attempt: "preview",
					lesson_key: lesson.lesson_key,
					block_key: block.block_key,
					duration: this.block_duration(block),
					poster: block.poster_image,
					title: block.heading,
					checkpoints_enabled: 1,
					min_coverage_percent: 0,
					settings: { heartbeat_interval_seconds: 10, max_playback_rate: 1.25 },
				},
				transport
			);
			this.preview_video = video;
			video.ready.then(() => {
				try {
					video.video.currentTime = from;
					const play = video.video.play();
					if (play && play.catch) play.catch(() => {});
				} catch (e) {
					/* nothing to play */
				}
			});
			})
			// Already reported in the pane above; marked handled so it is not also
			// an unhandled rejection.
			.catch(() => {});
	}

	// ----- chapters --------------------------------------------------------

	// Chapters group the outline. The server has always accepted them —
	// `save_draft_version` takes a `chapters` array, `_apply_chapters` replaces the
	// table in order and refuses to orphan a lesson, and Training Course Version
	// mints the keys on save — but nothing in the builder ever wrote
	// `this.dirty.chapters`, so an author could file a lesson under a chapter and
	// never create one. On a new course, whose first draft clones nothing, the list
	// was permanently empty and every lesson sat under "Unfiled".
	//
	// A dialog rather than inline rail editing: creating and renaming chapters is
	// occasional work, and putting rename affordances on every outline heading would
	// crowd the one surface an author uses constantly.

	manage_chapters() {
		if (!this.guard_editable()) return;

		// Worked on locally and only committed on Save, so Cancel really cancels.
		// `chapter_key` is carried through untouched — it is what every lesson
		// points at, and a regenerated one silently unfiles the lot.
		const rows = (this.chapters || []).map((row) => ({
			chapter_key: row.chapter_key,
			chapter_title: row.chapter_title || "",
			description: row.description || "",
		}));

		const dialog = new frappe.ui.Dialog({
			title: __("Chapters"),
			size: "large",
			fields: [{ fieldname: "chapters_area", fieldtype: "HTML" }],
			primary_action_label: __("Save"),
			primary_action: () => {
				const cleaned = rows.filter((row) => (row.chapter_title || "").trim());
				const orphans = this.chapters_in_use(cleaned);
				if (orphans.length) {
					// The server refuses this too, but it refuses on the next autosave —
					// several seconds later, with the dialog long closed and the message
					// attached to a save the author did not knowingly trigger.
					frappe.msgprint({
						title: __("Those chapters still have lessons in them"),
						indicator: "orange",
						message: __(
							"Move the lessons out of {0} first. A lesson whose chapter no longer exists vanishes from the outline, so this is refused rather than done quietly.",
							[orphans.join(", ")]
						),
					});
					return;
				}
				dialog.hide();
				this.chapters = cleaned;
				this.dirty.chapters = cleaned;
				this.schedule_save();
				this.render();
			},
		});

		const $wrap = $(dialog.get_field("chapters_area").$wrapper).empty();
		const paint = () => {
			$wrap.empty();
			if (!rows.length) {
				$('<div class="tb-empty"></div>')
					.text(__("No chapters yet. Lessons without one are shown under “Unfiled”."))
					.appendTo($wrap);
			}
			rows.forEach((row, index) => {
				const $row = $(`
					<div class="tb-chapter-edit">
						<input type="text" class="input-with-feedback form-control" data-act="title">
						<div class="tb-chapter-edit-actions">
							<button type="button" class="btn btn-xs" data-act="up" aria-label="${__("Move up")}">↑</button>
							<button type="button" class="btn btn-xs" data-act="down" aria-label="${__("Move down")}">↓</button>
							<button type="button" class="btn btn-xs" data-act="remove" aria-label="${__("Remove")}">✕</button>
						</div>
					</div>`).appendTo($wrap);
				// .val(), not a value attribute: a title with a quote in it would
				// otherwise truncate the markup.
				$row.find('[data-act="title"]')
					.val(row.chapter_title)
					.attr("placeholder", __("Chapter title"))
					.on("input", (event) => {
						rows[index].chapter_title = event.target.value;
					});
				// Buttons rather than drag alone. Reordering is the whole point of
				// this dialog and a drag-only control cannot be operated from a
				// keyboard — the same reason the lesson rail has key reordering.
				$row.find('[data-act="up"]').prop("disabled", index === 0).on("click", () => {
					rows.splice(index - 1, 0, rows.splice(index, 1)[0]);
					paint();
				});
				$row.find('[data-act="down"]').prop("disabled", index === rows.length - 1).on("click", () => {
					rows.splice(index + 1, 0, rows.splice(index, 1)[0]);
					paint();
				});
				$row.find('[data-act="remove"]').on("click", () => {
					rows.splice(index, 1);
					paint();
				});
			});
			$(`<button type="button" class="btn btn-default btn-sm">+ ${__("Add chapter")}</button>`)
				.on("click", () => {
					// No chapter_key: the server mints it and hands it back in the
					// save response, which apply_bootstrap adopts. Inventing one here
					// is how a key ends up disagreeing with the row it names.
					rows.push({ chapter_title: "", description: "" });
					paint();
					$wrap.find('[data-act="title"]').last().focus();
				})
				.appendTo($wrap);
		};
		paint();
		dialog.show();
	}

	chapters_in_use(kept) {

		const keys = new Set(kept.map((row) => row.chapter_key).filter(Boolean));
		const titles = {};
		(this.chapters || []).forEach((row) => (titles[row.chapter_key] = row.chapter_title));
		const missing = new Set();
		(this.lessons || []).forEach((lesson) => {
			const key = lesson.chapter_key;
			if (key && !keys.has(key)) missing.add(titles[key] || key);
		});
		return Array.from(missing);
	}

	// ----- version actions -------------------------------------------------

	new_draft_version() {
		if (!this.course) return;
		const dialog = new frappe.ui.Dialog({
			title: __("New Draft Version"),
			fields: [
				{
					fieldname: "change_type",
					fieldtype: "Select",
					label: __("Change type"),
					options: ["", TB_MINOR_EDIT, TB_MATERIAL_CHANGE].join("\n"),
					description: __(
						"A material change supersedes existing completions and raises retakes. It can be decided at publish time instead."
					),
				},
			],
			primary_action_label: __("Create"),
			primary_action: (values) => {
				dialog.hide();
				frappe
					.call({
						method: "erpnext_enhancements.api.training_author.create_draft_version",
						args: { course: this.course.name, change_type: values.change_type || null },
					})
					.then(() => this.reload());
			},
		});
		dialog.show();
	}

	submit_for_review() {
		// Not a bare `return`: from the ⋯ menu with no draft loaded this did
		// nothing at all -- no alert, no message, no state change -- which reads as
		// a broken button rather than as "there is nothing to send".
		if (!this.version) return this.guard_editable();
		this.save_then(__("Sending for review")).then(() =>
			frappe
				.call({
					method: "erpnext_enhancements.api.training_author.submit_for_review",
					args: { course_version: this.version.name },
				})
				.then(() => {
					frappe.show_alert({ message: __("Sent for review."), indicator: "green" });
					this.reload();
				})
		)
			.catch(() => {});
	}

	publish() {
		if (!this.version) return this.guard_editable();
		if (!this.can_publish) {
			frappe.msgprint(__("Only a Training Manager can publish a version."));
			return;
		}
		const dialog = new frappe.ui.Dialog({
			title: __("Publish this version"),
			fields: [
				{
					fieldname: "change_type",
					fieldtype: "Select",
					label: __("Change type"),
					reqd: 1,
					options: [TB_MINOR_EDIT, TB_MATERIAL_CHANGE].join("\n"),
					description: __(
						"Material Change supersedes every completion of the previous version and raises a retake for each. Minor Edit leaves them valid."
					),
				},
				{ fieldname: "release_notes", fieldtype: "Small Text", label: __("Release notes") },
			],
			primary_action_label: __("Publish"),
			primary_action: (values) => {
				dialog.hide();
				this.save_then(__("Publishing")).then(() =>
					frappe
						.call({
							method: "erpnext_enhancements.api.training_author.publish_version",
							args: {
								course_version: this.version.name,
								change_type: values.change_type,
								release_notes: values.release_notes || null,
							},
							freeze: true,
							freeze_message: __("Publishing…"),
						})
						.then((r) => {
							const result = (r && r.message) || {};
							frappe.show_alert({
								message: __("Published v{0}.", [result.version_number]),
								indicator: "green",
							});
							this.reload();
						})
				)
					.catch(() => {});
			},
		});
		dialog.show();
	}
}

// ----- small helpers -------------------------------------------------------

function tb_load_asset(url) {
	return new Promise((resolve, reject) => {
		const is_css = url.split("?")[0].endsWith(".css");
		const selector = is_css ? `link[data-tb-asset="${url}"]` : `script[data-tb-asset="${url}"]`;
		if (document.querySelector(selector)) return resolve();
		const el = document.createElement(is_css ? "link" : "script");
		el.setAttribute("data-tb-asset", url);
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

function tb_mmss(seconds) {
	const total = Math.max(0, Math.round(Number(seconds) || 0));
	const mins = Math.floor(total / 60);
	const secs = total % 60;
	return mins + ":" + (secs < 10 ? "0" : "") + secs;
}

// Question text is a Text Editor field, so it arrives as HTML. Anything shown
// in a list, an option label or a result line is plain text and goes in through
// textContent — so strip the markup rather than injecting it somewhere narrower
// than the sanitizer expects.
function tb_plain(html) {
	const node = document.createElement("div");
	node.innerHTML = String(html || "");
	return (node.textContent || "").trim();
}

function tb_shuffle(list) {
	for (let i = list.length - 1; i > 0; i--) {
		const j = Math.floor(Math.random() * (i + 1));
		const swap = list[i];
		list[i] = list[j];
		list[j] = swap;
	}
	return list;
}

// A progress line for uploads. frappe.show_progress is the desk's own, and a
// 300 MB video with no feedback is indistinguishable from a frozen page.
function tb_progress(title) {
	return {
		set: (ratio) => frappe.show_progress(title, Math.round(ratio * 100), 100, __("Uploading…")),
		done: () => frappe.hide_progress(),
	};
}
