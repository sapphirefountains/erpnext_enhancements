/*
 * The record tools (v1.686.0): the Desk form's sidebar, timeline, dashboard and toolbar, for a saved
 * record on the guided page and in the board's side panel. Nik, 2026-10-14: "We shouldn't have lost
 * any fidelity or information on any of the UI/UX changes."
 *
 * The editor both shells run (./editor.js) makes one RecordTools and mounts it with the comments
 * (mount_comments -> mount_tools). It draws into the `.ge-tools` section the shell draws, and puts an
 * "Actions" button beside the shell's own head button (.ge-band-actions on the guided page,
 * .fk-head-row in the side panel). A new record has none of it: the section stays hidden.
 *
 * What it shows, each by the code path the Desk form uses (frappe and ERPNext read on version-16):
 *
 *   Assigned to   docinfo.assignments; add with frappe.ui.form.AssignToDialog (the sidebar's own,
 *                 which needs no form), remove / done with frappe.desk.form.assign_to.remove / close
 *   Follow        frappe.desk.form.document_follow.update_follow (the form menu's Follow), where the
 *                 DocType tracks changes and the person gets document-follow emails
 *   Tags          frappe.desk.doctype.tag.tag.add_tag / remove_tag, suggestions from get_tags
 *   Shared with   docinfo.shared, read only; sharing is on the full form (frappe's Share is the form's)
 *   Attachments   docinfo.attachments; add with frappe.ui.FileUploader on the record ("Home/
 *                 Attachments", the DocType's make_attachments_public and max_attachments), by its
 *                 button or by dropping files anywhere on the page (the form's own
 *                 setup_file_drop), remove with frappe.desk.form.utils.remove_attach after
 *                 frappe's own question. Since v1.711.0 the guided page gives them a section of
 *                 their own under the steps (.ge-files) and a count in the head band
 *                 (.ge-files-chip), because they sat in the record tools, which a person may
 *                 collapse or place away from the form. Since v1.748.0 they are in the tools'
 *                 Attachments group as well, where the Desk sidebar and the board's side panel keep
 *                 them: one list, drawn into both by the one renderer (render_files), and shown by
 *                 the stylesheet only while the tools sit in the side column on a wide screen. The
 *                 side panel has no section and keeps them in the tools alone
 *   Activity      the timeline with "Show all activity" on: created, last edited, Versions (as
 *                 sentences, version_timeline_content_builder.js's rules), emails, the assignment,
 *                 attachment, info and workflow logs, milestones; ERPNext's CRM Activities (open
 *                 ToDos and Events, "done" as its update_status does); New Email
 *                 (frappe.views.CommunicationComposer), New Task and New Event
 *                 (frappe.views.InteractionComposer, set up as ERPNext's crm_activities.js does)
 *   People & places  where the form has the Contact / Address directory (contact_list_html /
 *                 address_list_html): the same lists through the same sync_contact endpoints, with the
 *                 same sources (global_enhancements/party_sources.js, which the form's widget calls
 *                 too), New contact / address (contacts_ux.create_for), Link existing, Import, Set
 *                 primary (the record's own field, saved through the editor), Unlink, and each
 *                 row's Account / Supplier line (party_sources.js account_entries, the Desk
 *                 table's column, v1.746.0)
 *   Connections   the dashboard's linked documents (meta.__dashboard, counts from
 *                 frappe.desk.notifications.get_open_count), each opening its list filtered to this
 *                 record
 *   Actions menu  the Create menu (form_kit/record_tools.js RECORD_TOOLS, frappe.model.open_mapped_doc),
 *                 Generate Contract, Open Drive Folder, Set Reminder (global_enhancements/
 *                 record_actions.js, which the form's buttons call too), New Email, Print, Customize,
 *                 and the full form for the rest (rename, duplicate, delete, merge)
 *
 * Everything is decided by meta, permissions and values, never by the DocType's name (the one
 * per-DocType list is RECORD_TOOLS). Every user string is written with .text() or escaped. Every
 * request is sf_kit's call, which settles on the promise. Every dialog joins the planner kit's
 * history guard through the Party component's open_dialog (./party.js track): Back closes the
 * dialog and nothing else, as the Party dialogs do; the Actions menu is the planner kit's own menu,
 * which is an overlay of the same guard.
 *
 * Browser only. The decisions are ../record_tools.js, pure, run under node by
 * tests/test_guided_record_tools.py.
 */

import * as rules from "../record_tools.js";
// Imported, not read off the editor's kit: a tab that loaded an older kit bundle before a deploy has
// a window.sf_form_kit without these.
import * as header from "../header.js";
import { party_sources, primary_address_field, primary_address_name, form_party, is_account_doctype, account_entries } from "../../global_enhancements/party_sources.js";
import * as record_actions from "../../global_enhancements/record_actions.js";

const API = "erpnext_enhancements.form_kit.api.";
const SYNC = "erpnext_enhancements.sync_contact.";
// The feed shows this many items until "Show all".
const FEED_FIRST = 15;
// How long the guided page waits for the planner kit (its menu) before giving up on the menu.
const KIT_WAIT_MS = 4000;

function can(fn, doctype) {
	try {
		return !!(frappe.model && typeof frappe.model[fn] === "function" && frappe.model[fn](doctype));
	} catch (e) {
		return false;
	}
}

function when_text(at) {
	if (!at) return "";
	try {
		const pretty = frappe.datetime.prettyDate(at);
		if (pretty) return String(pretty);
	} catch (e) {
		// Falls through to the plain date.
	}
	try {
		return frappe.datetime.str_to_user(at);
	} catch (e) {
		return String(at);
	}
}

function full_date(at) {
	try {
		return at ? frappe.datetime.str_to_user(at) : "";
	} catch (e) {
		return String(at || "");
	}
}

function shown_phone(value) {
	try {
		const display = window.erpnext_enhancements && erpnext_enhancements.normalize && erpnext_enhancements.normalize.phone_display;
		return display && value ? display(value) : value;
	} catch (e) {
		return value;
	}
}

export class RecordTools {
	constructor(editor) {
		this.ed = editor;
		this.seq = 0;
		this.info = null;
		this.people_loaded = false;
		this.connections_loaded = false;
	}

	// ------------------------------------------------------------------ mounting

	mount() {
		const ed = this.ed;
		this.seq += 1;
		// Which mount (record) a lazy list's answer belongs to; `seq` also moves on every reload.
		this.mount_seq = (this.mount_seq || 0) + 1;
		this.info = null;
		this.people_loaded = false;
		this.connections_loaded = false;
		this.show_all = false;
		this.widgets = null;
		this.$directory = null;
		this.$connections = null;
		this.groups = [];
		const $root = ed.$root || $();
		$root.find(".ge-tools-actions").remove();
		const $box = $root.find(".ge-tools");
		this.$box = $box;
		this.$dock = $root.find(".ge-files");
		this.$files_dock = null;
		this.$files = null;
		this.files_count = null;
		this.files_return = null;
		this.load_failed = false;
		this.dock_reset();
		if (!$box.length) {
			this.unbind_drop();
			return;
		}
		this.bind_drop($root);
		if (ed.is_new || !ed.name || !ed.doctype) {
			$box.empty().prop("hidden", true);
			return;
		}
		this.doctype = ed.doctype;
		this.name = ed.name;
		this.config = rules.tools_config(this.doctype);
		$box.empty().prop("hidden", false).attr("aria-label", __("About this record"));
		this.draw_actions_button();
		this.draw_frame();
		this.load();
	}

	current() {
		return this.ed.doctype === this.doctype && this.ed.name === this.name && !this.ed.is_new;
	}

	meta() {
		try {
			return frappe.get_meta(this.doctype) || {};
		} catch (e) {
			return {};
		}
	}

	perms() {
		return (this.info && this.info.permissions) || {};
	}

	title() {
		try {
			const model = this.ed.kit.summary_model(this.doctype, this.name, this.ed.values, this.ed.completeness);
			return model.title || this.name;
		} catch (e) {
			return this.name;
		}
	}

	is_me(user) {
		return !!user && frappe.session && user === frappe.session.user;
	}

	who(user, name) {
		return this.is_me(user) ? __("You") : name || user || __("Someone");
	}

	// A dialog joins the history guard (Back closes it): the Party component's own open_dialog.
	dialog(open) {
		const party = this.ed.party_entry && this.ed.party_entry();
		if (party && typeof party.open_dialog === "function") party.open_dialog(open);
		else open();
	}

	// Leave for another Desk page (print, a list, the form): what is typed saves first, and a refusal
	// keeps the person here with the reason on screen, as the page's own "Open form" does.
	leave(go) {
		this.ed
			.save_before_moving()
			.then(() => go())
			.catch(() => {});
	}

	open_form() {
		const ed = this.ed;
		const doctype = this.doctype;
		const name = this.name;
		this.leave(() => {
			if (typeof ed.open_in_form === "function") {
				ed.open_in_form();
				return;
			}
			// Through the guided default, so the form opens rather than bouncing back here.
			if (window.sf_guided && window.sf_guided.to_form) window.sf_guided.to_form(doctype, name);
			else frappe.set_route("Form", doctype, name);
		});
	}

	// ------------------------------------------------------------------ the frame

	draw_frame() {
		const $box = this.$box;
		$('<h2 class="ge-tools-title"></h2>').text(__("About this record")).appendTo($box);
		this.$people = $('<div class="ge-tools-meta"></div>').appendTo($box);
		this.sf().state.render(this.$people, "loading");
		const wide = !!(window.matchMedia && window.matchMedia("(min-width: 1024px)").matches) && this.ed.id_prefix !== "fkp";
		// The files: the guided page's section (v1.711.0) and the tools' group, both drawn from one list
		// (file_lists). The group starts open on the guided page, because the stylesheet shows it only
		// beside the form on a wide screen; in the side panel it opens as the activity does.
		this.$files_dock = this.$dock.length ? this.dock_files() : null;
		this.$files = this.group("attachments", __("Attachments"), !!this.$dock.length || wide);
		this.$activity = this.group("activity", __("Activity"), wide);
		const widgets = rules.directory_widgets((this.meta().fields || []).map((f) => f.fieldname));
		if (widgets.contacts || widgets.addresses) {
			this.widgets = widgets;
			this.$directory = this.group("people", __("People & places"), false);
			this.$directory.closest("details").on("toggle", (event) => {
				if (event.currentTarget.open && !this.people_loaded) this.load_people();
			});
		}
		this.groups = rules.connection_groups(this.meta().__dashboard, (doctype) => can("can_read", doctype));
		if (this.groups.length) {
			this.$connections = this.group("connections", __("Connections"), false);
			this.$connections.closest("details").on("toggle", (event) => {
				if (event.currentTarget.open && !this.connections_loaded) this.load_connections();
			});
		}
	}

	group(key, label, open) {
		const $details = $('<details class="ge-tools-group"></details>').attr("data-tool", key).appendTo(this.$box);
		if (open) $details.attr("open", "");
		const $summary = $('<summary class="ge-tools-summary"></summary>').appendTo($details);
		$('<span class="ge-tools-label"></span>').text(label).appendTo($summary);
		$('<span class="ge-tools-count"></span>').appendTo($summary);
		return $('<div class="ge-tools-body"></div>').appendTo($details);
	}

	count(key, text) {
		this.$box.find(`.ge-tools-group[data-tool="${key}"] .ge-tools-count`).text(text === "" || text === null ? "" : String(text));
	}

	// ------------------------------------------------------------------ the files section (v1.711.0)

	// Nothing of the files section or the band's chip belongs to a record that is not shown.
	dock_reset() {
		if (this.$dock) this.$dock.empty().prop("hidden", true);
		this.ed.$root.find(".ge-files-chip").prop("hidden", true).empty();
	}

	// The guided page's own place for the record's files: a section under the steps, open unless the
	// page says otherwise (focus mode), with the same body the tools' group has. Drawn here, in the
	// frame, so it exists while the files load.
	dock_files() {
		const open = typeof this.ed.files_open_default === "function" ? this.ed.files_open_default() : true;
		const $details = $('<details class="ge-files-group"></details>').appendTo(this.$dock.empty().prop("hidden", false));
		if (open) $details.attr("open", "");
		const $summary = $('<summary class="ge-files-summary"></summary>').appendTo($details);
		$('<h2 class="ge-files-title"></h2>').text(__("Attachments")).appendTo($summary);
		$('<span class="ge-files-count"></span>').appendTo($summary);
		return $('<div class="ge-files-body"></div>').appendTo($details);
	}

	// Every place the record's files are listed: the files section (the guided page only) and the
	// tools' group. Each is drawn from the same answer, at the same moment, so they never disagree.
	file_lists() {
		return [this.$files_dock, this.$files].filter(($body) => $body && $body.length);
	}

	// The count, in the files section and the band's chip.
	show_count(n) {
		this.files_count = n;
		this.$dock.find(".ge-files-count").text(n ? String(n) : "");
		const $chip = this.ed.$root.find(".ge-files-chip");
		if (!$chip.length || !this.$dock.length) return;
		// The accessible name always contains the visible word. With nothing to count and a right to add,
		// the chip is the call to attach (chip_action), and says so in both.
		const label = header.files_chip_label(n, this.can_add_file());
		const wording = {
			attach: [__("Attach a file"), __("Attach a file")],
			none: [__("No files"), __("No files attached")],
			one: [__("1 file"), __("1 file attached")],
			many: [__("{0} files", [n]), __("{0} files attached", [n])],
		}[label.kind];
		if (!wording) return;
		const [word, aria] = wording;
		$chip.empty().prop("hidden", false).attr("aria-label", aria);
		$('<span class="ge-files-chip-icon" aria-hidden="true"></span>').appendTo($chip);
		$('<span class="ge-files-chip-text"></span>').text(word).appendTo($chip);
	}

	// Whether another file may be attached: write, and the DocType's max_attachments not reached.
	can_add_file() {
		return rules.can_add_file({
			write: this.perms().write,
			max_attachments: this.meta().max_attachments,
			count: ((this.info && this.info.attachments) || []).length,
		});
	}

	// ------------------------------------------------------------------ dropping files on the page

	// The form accepts a file dropped anywhere on it (frappe's Form.setup_file_drop); the guided page
	// does the same, over the page it draws. A file dragged over a page that does not claim it is
	// opened by the browser, which would take the person off the record they were typing in, so every
	// file drag over the page is claimed, and answered (header.js files_drop). Where it does NOT work:
	// the Desk's own navbar and the page head sit outside the page's root, so a drop there is the
	// browser's; the landing and loading screens are unbound (unbind_drop, from reset); and frappe's
	// Text Editor takes a drop on itself (its own handler stops it), as it does on the Desk form.
	// Bound once per mount, under a namespace, because the page's root outlives the record.
	bind_drop($root) {
		const ns = ".gefiles";
		const is_files = (event) => {
			const dt = event.originalEvent && event.originalEvent.dataTransfer;
			return !!dt && Array.prototype.indexOf.call(dt.types || [], "Files") > -1;
		};
		this.unbind_drop();
		this.$drop_root = $root;
		const clear = () => {
			clearTimeout(this.drop_timer);
			$root.removeClass("is-dropping-files");
		};
		$root.on(`dragenter${ns} dragover${ns}`, (event) => {
			if (!is_files(event)) return;
			event.preventDefault();
			$root.addClass("is-dropping-files");
			// A drop that something inside the page swallows (the Text Editor stops it) fires nothing
			// here, so the outline ends when the drag stops sending dragover, not only on our drop.
			clearTimeout(this.drop_timer);
			this.drop_timer = setTimeout(clear, 250);
		});
		$root.on(`dragleave${ns}`, (event) => {
			// Leaving for a child fires too; only leaving the root ends the highlight.
			if (event.relatedTarget && $root[0].contains(event.relatedTarget)) return;
			clear();
		});
		$root.on(`dragend${ns}`, clear);
		$root.on(`drop${ns}`, (event) => {
			clear();
			const dt = event.originalEvent && event.originalEvent.dataTransfer;
			// Copied now: a browser empties the event's DataTransfer once the handler returns, and the
			// uploader may open later (the dialog waits for the history guard).
			const files = dt && dt.files ? Array.from(dt.files) : [];
			const verdict = header.files_drop({
				has_files: files.length > 0,
				is_new: !!this.ed.is_new,
				failed: !!this.load_failed,
				loaded: !!this.info && this.current(),
				can_add: this.can_add_file(),
			});
			if (verdict === "ignore") return;
			event.preventDefault();
			event.stopPropagation();
			const warn = (text) => this.sf().toast(text, { tone: "warning", timeout: 5000 });
			if (verdict === "attach") {
				// Focus goes back to what the person had (a field they were typing in) once the uploader
				// closes, without scrolling the page; nothing focused before, nothing moved after.
				const before = document.activeElement;
				this.files_return = before && before !== document.body ? { el: before, quiet: true } : null;
				this.attach(files);
			} else if (verdict === "save") warn(__("Please save before attaching."));
			else if (verdict === "failed") warn(__("This record's attachments could not be loaded, so files cannot be added yet. Reload the page to try again."));
			else if (verdict === "wait") warn(__("This record's files are still loading. Try again in a moment."));
			else warn(__("You cannot attach files to this record."));
		});
	}

	// The page left the record (the landing screen, a record loading): nothing here takes a drop.
	unbind_drop() {
		clearTimeout(this.drop_timer);
		if (this.$drop_root) this.$drop_root.off(".gefiles").removeClass("is-dropping-files");
		this.$drop_root = null;
	}

	// The band's files chip: with files it brings the section into view (the page's show_files); with
	// none yet and a right to add it opens the uploader, as it says; a new record says to save first.
	chip_action() {
		const action = header.chip_action({
			is_new: !!this.ed.is_new,
			count: this.files_count,
			can_add: this.can_add_file(),
		});
		if (action === "save") this.sf().toast(__("Please save before attaching."), { tone: "warning", timeout: 5000 });
		else if (action === "attach") {
			this.files_return = { el: this.ed.$root.find(".ge-files-chip")[0] };
			this.attach();
		} else if (typeof this.ed.show_files === "function") this.ed.show_files();
	}

	sf() {
		return this.ed.sf;
	}

	// ------------------------------------------------------------------ loading

	load() {
		const seq = this.seq;
		const doctype = this.doctype;
		const name = this.name;
		this.sf()
			.call(API + "record_info", { doctype, name }, { quiet: true, method: "GET" })
			.then((info) => {
				if (seq !== this.seq || !this.current()) return;
				this.info = info || {};
				this.render_meta();
				this.render_files();
				this.render_activity();
				if (this.people_loaded) this.load_people();
			})
			.catch((err) => {
				if (seq !== this.seq || !this.current()) return;
				const retry = () => {
					this.sf().state.render(this.$people, "loading");
					this.load();
				};
				this.sf().state.render(this.$people, "error", {
					title: __("Attachments, activity and assignments could not be loaded."),
					body: (err && err.message) || "",
					on_action: retry,
				});
				// Each list of files says so itself: an empty "Attachments" would read as no files, and a
				// drop is told the same instead of "still loading".
				this.load_failed = true;
				this.redraw_files(($body) => {
					if (this.$dock.length) $('<p class="ge-muted ge-tools-none"></p>').text(__("The attachments could not be loaded. Reload the page to try again.")).appendTo($body);
				});
				this.$activity.empty();
			});
	}

	reload() {
		if (!this.current()) return;
		this.seq += 1;
		this.load();
	}

	// ------------------------------------------------------------------ assigned to, follow, tags, shared

	render_meta() {
		const info = this.info;
		const perms = this.perms();
		const $box = this.$people.empty();

		// Assigned to
		const $assigned = $('<div class="ge-tools-row" role="group"></div>').attr("aria-label", __("Assigned to")).appendTo($box);
		$('<h3 class="ge-tools-head"></h3>').text(__("Assigned to")).appendTo($assigned);
		const $who = $('<ul class="ge-tools-chips"></ul>').appendTo($assigned);
		const assignments = info.assignments || [];
		if (!assignments.length) $('<li class="ge-muted ge-tools-none"></li>').text(__("Nobody")).appendTo($who);
		assignments.forEach((row) => {
			const $chip = $('<li class="ge-tools-chip"></li>').appendTo($who);
			$('<span class="ge-tools-chip-text"></span>').text(row.user_name || row.user).attr("title", row.text || "").appendTo($chip);
			if (this.is_me(row.user)) {
				this.chip_button($chip, "✓", __("Mark {0}'s assignment done", [row.user_name || row.user]), () =>
					this.assignment("close", row.user)
				);
			}
			if (this.is_me(row.user) || perms.write) {
				this.chip_button($chip, "×", __("Remove {0}", [row.user_name || row.user]), () => this.assignment("remove", row.user));
			}
		});
		$('<button type="button" class="sf-btn sf-btn--secondary ge-tools-add"></button>')
			.text(__("Assign"))
			.on("click", () => this.open_assign())
			.appendTo($assigned);

		// Follow
		const meta = this.meta();
		const follow_notify = !!(frappe.boot && frappe.boot.user && frappe.boot.user.document_follow_notify);
		if (meta.track_changes && follow_notify) {
			const $follow = $('<div class="ge-tools-row"></div>').appendTo($box);
			$('<button type="button" class="sf-btn sf-btn--secondary ge-tools-follow"></button>')
				.text(info.followed ? __("Unfollow") : __("Follow"))
				.attr("aria-pressed", info.followed ? "true" : "false")
				.on("click", (event) => this.toggle_follow($(event.currentTarget)))
				.appendTo($follow);
			$('<span class="ge-muted ge-tools-note"></span>')
				.text(info.followed ? __("You get an email when this changes.") : __("Follow to get an email when this changes."))
				.appendTo($follow);
		}

		// Tags
		if (!meta.issingle) {
			const $tags = $('<div class="ge-tools-row" role="group"></div>').attr("aria-label", __("Tags")).appendTo($box);
			$('<h3 class="ge-tools-head"></h3>').text(__("Tags")).appendTo($tags);
			const $list = $('<ul class="ge-tools-chips"></ul>').appendTo($tags);
			const tags = info.tags || [];
			if (!tags.length) $('<li class="ge-muted ge-tools-none"></li>').text(__("None")).appendTo($list);
			tags.forEach((tag) => {
				const $chip = $('<li class="ge-tools-chip"></li>').appendTo($list);
				$('<span class="ge-tools-chip-text"></span>').text(tag).appendTo($chip);
				if (perms.write) this.chip_button($chip, "×", __("Remove tag {0}", [tag]), () => this.remove_tag(tag));
			});
			if (perms.write) this.draw_tag_input($tags);
		}

		// Shared with
		const shared = (info.shared || []).filter((row) => row.user || row.everyone);
		if (shared.length || (perms.share && can("can_share", this.doctype))) {
			const $shared = $('<div class="ge-tools-row"></div>').appendTo($box);
			$('<h3 class="ge-tools-head"></h3>').text(__("Shared with")).appendTo($shared);
			const names = shared.map((row) => {
				const who = row.everyone ? __("Everyone") : row.user_name || row.user;
				return row.write ? __("{0} (can edit)", [who]) : who;
			});
			$('<p class="ge-tools-text"></p>').text(names.length ? names.join(", ") : __("Nobody")).appendTo($shared);
			if (perms.share && can("can_share", this.doctype)) {
				$('<button type="button" class="sf-btn sf-btn--quiet ge-tools-link"></button>')
					.text(__("Share on the full form"))
					.on("click", () => this.open_form())
					.appendTo($shared);
			}
		}

		// The block is drawn again after each change: focus goes back where the person was working.
		if (this.focus_after) {
			$box.find(this.focus_after).first().trigger("focus");
			this.focus_after = null;
		}
	}

	chip_button($chip, symbol, label, fn) {
		$('<button type="button" class="ge-tools-chip-btn"></button>')
			.text(symbol)
			.attr("aria-label", label)
			.attr("title", label)
			.on("click", (event) => {
				event.preventDefault();
				$(event.currentTarget).prop("disabled", true);
				fn();
			})
			.appendTo($chip);
	}

	// frappe's own Add to ToDo dialog (form/sidebar/assign_to.js), which needs no form: it posts
	// frappe.desk.form.assign_to.add with the doctype and name, and calls back with the new list.
	// One per record, kept and cleared on each open, as the sidebar keeps its own (assign_to.js add).
	open_assign() {
		const doctype = this.doctype;
		const name = this.name;
		this.dialog(() => {
			const kept = this.assign_to;
			const assign =
				kept && kept.doctype === doctype && kept.docname === name
					? kept
					: new frappe.ui.form.AssignToDialog({
							method: "frappe.desk.form.assign_to.add",
							doctype,
							docname: name,
							callback: () => this.reload(),
					  });
			if (kept && kept !== assign && kept.dialog && kept.dialog.$wrapper) kept.dialog.$wrapper.remove();
			this.assign_to = assign;
			assign.dialog.clear();
			assign.dialog.show();
			// The ask starts empty (WI-081): My Work's dialog is "What do you need?", and a ToDo whose
			// text is the record's own title -- what this used to fill in -- asks for nothing.
			return assign;
		});
	}

	assignment(action, user) {
		this.focus_after = ".ge-tools-add";
		this.sf()
			.call(`frappe.desk.form.assign_to.${action}`, { doctype: this.doctype, name: this.name, assign_to: user }, { quiet: true })
			.then(() => this.reload())
			.catch((err) => this.refused(err, __("The assignment was not changed.")));
	}

	toggle_follow($button) {
		const following = !(this.info && this.info.followed);
		$button.prop("disabled", true);
		this.sf()
			.call(
				"frappe.desk.form.document_follow.update_follow",
				{ doctype: this.doctype, doc_name: this.name, following: following ? 1 : 0 },
				{ quiet: true }
			)
			.then((result) => {
				if (this.info) this.info.followed = !!result;
				this.focus_after = ".ge-tools-follow";
				this.render_meta();
				this.sf().toast(result ? __("You follow this now.") : __("You no longer follow this."), { tone: "success", timeout: 3000 });
			})
			.catch((err) => {
				$button.prop("disabled", false);
				this.refused(err, __("Following was not changed."));
			});
	}

	draw_tag_input($row) {
		const id = `${this.ed.id_prefix || "ge"}-tools-tags`;
		const $form = $('<form class="ge-tools-tag-add"></form>').appendTo($row);
		const $input = $('<input type="text" class="form-control ge-tools-tag-input" autocomplete="off">')
			.attr({ placeholder: __("Add a tag"), "aria-label": __("Add a tag"), list: `${id}-list`, maxlength: 140 })
			.appendTo($form);
		const $list = $("<datalist></datalist>").attr("id", `${id}-list`).appendTo($form);
		$('<button type="submit" class="sf-btn sf-btn--secondary ge-tools-add"></button>').text(__("Add")).appendTo($form);
		let timer = null;
		$input.on("input", () => {
			clearTimeout(timer);
			const txt = rules.clean_tag($input.val());
			if (!txt) return;
			timer = setTimeout(() => {
				this.sf()
					.call("frappe.desk.doctype.tag.tag.get_tags", { doctype: this.doctype, txt }, { quiet: true, method: "GET" })
					.then((tags) => {
						$list.empty();
						(tags || []).slice(0, 20).forEach((tag) => $("<option></option>").attr("value", tag).appendTo($list));
					})
					.catch(() => {});
			}, 250);
		});
		$form.on("submit", (event) => {
			event.preventDefault();
			const tag = rules.clean_tag($input.val());
			if (!tag) return;
			$input.prop("disabled", true);
			this.sf()
				.call("frappe.desk.doctype.tag.tag.add_tag", { tag, dt: this.doctype, dn: this.name }, { quiet: true })
				.then(() => {
					if (this.info) this.info.tags = (this.info.tags || []).concat(tag).filter((t, i, all) => all.indexOf(t) === i);
					this.focus_after = ".ge-tools-tag-input";
					this.render_meta();
				})
				.catch((err) => {
					$input.prop("disabled", false);
					this.refused(err, __("The tag was not added."));
				});
		});
	}

	remove_tag(tag) {
		this.sf()
			.call("frappe.desk.doctype.tag.tag.remove_tag", { tag, dt: this.doctype, dn: this.name }, { quiet: true })
			.then(() => {
				if (this.info) this.info.tags = (this.info.tags || []).filter((t) => t.toLowerCase() !== tag.toLowerCase());
				this.focus_after = ".ge-tools-tag-input";
				this.render_meta();
			})
			.catch((err) => this.refused(err, __("The tag was not removed.")));
	}

	refused(err, fallback) {
		this.sf().toast((err && err.message) || fallback, { tone: "warning", timeout: 6000 });
		this.reload();
	}

	// ------------------------------------------------------------------ attachments

	// One answer for every list (file_lists): the files, whether one may be added and whether one may
	// be removed are decided once, so the counts, the chip and "Attach a file" agree wherever they show.
	render_files() {
		const info = this.info;
		const meta = this.meta();
		const perms = this.perms();
		const all = info.attachments || [];
		const files = rules.unique_files(all);
		this.count("attachments", files.length || "");
		this.show_count(files.length);
		const can_add = this.can_add_file();
		const can_remove = rules.can_remove_files({
			protect_attached_files: meta.protect_attached_files,
			docstatus: info.docstatus,
			write: perms.write,
			delete: perms.delete,
		});
		this.redraw_files(($body) => this.draw_files($body, files, can_add, can_remove));
	}

	// Every list emptied and drawn again. A control in a list that had focus goes with the old list (an
	// upload finishing, a remove, a realtime reload) and focus falls to the page, so which list held it
	// is noted first and refocus_files puts it back there.
	redraw_files(draw) {
		const active = document.activeElement;
		const $was = this.file_lists().find(($body) => !!active && $body[0].contains(active));
		const was = $was ? { list: $was[0], to: $(active).hasClass("ge-tools-add") ? "attach" : "summary" } : null;
		this.file_lists().forEach(($body) => draw($body.empty()));
		this.refocus_files(was);
	}

	// Focus back where the person was working, once it has fallen to the page: frappe returns it after a
	// dialog only on a Form route, and the guided page is not one. In order: what they used (the button,
	// the chip, the field they had before a drop) while it is still there; else that list's "Attach a
	// file", if that is what they used and it is still offered; else that list's summary, or the first
	// list on screen. Nothing moves while focus is on anything else on screen: a person who has moved
	// on keeps it. While a dialog that is still open holds it (the uploader on its last file), this
	// waits for it to close. A control inside a dialog already hidden counts as lost: the browser may
	// still report it as focused when bootstrap's hidden event fires.
	refocus_files(was) {
		const want = this.files_return || was;
		this.files_return = null;
		if (!want) return;
		const shown = (el) => !!el && document.contains(el) && el.getClientRects().length > 0;
		const active = document.activeElement;
		if (active && active !== document.body && shown(active)) {
			const $modal = $(active).closest(".modal");
			if ($modal.length) {
				this.files_return = want;
				$modal.one("hidden.bs.modal", () => this.refocus_files());
			}
			return;
		}
		const summary = (list) => $(list).closest("details").children("summary")[0];
		let target = shown(want.el) ? want.el : null;
		if (!target) {
			const lists = this.file_lists().map(($body) => $body[0]);
			const list = lists.includes(want.list) && shown(summary(want.list)) ? want.list : lists.find((l) => shown(summary(l)));
			const attach = list && want.to === "attach" ? $(list).find(".ge-tools-add")[0] : null;
			target = shown(attach) ? attach : list ? summary(list) : null;
		}
		if (target) target.focus({ preventScroll: !!want.quiet });
	}

	// frappe's dialog (the uploader, the question before a remove): when it closes, refocus_files.
	on_closed(dialog) {
		const $wrapper = dialog && dialog.$wrapper;
		if ($wrapper && typeof $wrapper.one === "function") $wrapper.one("hidden.bs.modal", () => this.refocus_files());
	}

	// One list of the record's files, with Attach a file and each file's Remove.
	draw_files($body, files, can_add, can_remove) {
		const meta = this.meta();
		const perms = this.perms();
		const list = $body[0];
		if (can_add) {
			$('<button type="button" class="sf-btn sf-btn--secondary ge-tools-add"></button>')
				.text(__("Attach a file"))
				.on("click", (event) => {
					this.files_return = { el: event.currentTarget, list, to: "attach" };
					this.attach();
				})
				.appendTo($body);
		} else if (perms.write && meta.max_attachments) {
			$('<p class="ge-muted ge-tools-note"></p>')
				.text(__("The most files this record can hold ({0}) are attached.", [meta.max_attachments]))
				.appendTo($body);
		}
		if (!files.length) {
			const dropping = this.$dock.length && can_add;
			$('<p class="ge-muted ge-tools-none"></p>')
				.text(dropping ? __("No files attached. Drop files anywhere on this page, or attach one.") : __("No files attached."))
				.appendTo($body);
			return;
		}
		const $list = $('<ul class="ge-tools-list ge-tools-files"></ul>').appendTo($body);
		files.forEach((file) => {
			const $item = $('<li class="ge-tools-item"></li>').appendTo($list);
			const $main = $('<div class="ge-tools-item-main"></div>').appendTo($item);
			$('<a class="ge-tools-file" target="_blank" rel="noopener noreferrer"></a>')
				.attr("href", rules.file_url(file))
				.text(file.file_name || file.file_url || file.name)
				.appendTo($main);
			const details = [
				rules.kind_text(file),
				rules.size_text(file.file_size),
				file.is_private ? __("Private") : "",
				file.owner_name,
				when_text(file.creation),
			].filter(Boolean);
			$('<div class="ge-tools-item-meta"></div>').text(details.join(" · ")).attr("title", full_date(file.creation)).appendTo($main);
			if (can_remove) {
				$('<button type="button" class="sf-btn sf-btn--quiet ge-tools-item-btn"></button>')
					.text(__("Remove"))
					.attr("aria-label", __("Remove {0}", [file.file_name || file.name]))
					.on("click", (event) => {
						this.files_return = { el: event.currentTarget, list, to: "summary" };
						this.remove_file(file);
					})
					.appendTo($item);
			}
		});
	}

	// frappe's sidebar new_attachment, without a form: the record, "Home/Attachments", the DocType's
	// public default, and what is left of max_attachments.
	attach(dropped) {
		const meta = this.meta();
		const doctype = this.doctype;
		const name = this.name;
		const count = ((this.info && this.info.attachments) || []).length;
		let timer = null;
		const uploaded = () => {
			clearTimeout(timer);
			timer = setTimeout(() => this.reload(), 300);
		};
		const make = () => {
			const restrictions = {};
			if (meta.max_attachments) restrictions.max_number_of_files = meta.max_attachments - count;
			const uploader = new frappe.ui.FileUploader({
				doctype,
				docname: name,
				folder: "Home/Attachments",
				make_attachments_public: meta.make_attachments_public,
				on_success: uploaded,
				restrictions,
				// Files dropped on the page, as the form's drop passes them.
				files: dropped && dropped.length ? dropped : undefined,
			});
			this.on_closed(uploader && uploader.dialog);
			return uploader;
		};
		this.dialog(() => {
			if (frappe.ui.FileUploader) return make();
			return new Promise((resolve) => frappe.require("file_uploader.bundle.js", () => resolve(make())));
		});
	}

	// frappe's question, then every row of that file name (the sidebar removes them together).
	remove_file(file) {
		const rows = ((this.info && this.info.attachments) || []).filter((row) => row.file_name === file.file_name);
		const targets = rows.length ? rows : [file];
		this.dialog(() => {
			const asked = frappe.confirm(__("Are you sure you want to delete the attachment?"), () => {
				Promise.all(
					targets.map((row) =>
						this.sf().call(
							"frappe.desk.form.utils.remove_attach",
							{ fid: row.name, dt: this.doctype, dn: this.name },
							{ quiet: true }
						)
					)
				)
					.then(() => {
						this.sf().toast(__("Attachment removed."), { tone: "success", timeout: 3000 });
						this.reload();
					})
					.catch((err) => this.refused(err, __("The attachment was not removed.")));
			});
			this.on_closed(asked);
			return asked;
		});
	}

	// ------------------------------------------------------------------ activity

	render_activity() {
		const info = this.info;
		const perms = this.perms();
		const $body = this.$activity.empty();
		const tasks = (info.open && info.open.tasks) || [];
		const events = (info.open && info.open.events) || [];

		const $actions = $('<div class="ge-tools-buttons"></div>').appendTo($body);
		if (perms.email) {
			$('<button type="button" class="sf-btn sf-btn--secondary"></button>')
				.text(__("New email"))
				.on("click", () => this.new_email())
				.appendTo($actions);
		}
		if (can("can_create", "ToDo")) {
			$('<button type="button" class="sf-btn sf-btn--secondary"></button>')
				.text(__("New task"))
				.on("click", () => this.new_interaction("ToDo"))
				.appendTo($actions);
		}
		if (can("can_create", "Event")) {
			$('<button type="button" class="sf-btn sf-btn--secondary"></button>')
				.text(__("New event"))
				.on("click", () => this.new_interaction("Event"))
				.appendTo($actions);
		}
		if (!$actions.children().length) $actions.remove();

		if (tasks.length || events.length) {
			$('<h3 class="ge-tools-head"></h3>').text(__("Open tasks and events")).appendTo($body);
			const $open = $('<ul class="ge-tools-list ge-tools-open"></ul>').appendTo($body);
			tasks.forEach((task) => {
				const $item = $('<li class="ge-tools-item"></li>').appendTo($open);
				const $main = $('<div class="ge-tools-item-main"></div>').appendTo($item);
				$('<a class="ge-tools-item-title"></a>')
					.attr("href", frappe.utils.get_form_link("ToDo", task.name))
					.text(task.text || __("Task"))
					.appendTo($main);
				$('<div class="ge-tools-item-meta"></div>')
					.text([__("Task"), task.allocated_name, task.date ? full_date(task.date) : ""].filter(Boolean).join(" · "))
					.appendTo($main);
				this.done_button($item, "ToDo", task.name, task.text);
			});
			events.forEach((event) => {
				const $item = $('<li class="ge-tools-item"></li>').appendTo($open);
				const $main = $('<div class="ge-tools-item-main"></div>').appendTo($item);
				$('<a class="ge-tools-item-title"></a>')
					.attr("href", frappe.utils.get_form_link("Event", event.name))
					.text(event.subject || __("Event"))
					.appendTo($main);
				$('<div class="ge-tools-item-meta"></div>')
					.text([__("Event"), __(event.category || ""), full_date(event.starts_on)].filter(Boolean).join(" · "))
					.appendTo($main);
				this.done_button($item, "Event", event.name, event.subject);
			});
		}

		const feed = rules.activity_feed(info, (data) => rules.describe_version(data, (f) => this.field_label(f), (t, f) => this.child_label(t, f)));
		this.count("activity", tasks.length + events.length ? __("{0} open", [tasks.length + events.length]) : "");
		$('<h3 class="ge-tools-head"></h3>').text(__("History")).appendTo($body);
		if (info.document_email) {
			$('<p class="ge-muted ge-tools-note"></p>')
				.text(__("Add to this activity by mailing to {0}", [info.document_email]))
				.appendTo($body);
		}
		const shown = this.show_all ? feed : feed.slice(0, FEED_FIRST);
		const $list = $('<ol class="ge-tools-list ge-tools-feed"></ol>').appendTo($body);
		shown.forEach((item) => this.feed_item(item).appendTo($list));
		if (feed.length > shown.length) {
			$('<button type="button" class="sf-btn sf-btn--quiet ge-tools-more"></button>')
				.text(__("Show all {0}", [feed.length]))
				.on("click", () => {
					this.show_all = true;
					this.render_activity();
				})
				.appendTo($body);
		}
		if ((info.communications || []).length >= 20) {
			$('<p class="ge-muted ge-tools-note"></p>').text(__("Older emails are on the full form.")).appendTo($body);
		}
	}

	// ERPNext's crm_activities.js update_status: a ToDo or Event set Closed.
	done_button($item, doctype, name, label) {
		if (!can("can_write", doctype)) return;
		$('<button type="button" class="sf-btn sf-btn--quiet ge-tools-item-btn"></button>')
			.text(__("Done"))
			.attr("aria-label", __("Mark {0} done", [label || name]))
			.on("click", (event) => {
				$(event.currentTarget).prop("disabled", true);
				this.sf()
					.call("frappe.client.set_value", { doctype, name, fieldname: "status", value: "Closed" }, { quiet: true })
					.then(() => this.reload())
					.catch((err) => this.refused(err, __("It was not marked done.")));
			})
			.appendTo($item);
	}

	// The timeline's display rule for one field (version_timeline_content_builder.js): its label, or
	// null when it is hidden (and not shown on the timeline) or at a permlevel the person cannot read.
	field_label(fieldname) {
		return this.label_of(this.doctype, fieldname);
	}

	child_label(table_fieldname, fieldname) {
		let table;
		try {
			table = frappe.meta.get_docfield(this.doctype, table_fieldname, this.name);
		} catch (e) {
			table = null;
		}
		if (!table || !table.options) return null;
		return this.label_of(table.options, fieldname);
	}

	label_of(doctype, fieldname) {
		let df;
		try {
			df = frappe.meta.get_docfield(doctype, fieldname, this.name);
		} catch (e) {
			df = null;
		}
		if (!df || (df.hidden && !df.show_on_timeline)) return null;
		let status = "None";
		try {
			const levels = (frappe.perm.get_perm(this.doctype) || []).slice();
			levels[0] = Object.assign({}, levels[0] || {}, this.perms());
			status = frappe.perm.get_field_display_status(df, null, levels);
		} catch (e) {
			// Unknown: only what the flow itself showed this person.
			status = this.ed.defs && this.ed.defs[fieldname] ? "Read" : "None";
		}
		if (status === "Read" || status === "Write" || (df.hidden && df.show_on_timeline)) {
			return __(df.label || fieldname, null, df.parent);
		}
		return null;
	}

	feed_item(item) {
		const $li = $('<li class="ge-tools-event"></li>').attr("data-kind", item.kind);
		const who = this.who(item.by, item.by_name);
		const $text = $('<div class="ge-tools-event-text"></div>').appendTo($li);
		if (item.kind === "communication") {
			this.communication_card($li, $text, item.communication);
		} else {
			$text.text(this.sentence(item, who));
		}
		$('<div class="ge-tools-item-meta"></div>').text(when_text(item.at)).attr("title", full_date(item.at)).appendTo($li);
		return $li;
	}

	sentence(item, who) {
		if (item.kind === "created") return __("{0} created this", [who]);
		if (item.kind === "edited") return __("{0} last edited this", [who]);
		if (item.kind === "milestone") {
			const m = item.milestone || {};
			const label = this.field_label(m.track_field) || m.track_field;
			return __("{0} changed {1} to {2}", [who, label, m.value || '""']);
		}
		if (item.kind === "log") {
			const log = item.log || {};
			if (log.kind === "assignment") return log.text;
			if (log.kind === "attachment") {
				return log.comment_type === "Attachment Removed"
					? __("{0} removed attachment {1}", [who, log.text])
					: __("{0} attached {1}", [who, log.text]);
			}
			return `${who} ${log.text}`;
		}
		const change = item.change || {};
		if (change.kind === "comment") return `${who}: ${change.text}`;
		if (change.kind === "submitted") return __("{0} submitted this document", [who]);
		if (change.kind === "cancelled") return __("{0} cancelled this document", [who]);
		if (change.kind === "changed") {
			const parts = change.parts.map((p) => __("{0} from {1} to {2}", [p.label, p.from, p.to])).join(", ");
			return __("{0} changed the value of {1}", [who, parts]);
		}
		if (change.kind === "rows_changed") {
			const parts = change.parts.map((p) => __("{0} from {1} to {2} in row #{3}", [p.label, p.from, p.to, p.row])).join(", ");
			return __("{0} changed the values for {1}", [who, parts]);
		}
		if (change.kind === "added" || change.kind === "removed") {
			const parts = change.tables
				.map((t) => (t.count === 1 ? __("1 row to {0}", [t.label]) : __("{0} rows to {1}", [t.count, t.label])))
				.join(", ");
			return change.kind === "added" ? __("{0} added {1}", [who, parts]) : __("{0} removed {1}", [who, parts]);
		}
		return "";
	}

	// An email or other communication, as text: the body is plain text from the server (the
	// Communication form has it as sent).
	communication_card($li, $text, c) {
		const comm = c || {};
		$li.addClass("ge-tools-event--card");
		$('<a class="ge-tools-item-title"></a>')
			.attr("href", frappe.utils.get_form_link("Communication", comm.name))
			.text(comm.subject || __("(no subject)"))
			.appendTo($text);
		const route = [comm.sender_name || comm.sender, comm.recipients ? __("to {0}", [comm.recipients]) : ""].filter(Boolean).join(" ");
		const details = [__(comm.medium || comm.type || ""), route, comm.status ? __(comm.status) : "", comm.attachments ? __("{0} attachment(s)", [comm.attachments]) : ""]
			.filter(Boolean)
			.join(" · ");
		$('<div class="ge-tools-item-meta"></div>').text(details).appendTo($text);
		if (comm.text) $('<div class="ge-tools-email-text"></div>').text(comm.text).appendTo($text);
		if (this.perms().email && comm.medium === "Email" && comm.type === "Communication") {
			$('<button type="button" class="sf-btn sf-btn--quiet ge-tools-reply"></button>')
				.text(__("Reply"))
				.on("click", () => this.new_email(comm))
				.appendTo($text);
		}
	}

	// This record's files, as the composers list them (each its own copy: InteractionComposer's
	// render_attach rewrites file_url on the objects it is given).
	record_files() {
		return rules.unique_files((this.info && this.info.attachments) || []).map((f) => ({
			name: f.name,
			file_name: f.file_name,
			file_url: f.file_url,
		}));
	}

	// frappe's CommunicationComposer on the record, with the subject and recipient the form's own
	// New Email starts with (form_timeline.js compose_mail; the composer's subject rule), and the
	// record's files to pick from (the composer's `attachments`). A reply starts from the email it
	// answers, as the timeline's Reply does.
	//
	// No `frm` is passed, on purpose: the composer reads a form's timeline, events, meta, print formats,
	// letter head, get_files, attachments, perm and reload_doc, and announces "form-typing" for it, so a
	// stand-in would have to be a form. Without one it leaves out "Attach Document Print" and keeps no
	// draft in local storage; the full form's New Email has both.
	//
	// The address comes from the record's email fields as record_info read them (`emails`): the
	// form's email field (Opportunity: contact_email) is hidden, so it is in no step and never in the
	// editor's values. The subject follows the Desk's setup_subject_and_recipients (rules.email_start).
	new_email(reply) {
		const values = this.email_values();
		const doc = Object.assign({}, this.ed.values || {}, { doctype: this.doctype, name: this.name });
		const start = rules.email_start(this.config, values, (this.info && this.info.communications) || [], this.title(), this.name);
		const opts = {
			doc,
			subject: start.subject,
			recipients: start.recipients,
			attachments: this.record_files(),
			success: () => this.reload(),
		};
		if (!reply) {
			this.dialog(() => new frappe.views.CommunicationComposer(opts));
			return;
		}
		const me = frappe.session && frappe.session.user_email;
		Object.assign(opts, {
			title: __("Reply"),
			is_a_reply: true,
			recipients: reply.sender && reply.sender !== me ? reply.sender : rules.recipient_for(this.config, values),
			subject: reply.subject,
		});
		// The quote is the email as sent, as the timeline's Reply quotes it: its full content from the
		// timeline's own read (frappe.desk.form.load.get_communications, which checks read on this
		// record), scripts and styles taken out as the timeline does. record_info carries only a cut
		// text copy; it is the quote only if that read fails.
		//
		// That read is a round trip, and the board's side panel switches records in place: if the
		// editor is on another record when it answers, this reply never opens over that one (v1.688.1).
		// The composer is asked again as it is made, because the dialog waits too: on the guided page
		// the first one loads the history guard (party.js open_dialog), up to four seconds. The record
		// it was asked from is compared, not the mount: a reload of the same record (a realtime refresh
		// re-mounts it) still opens the reply the person asked for.
		const doctype = this.doctype;
		const name = this.name;
		const asked_here = () => this.ed.doctype === doctype && this.ed.name === name;
		const open = (content) => {
			opts.last_email = {
				name: reply.name,
				subject: reply.subject,
				sender: reply.sender,
				recipients: reply.recipients,
				cc: reply.cc,
				communication_date: reply.at,
				content,
			};
			this.dialog(() => (asked_here() ? new frappe.views.CommunicationComposer(opts) : null));
		};
		const cut = frappe.utils.escape_html(reply.text || "").replace(/\n/g, "<br>");
		this.sf()
			.call(
				"frappe.desk.form.load.get_communications",
				{ doctype, name, start: 0, limit: 100 },
				{ quiet: true, method: "GET" }
			)
			.then((rows) => {
				if (!asked_here()) return;
				const found = (rows || []).find((row) => row && row.name === reply.name);
				const content = found && typeof found.content === "string" ? frappe.dom.remove_script_and_style(found.content) : null;
				open(content || cut);
			})
			.catch(() => {
				if (asked_here()) open(cut);
			});
	}

	// The record's values with the email fields record_info read filled in where the values have none.
	email_values() {
		return rules.with_emails(this.ed.values, this.info && this.info.emails);
	}

	// New Task / New Event exactly as ERPNext's CRM Activities makes them (crm_activities.js
	// create_task / create_event): frappe's InteractionComposer on the record, with the Reference
	// fixed. Its `frm` is a stand-in carrying only what the composer reads (doc, doctype, docname, and
	// attachments.attachment_uploaded for a file added in the dialog).
	//
	// The composer also reads the GLOBAL cur_frm: render_attach lists cur_frm.get_files(), and its
	// insert callback runs cur_frm.reload_doc(). Both shells are Desk Pages, and v16's pageview.js sets
	// cur_frm to null whenever a Page is shown, so normally it is null here and this record's files come
	// in through the composer's own `attachments`. A defence for the rare case it is not: if cur_frm is
	// another record's form, it is null while this composer is open and until its insert has called
	// back, then put back, but only if nothing has set it meanwhile and the route has not changed (a
	// stale form is never put back on a Page). If it is this very record's form, its files are listed
	// from it, so `attachments` is not passed as well (they would list twice).
	new_interaction(kind) {
		const doctype = this.doctype;
		const name = this.name;
		let composer = null;
		const frm = {
			doc: { doctype, name },
			doctype,
			docname: name,
			attachments: {
				attachment_uploaded: (file) => {
					if (composer && file && file.name) {
						composer.attachments.push({ name: file.name, file_name: file.file_name, file_url: file.file_url });
					}
					this.reload();
				},
			},
		};
		const route_str = () => {
			try {
				return frappe.get_route_str ? frappe.get_route_str() : String(frappe.get_route() || "");
			} catch (e) {
				return "";
			}
		};
		const saved = window.cur_frm || null;
		const foreign = !!saved && !(saved.doctype === doctype && saved.docname === name);
		const own_form = !!saved && !foreign;
		const route = route_str();
		let creating = false;
		let restored = !foreign;
		const restore = () => {
			if (restored) return;
			restored = true;
			// A tick later: a route change closes the dialog before the router has the new route.
			setTimeout(() => {
				if (window.cur_frm === null && route_str() === route) window.cur_frm = saved;
			}, 0);
		};
		this.dialog(() => {
			if (foreign) window.cur_frm = null;
			try {
				composer = new frappe.views.InteractionComposer({
					doc: frm.doc,
					frm,
					attachments: own_form ? [] : this.record_files(),
					title: kind === "ToDo" ? __("New Task") : __("New Event"),
				});
			} catch (e) {
				restore();
				throw e;
			}
			// Closed without creating: cur_frm comes back now. Closed by its own create: after the insert.
			composer.dialog.$wrapper.on("hidden.bs.modal", () => {
				if (!creating) restore();
			});
			composer.dialog.get_field("interaction_type").set_value(kind);
			if (kind === "ToDo") {
				// hide column having interaction type field
				$(composer.dialog.get_field("interaction_type").wrapper).closest(".form-column").hide();
				// hide summary field
				$(composer.dialog.get_field("summary").wrapper).closest(".form-section").hide();
			} else {
				$(composer.dialog.get_field("interaction_type").wrapper).hide();
			}
			// The composer reloads the form it was given; there is none here, so its create is followed
			// by a reload of the activity instead.
			const create = composer.create_interaction.bind(composer);
			composer.create_interaction = (...args) => {
				creating = true;
				let pending;
				try {
					pending = create(...args);
				} catch (e) {
					restore();
					throw e;
				}
				Promise.resolve(pending)
					.then(() => this.reload())
					.catch(() => {})
					// frappe.call runs its callback (the composer's reload_doc) before its promise settles.
					.finally(() => setTimeout(restore, 0));
				return pending;
			};
			return composer;
		});
	}

	// ------------------------------------------------------------------ people & places

	sources() {
		const doc = Object.assign({}, this.ed.values || {}, { name: this.name });
		return party_sources(this.doctype, doc, this.meta().fields || []);
	}

	load_people() {
		this.people_loaded = true;
		// Its own count: a reload of the rest (seq) must not strand this list on "loading".
		const seq = (this.people_seq = (this.people_seq || 0) + 1);
		const record = this.mount_seq;
		const $body = this.$directory;
		if (!$body) return;
		const sources = this.sources();
		this.sf().state.render($body, "loading");
		const args = { sources, context_doctype: this.doctype, context_name: this.name };
		const ask = (method, on) =>
			on ? this.sf().call(SYNC + method, args, { quiet: true, method: "GET" }) : Promise.resolve(null);
		Promise.all([ask("get_contacts_for_context", this.widgets.contacts), ask("get_addresses_for_context", this.widgets.addresses)])
			.then(([contacts, addresses]) => {
				if (seq !== this.people_seq || record !== this.mount_seq || !this.current()) return;
				this.render_people(contacts, addresses, sources);
			})
			.catch((err) => {
				if (seq !== this.people_seq || record !== this.mount_seq || !this.current()) return;
				this.sf().state.render($body, "error", {
					title: __("Contacts and addresses could not be loaded."),
					body: (err && err.message) || "",
					on_action: () => this.load_people(),
				});
			});
	}

	// The record's own field for its primary, when the person may change it here (the flow offers it).
	editable_field(fieldname) {
		if (!fieldname || !this.ed.defs || !this.ed.defs[fieldname] || !this.perms().write) return false;
		try {
			return this.ed.party_entry().editable().has(fieldname);
		} catch (e) {
			return false;
		}
	}

	render_people(contacts, addresses, sources) {
		const $body = this.$directory.empty();
		const perms = this.perms();
		const on_account = is_account_doctype(this.doctype);
		const related = sources.filter((s) => !(s.doctype === this.doctype && s.name === this.name));
		let total = 0;
		if (this.widgets.contacts) {
			const list = contacts || [];
			total += list.length;
			const $section = $('<section class="ge-tools-people"></section>').appendTo($body);
			$('<h3 class="ge-tools-head"></h3>').text(__("Contacts ({0})", [list.length])).appendTo($section);
			const $buttons = $('<div class="ge-tools-buttons"></div>').appendTo($section);
			if (perms.write && can("can_create", "Contact")) this.button($buttons, __("New contact"), () => this.create_party("Contact"));
			if (perms.write) this.button($buttons, __("Link existing"), () => this.link_existing("Contact"));
			if (perms.write && related.length) this.button($buttons, __("Import contacts"), () => this.import_contacts(sources));
			const primary = (this.ed.values || {}).primary_contact || null;
			const can_set = this.editable_field("primary_contact");
			if (!list.length) $('<p class="ge-muted ge-tools-none"></p>').text(__("No contacts linked to any related parties yet.")).appendTo($section);
			const $list = $('<ul class="ge-tools-list"></ul>').appendTo($section);
			list.forEach((c) => {
				const is_primary = c.name === primary || (on_account && !!c.is_primary_contact);
				const account_primary = !is_primary && !on_account && !!c.is_primary_contact;
				const full_name = [c.first_name, c.last_name].filter(Boolean).join(" ") || c.name;
				const phone = c.custom_phone_number || c.custom_mobile_number || "";
				const $item = this.person_item($list, full_name, frappe.utils.get_form_link("Contact", c.name), is_primary, account_primary);
				const $meta = $item.find(".ge-tools-item-main");
				if (c.custom_title) $('<div class="ge-tools-item-meta"></div>').text(c.custom_title).appendTo($meta);
				const $reach = $('<div class="ge-tools-reach"></div>').appendTo($meta);
				if (c.custom_email) $("<a></a>").attr("href", `mailto:${c.custom_email}`).text(c.custom_email).appendTo($reach);
				if (phone) $("<a></a>").attr("href", `tel:${phone}`).text(shown_phone(phone)).appendTo($reach);
				this.accounts_line($meta, c);
				const $tools = $('<div class="ge-tools-item-tools"></div>').appendTo($item);
				if (can_set && primary !== c.name) this.button($tools, __("Set primary"), () => this.set_primary("Contact", c.name, full_name), "quiet");
				if (perms.write) this.button($tools, __("Unlink"), () => this.unlink("Contact", c.name, full_name), "quiet");
			});
		}
		if (this.widgets.addresses) {
			const list = addresses || [];
			total += list.length;
			const fields = this.meta().fields || [];
			const link_field = primary_address_field(fields);
			const primary = primary_address_name(this.ed.values || {}, fields);
			const can_set = this.editable_field(link_field);
			const $section = $('<section class="ge-tools-people"></section>').appendTo($body);
			$('<h3 class="ge-tools-head"></h3>').text(__("Addresses ({0})", [list.length])).appendTo($section);
			const $buttons = $('<div class="ge-tools-buttons"></div>').appendTo($section);
			if (perms.write && can("can_create", "Address")) this.button($buttons, __("New address"), () => this.create_party("Address"));
			if (perms.write) this.button($buttons, __("Link existing"), () => this.link_existing("Address"));
			if (!list.length) $('<p class="ge-muted ge-tools-none"></p>').text(__("No addresses linked to any related parties yet.")).appendTo($section);
			const $list = $('<ul class="ge-tools-list"></ul>').appendTo($section);
			list.forEach((a) => {
				const is_primary = a.name === primary || (on_account && !!a.is_primary_address);
				const account_primary = !is_primary && !on_account && !!a.is_primary_address;
				const text = a.custom_full_address || [a.address_line1, a.address_line2].filter(Boolean).join(", ") || a.name;
				const $item = this.person_item($list, text, frappe.utils.get_form_link("Address", a.name), is_primary, account_primary);
				const $meta = $item.find(".ge-tools-item-main");
				const kind = [a.address_type ? __(a.address_type) : "", a.address_title].filter(Boolean).join(" · ");
				if (kind) $('<div class="ge-tools-item-meta"></div>').text(kind).appendTo($meta);
				this.accounts_line($meta, a);
				const $tools = $('<div class="ge-tools-item-tools"></div>').appendTo($item);
				if (can_set && primary !== a.name) this.button($tools, __("Set primary"), () => this.set_primary("Address", a.name, text), "quiet");
				if (perms.write) this.button($tools, __("Unlink"), () => this.unlink("Address", a.name, text), "quiet");
			});
		}
		this.count("people", total || "");
	}

	button($parent, label, fn, tone) {
		return $(`<button type="button" class="sf-btn sf-btn--${tone || "secondary"}"></button>`)
			.text(label)
			.on("click", (event) => {
				event.preventDefault();
				fn();
			})
			.appendTo($parent);
	}

	person_item($list, title, href, is_primary, account_primary) {
		const $item = $('<li class="ge-tools-item"></li>').appendTo($list);
		const $main = $('<div class="ge-tools-item-main"></div>').appendTo($item);
		const $head = $('<div class="ge-tools-item-head"></div>').appendTo($main);
		$('<a class="ge-tools-item-title" target="_blank" rel="noopener noreferrer"></a>').attr("href", href).text(title).appendTo($head);
		if (is_primary) $('<span class="ge-tools-badge"></span>').text(__("Primary")).appendTo($head);
		if (account_primary) {
			$('<span class="ge-tools-badge ge-tools-badge--quiet"></span>')
				.text(__("Account primary"))
				.attr("title", __("Primary for the account, not for this document"))
				.appendTo($head);
		}
		return $item;
	}

	// The Desk directory's Account / Supplier column as a line under the contact or address
	// (v1.746.0, TASK-2026-02942); it was "Linked to", every party by ID. The same rule decides it
	// (party_sources.js account_entries): the Customers and Suppliers by display name, each linked
	// to its record with the ID on hover, a Supplier tagged, this record itself a quiet "This
	// account" / "This supplier" when it is a Customer or Supplier, and a quiet "None" when there
	// are neither. Written as text, never markup.
	accounts_line($meta, record) {
		const entries = account_entries(record && record.accounts, this.doctype, this.name);
		const $line = $('<div class="ge-tools-item-meta ge-tools-accounts"></div>').appendTo($meta);
		$('<span class="ge-tools-accounts-label"></span>').text(`${__("Account / Supplier")}:`).appendTo($line);
		if (!entries.length) {
			$('<span class="ge-tools-account"></span>').text(__("None")).appendTo($line);
			return;
		}
		entries.forEach((e) => {
			const $entry = $('<span class="ge-tools-account"></span>').appendTo($line);
			// The rule's words go through literal __() calls, so Frappe's translation extractor, which
			// reads only __("...") literals, finds them. The tag below is the exception: it is a DocType's
			// name, which this file never writes (the tools single out no DocType), and the catalogue has
			// it from the DocType itself and from the Desk cell's literal.
			if (e.own) {
				$entry.text(e.words === "This supplier" ? __("This supplier") : __("This account"));
				return;
			}
			const $link = $('<a target="_blank" rel="noopener noreferrer"></a>')
				.attr("href", frappe.utils.get_form_link(e.doctype, e.name))
				.text(e.label)
				.appendTo($entry);
			if (e.hover) $link.attr("title", e.hover);
			if (e.tag) $('<span class="ge-tools-badge ge-tools-badge--quiet"></span>').text(__(e.tag)).appendTo($entry);
		});
	}

	// The app's Contact / Address quick entry (contacts_ux.create_for), with this record as its
	// context, as the Party component's "+ New" opens it.
	create_party(doctype) {
		const ux = window.erpnext_enhancements && erpnext_enhancements.contacts_ux;
		if (!ux || typeof ux.create_for !== "function") {
			this.open_form();
			return;
		}
		const record = Object.assign({}, this.ed.values || {}, { doctype: this.doctype, name: this.name });
		this.dialog(() => ux.create_for(doctype, record, { after_insert: () => this.load_people() }));
	}

	// The directory's Link Existing (unified_tab_controller.js link_existing_record): the picked
	// record is linked to this one, and to its party only when the person ticks "Also add to".
	link_existing(doctype) {
		const party = form_party(this.doctype, Object.assign({}, this.ed.values || {}, { name: this.name }));
		const fields = [{ label: __("Select {0}", [__(doctype)]), fieldname: "record", fieldtype: "Link", options: doctype, reqd: 1 }];
		if (party) {
			// A frappe field's label and description are HTML: the party's name is escaped.
			const party_name = frappe.utils.escape_html(party.link_name);
			fields.push({
				label: __("Also add to {0} {1}", [__(party.link_doctype), party_name]),
				fieldname: "also_party",
				fieldtype: "Check",
				default: 0,
				description: __("Only if this {0} belongs to {1}. A consultant, inspector or subcontractor on the job does not.", [
					__(doctype),
					party_name,
				]),
			});
		}
		const link_to = (record, target) =>
			this.sf().call(
				SYNC + "link_existing_record",
				{ doctype, docname: record, link_doctype: target.link_doctype, link_name: target.link_name },
				{ quiet: true }
			);
		this.dialog(() =>
			frappe.prompt(
				fields,
				(values) => {
					link_to(values.record, { link_doctype: this.doctype, link_name: this.name })
						.then(() => {
							if (!(party && values.also_party)) return null;
							return link_to(values.record, party).catch(() => {
								this.sf().toast(__("Linked to this {0}, but not added to {1}.", [__(this.doctype), party.link_name]), {
									tone: "warning",
									timeout: 6000,
								});
							});
						})
						.then(() => {
							this.sf().toast(__("{0} linked.", [__(doctype)]), { tone: "success", timeout: 3000 });
							this.load_people();
						})
						.catch((err) => this.people_refused(err, __("It was not linked.")));
				},
				__("Add {0}", [__(doctype)]),
				__("Add")
			)
		);
	}

	// The directory's Import Contacts: the related parties' contacts this record does not have yet,
	// ticked one by one; only the ticked ones are linked.
	import_contacts(sources) {
		this.sf()
			.call(SYNC + "get_importable_contacts", { target_doctype: this.doctype, target_name: this.name, sources }, { quiet: true, method: "GET" })
			.then((available) => {
				const list = available || [];
				if (!list.length) {
					this.sf().toast(__("Every contact on the related records is already on this {0}.", [__(this.doctype)]), { tone: "info", timeout: 5000 });
					return;
				}
				const esc = frappe.utils.escape_html;
				this.dialog(() => {
					const dialog = new frappe.ui.Dialog({
						title: __("Import Contacts"),
						size: "large",
						fields: [
							{
								fieldtype: "HTML",
								fieldname: "intro",
								options: `<p class="text-muted">${esc(
									__("Contacts on the related records that are not linked to this {0} yet. Only the ones you tick are linked.", [__(this.doctype)])
								)}</p>`,
							},
							{
								fieldtype: "MultiCheck",
								fieldname: "contacts",
								// frappe v16's MultiCheck writes `label` as HTML, so every piece of it is escaped,
								// and writes `value` unescaped into an attribute (data-unit="..."), so the value is
								// the row's index, never the Contact's name; it is mapped back on Import.
								options: list.map((c, index) => {
									const name = [c.first_name, c.last_name].filter(Boolean).join(" ") || c.name;
									const detail = [c.custom_title, c.custom_email, shown_phone(c.custom_phone_number || c.custom_mobile_number)].filter(Boolean).join(" · ");
									return {
										label: detail ? `${esc(name)} <span class="text-muted">— ${esc(detail)}</span>` : esc(name),
										value: String(index),
										checked: 0,
									};
								}),
								columns: "22rem 2",
								select_all: true,
							},
						],
						primary_action_label: __("Import"),
						primary_action: () => {
							const selected = (dialog.get_value("contacts") || [])
								.map((index) => (list[Number(index)] || {}).name)
								.filter(Boolean);
							if (!selected.length) {
								this.sf().toast(__("Select at least one contact"), { tone: "warning", timeout: 4000 });
								return;
							}
							const $btn = dialog.get_primary_btn();
							$btn.prop("disabled", true);
							this.sf()
								.call(SYNC + "import_contacts", { target_doctype: this.doctype, target_name: this.name, contacts: JSON.stringify(selected) }, { quiet: true })
								.then((res) => {
									dialog.hide();
									const linked = (res && res.linked) || 0;
									this.sf().toast(linked === 1 ? __("1 contact linked") : __("{0} contacts linked", [linked]), {
										tone: linked ? "success" : "warning",
										timeout: 4000,
									});
									this.load_people();
								})
								.catch((err) => {
									$btn.prop("disabled", false);
									this.sf().toast((err && err.message) || __("The contacts were not linked."), { tone: "warning", timeout: 6000 });
								});
						},
					});
					dialog.show();
					return dialog;
				});
			})
			.catch((err) => this.people_refused(err, __("The contacts could not be read.")));
	}

	// Set Primary, as the directory does it: the record's own field first (saved through the editor's
	// queue, as if the person had picked it in its step), then, on an account only, its global flag.
	set_primary(doctype, record, label) {
		const fieldname = doctype === "Contact" ? "primary_contact" : primary_address_field(this.meta().fields || []);
		if (!this.editable_field(fieldname)) return;
		const scope = is_account_doctype(this.doctype) ? this.name : `${__(this.doctype)} ${this.name}`;
		const question =
			doctype === "Contact"
				? __("Set {0} as the primary contact for {1}?", [frappe.utils.escape_html(label), frappe.utils.escape_html(scope)])
				: __("Set this as the primary address for {0}?", [frappe.utils.escape_html(scope)]);
		this.dialog(() =>
			frappe.confirm(question, () => {
				// Into the values and the queue now (synchronously, so the save below carries it), on
				// screen if the field is, and the Party component told, as a pick in the step would.
				const ed = this.ed;
				const before = (ed.values || {})[fieldname];
				ed.set_value_local(fieldname, record);
				ed.show_value(fieldname, record);
				ed.party_entry().changed(fieldname, before, record);
				// save_before_moving resolves without saving when the save could not be sent (no signal, a
				// 5xx): the change then stays queued and the saving line says so, and nothing claims it saved.
				const unsaved = () => !!(ed.queue && ed.queue.has_unsaved());
				ed
					.save_before_moving()
					.then(() => {
						if (unsaved()) return "unsaved";
						if (!is_account_doctype(this.doctype)) return null;
						const method = doctype === "Contact" ? "set_primary_contact" : "set_primary_address";
						const args = { account_doctype: this.doctype, account_name: this.name };
						args[doctype === "Contact" ? "contact_name" : "address_name"] = record;
						return this.sf().call(SYNC + method, args, { quiet: true });
					})
					.then((result) => {
						if (result === "unsaved") {
							this.sf().toast(__("Not saved yet: it saves with the rest of this record's changes."), { tone: "warning", timeout: 6000 });
						} else {
							this.sf().toast(doctype === "Contact" ? __("Primary contact updated") : __("Primary address updated"), { tone: "success", timeout: 3000 });
						}
						this.load_people();
					})
					.catch((err) => this.people_refused(err, __("The primary was not changed.")));
			})
		);
	}

	unlink(doctype, record, label) {
		this.dialog(() =>
			frappe.confirm(__("Unlink {0} from this {1}?", [frappe.utils.escape_html(label), __(this.doctype)]), () => {
				this.sf()
					.call(SYNC + "unlink_record", { doctype, docname: record, link_doctype: this.doctype, link_name: this.name }, { quiet: true })
					.then(() => {
						this.sf().toast(__("{0} unlinked.", [__(doctype)]), { tone: "success", timeout: 3000 });
						this.load_people();
					})
					.catch((err) => this.people_refused(err, __("It was not unlinked.")));
			})
		);
	}

	people_refused(err, fallback) {
		this.sf().toast((err && err.message) || fallback, { tone: "warning", timeout: 6000 });
		this.load_people();
	}

	// ------------------------------------------------------------------ connections

	load_connections() {
		this.connections_loaded = true;
		// Its own count: a reload of the rest (seq) must not strand this list on "loading".
		const seq = (this.connections_seq = (this.connections_seq || 0) + 1);
		const record = this.mount_seq;
		const $body = this.$connections;
		const dashboard = this.meta().__dashboard || {};
		const items = rules.connection_doctypes(this.groups);
		this.sf().state.render($body, "loading");
		this.sf()
			.call(dashboard.method || "frappe.desk.notifications.get_open_count", { doctype: this.doctype, name: this.name, items }, { quiet: true, method: "GET" })
			.then((result) => {
				if (seq !== this.connections_seq || record !== this.mount_seq || !this.current()) return;
				this.render_connections(rules.connection_counts((result || {}).count));
			})
			.catch((err) => {
				if (seq !== this.connections_seq || record !== this.mount_seq || !this.current()) return;
				this.sf().state.render($body, "error", {
					title: __("Connections could not be counted."),
					body: (err && err.message) || "",
					on_action: () => this.load_connections(),
				});
			});
	}

	render_connections(counts) {
		const $body = this.$connections.empty();
		const dashboard = this.meta().__dashboard || {};
		let total = 0;
		this.groups.forEach((group) => {
			const $group = $('<section class="ge-tools-connections"></section>').appendTo($body);
			if (group.label) $('<h3 class="ge-tools-head"></h3>').text(__(group.label)).appendTo($group);
			const $list = $('<ul class="ge-tools-list"></ul>').appendTo($group);
			group.items.forEach((doctype) => {
				const found = counts[doctype] || { count: 0, open_count: 0, names: [], internal: false };
				total += found.count;
				const filters = rules.connection_filters(dashboard, doctype, this.name, found);
				const $item = $('<li class="ge-tools-item"></li>').appendTo($list);
				const $open = $('<button type="button" class="ge-tools-connection"></button>').prop("disabled", !filters).appendTo($item);
				$('<span class="ge-tools-connection-name"></span>').text(__(doctype)).appendTo($open);
				$('<span class="ge-tools-connection-count"></span>')
					.text(found.open_count ? __("{0} ({1} open)", [found.count, found.open_count]) : String(found.count))
					.appendTo($open);
				$open.attr("aria-label", __("{0}: {1} linked to this record. Open the list.", [__(doctype), found.count]));
				$open.on("click", () =>
					this.leave(() => {
						frappe.route_options = filters;
						frappe.set_route("List", doctype, "List");
					})
				);
			});
		});
		this.count("connections", total || "");
	}

	// ------------------------------------------------------------------ the Actions menu

	draw_actions_button() {
		const $where = (this.ed.$root || $()).find(".ge-band-actions, .fk-head-row").first();
		if (!$where.length) return;
		$('<button type="button" class="sf-btn sf-btn--quiet ge-tools-actions" aria-haspopup="menu"></button>')
			.text(__("Actions"))
			.attr("aria-label", __("Actions for this record"))
			.on("click", (event) => this.open_menu(event.currentTarget))
			.appendTo($where);
		// The menu's script and stylesheet ahead of the first tap: frappe.require adds a stylesheet
		// without waiting for it, and a menu measured before its styles arrive is placed wrongly.
		this.kit();
	}

	// The planner kit (its menu, and the history guard the menu joins), loaded on the guided page the
	// first time; null when it cannot be had in time.
	kit() {
		const have = () => (window.planner_kit && window.planner_kit.menu ? window.planner_kit : null);
		// Asked for every time: the Party component may have loaded the kit's script without its
		// stylesheet (it needs only the guard), and frappe.require loads each asset once.
		if (this.kit_ready) return Promise.resolve(have());
		return new Promise((resolve) => {
			const timer = setTimeout(() => resolve(have()), KIT_WAIT_MS);
			try {
				frappe.require(["planner_kit.bundle.js", "planner_kit.bundle.css"], () => {
					clearTimeout(timer);
					this.kit_ready = !!have();
					resolve(have());
				});
			} catch (e) {
				clearTimeout(timer);
				resolve(null);
			}
		});
	}

	open_menu(anchor) {
		const items = this.menu_items();
		const fresh = !this.kit_ready;
		this.kit().then((kit) => {
			if (!kit || !this.current()) {
				this.sf().toast(__("The actions could not be opened. Use the full form."), { tone: "warning", timeout: 5000 });
				return;
			}
			// Loaded by this tap: give its stylesheet a moment before the menu is measured.
			setTimeout(() => kit.menu({ anchor, title: this.title(), items, owner: "ge-tools" }), fresh ? 150 : 0);
		});
	}

	menu_items() {
		const doctype = this.doctype;
		const name = this.name;
		const values = this.ed.values || {};
		const perms = this.perms();
		const meta = this.meta();
		const info = this.info || {};
		const items = [];
		const add = (label, on_click, extra) => items.push(Object.assign({ label, on_click }, extra || {}));

		// Create, as the DocType's own form script offers it.
		rules.create_items(this.config, values, (target) => can("can_create", target)).forEach((item) => {
			add(__("Create {0}", [__(item.label)]), () =>
				this.leave(() => frappe.model.open_mapped_doc({ method: item.method, source_name: name }))
			);
		});
		if (record_actions.can_generate_contract(doctype)) {
			// What is typed saves first, as for Create, Print and Customize: the draft is made from the record.
			add(__("Generate Contract"), () => this.leave(() => this.dialog(() => record_actions.open_generate_contract(doctype, name))));
		}
		if (items.length) items.push({ divider: true });

		// This app's own buttons on the form, by what the record has.
		const drive = info.drive_folder || {};
		if (drive.id) {
			add(drive.missing ? __("Drive Folder Missing") : __("Open Drive Folder"), () => {
				const dialog = record_actions.open_drive_folder(drive.id, drive.missing);
				if (dialog) this.dialog(() => dialog);
			});
		}
		if ((meta.fields || []).some((f) => f.fieldname === "custom_reminder_action")) {
			add(__("Set Reminder"), () =>
				this.dialog(() => record_actions.open_reminder({ doctype, name, title: this.title(), after_insert: () => this.reload() }))
			);
		}
		if (perms.email) add(__("New Email"), () => this.new_email());
		const print_settings = (() => {
			try {
				return frappe.model.get_doc(":Print Settings", "Print Settings") || {};
			} catch (e) {
				return {};
			}
		})();
		if (
			rules.can_print({
				print: perms.print,
				issingle: meta.issingle,
				is_submittable: meta.is_submittable,
				docstatus: info.docstatus,
				allow_print_for_draft: print_settings.allow_print_for_draft,
				allow_print_for_cancelled: print_settings.allow_print_for_cancelled,
			})
		) {
			add(__("Print"), () => this.leave(() => frappe.set_route("print", doctype, name)));
		}
		if (items.length && !items[items.length - 1].divider) items.push({ divider: true });
		if (
			can("can_create", "Custom Field") &&
			can("can_create", "Property Setter") &&
			!meta.issingle &&
			!((frappe.model.core_doctypes_list || []).indexOf(doctype) !== -1)
		) {
			add(__("Customize"), () => this.leave(() => frappe.set_route("Form", "Customize Form", { doc_type: doctype })));
		}
		add(__("Rename, duplicate, delete… (full form)"), () => this.open_form());
		return items;
	}
}
