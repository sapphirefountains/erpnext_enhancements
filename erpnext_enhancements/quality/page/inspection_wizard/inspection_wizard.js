// Inspection Wizard — the inspector's touch-first, section-at-a-time inspection form.
//
// A desk Page that walks a Project Quality Inspection one frozen section at a time. Every
// answer is a tap; a measurement is one numeric field with its contracted range printed
// beside it. The URL says which screen is showing, and nothing else does:
//
//     /desk/inspection-wizard                      the drafts assigned to the signed-in user
//     /desk/inspection-wizard/QIR-.../<section>    one inspection, at <section> (from 0)
//
// Every section is its own history entry, so the phone's Back steps to the previous section
// and Forward restores the next one; Back from the list, or from the section a link opened,
// leaves the page the way it leaves any other. The older ?inspection=QIR-... form still opens
// the record, rewritten in place to the path form (replaced, not pushed): v16's set_route
// cannot write a query string, which is how the list once pushed an entry identical to
// itself and Back from any section landed on the list.
//
// It reads and writes the same record as the desk form, through api/quality_wizard.py:
// bootstrap once, autosave a field-allowlisted patch with optimistic locking, submit
// server-side. All downstream automation — non-conformances, carried-fix verdicts, the
// Critical alert — hangs off on_submit and is untouched. The desk form stays the
// supervisor surface.
//
// THE ROWS ARE FROZEN AND THIS PAGE CANNOT CHANGE THEM. The check text, the acceptance
// criteria and the measurement bounds were copied from the master template at generation.
// They are rendered here and never sent back; the server's allowlist accepts only outcome,
// measured_value, notes and photo. A wizard that could edit a bound could turn a failing
// measurement into a passing one from a phone, on site, with nothing in the diff.
//
// Styling uses Frappe CSS variables so Frappe Light and Timeless Night both work; pass/fail
// colours stay literal, matching the Visit Wizard's house convention. The page loader serves
// this file version-aware, so no .bundle.* is needed.

frappe.pages["inspection-wizard"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Inspection Wizard"),
		single_column: true,
	});
	wrapper.inspection_wizard = new InspectionWizard(page, wrapper);
};

frappe.pages["inspection-wizard"].on_page_show = function (wrapper) {
	if (wrapper.inspection_wizard) {
		wrapper.inspection_wizard.handle_route();
	}
};

const QW_STYLE = `
.qw-wrap{max-width:640px;margin:0 auto;padding-bottom:104px;font-size:15px;}
.qw-muted{color:var(--text-muted);font-size:13px;}
.qw-progress{height:6px;border-radius:3px;background:var(--control-bg);margin:8px 0 4px;overflow:hidden;}
.qw-progress>div{height:100%;border-radius:3px;background:var(--primary,#2490ef);transition:width .25s;}
.qw-stepline{display:flex;justify-content:space-between;align-items:center;font-size:14px;color:var(--text-muted);margin-bottom:10px;}
.qw-tabs{display:flex;gap:8px;overflow-x:auto;padding:2px 0 10px;position:sticky;top:0;background:var(--bg-color);z-index:3;}
.qw-tab{flex:0 0 auto;padding:9px 15px;border-radius:16px;border:1px solid var(--border-color);background:var(--card-bg);color:var(--text-color);font-size:15px;cursor:pointer;white-space:nowrap;}
.qw-tab.qw-active{background:var(--primary,#2490ef);border-color:var(--primary,#2490ef);color:#fff;font-weight:600;}
.qw-tab.qw-has-fail{border-color:#dc2626;}
.qw-card{background:var(--card-bg);border:1px solid var(--border-color);border-radius:10px;padding:14px;margin-bottom:10px;color:var(--text-color);}
.qw-card.qw-bad{border-color:#dc2626;background:rgba(220,38,38,.06);}
.qw-card.qw-done{border-color:#15803d;}
.qw-card-title{font-weight:600;font-size:17px;line-height:1.35;}
.qw-card-sub{font-size:14px;color:var(--text-muted);margin-top:4px;}
.qw-chip{display:inline-block;font-size:12px;border-radius:10px;padding:2px 9px;margin-left:6px;vertical-align:middle;}
.qw-chip-req{background:#fef3c7;color:#92400e;font-weight:600;}
.qw-chip-photo{background:#e5e7eb;color:#374151;}
.qw-chip-carried{background:#ede9fe;color:#5b21b6;font-weight:600;}
.qw-chip-addendum{background:#dbeafe;color:#1e40af;}
.qw-chip-red{background:#fde8e8;color:#b91c1c;font-weight:600;}
.qw-seg{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px;}
.qw-seg button{flex:1 1 calc(33% - 8px);min-width:84px;min-height:48px;border-radius:8px;border:1px solid var(--border-color);background:var(--control-bg);color:var(--text-color);font-size:15px;cursor:pointer;padding:7px 4px;}
.qw-seg button.qw-on{background:var(--primary,#2490ef);border-color:var(--primary,#2490ef);color:#fff;font-weight:600;}
.qw-seg button.qw-on.qw-neg{background:#dc2626;border-color:#dc2626;}
.qw-seg button.qw-on.qw-na{background:#6b7280;border-color:#6b7280;}
.qw-measure{display:flex;gap:10px;align-items:center;margin-top:12px;}
.qw-measure input{flex:1;font-size:24px;text-align:center;padding:10px;border-radius:8px;border:1px solid var(--border-color);background:var(--control-bg);color:var(--text-color);}
.qw-measure input:focus{outline:2px solid var(--primary,#2490ef);}
.qw-measure input.qw-oor{border-color:#dc2626;color:#b91c1c;font-weight:700;}
.qw-measure .qw-uom{flex:0 0 auto;font-size:15px;color:var(--text-muted);}
.qw-range{margin-top:6px;font-size:13px;color:var(--text-muted);text-align:center;}
.qw-range.qw-oor{color:#b91c1c;font-weight:600;}
.qw-row-extra{margin-top:10px;display:flex;gap:8px;align-items:center;}
.qw-row-extra input{flex:1;padding:10px;border-radius:8px;border:1px solid var(--border-color);background:var(--control-bg);color:var(--text-color);font-size:15px;}
.qw-photo-btn{flex:0 0 auto;min-width:52px;min-height:44px;border-radius:8px;border:1px solid var(--border-color);background:var(--control-bg);cursor:pointer;font-size:18px;}
.qw-photo-btn.qw-has-photo{border-color:#15803d;background:#e7f7ed;}
.qw-banner{border-radius:10px;padding:12px 14px;margin-bottom:10px;font-size:15px;line-height:1.45;}
.qw-banner-red{background:#fde8e8;color:#b91c1c;}
.qw-banner-blue{background:rgba(36,144,239,.1);color:var(--text-color);}
.qw-banner-green{background:#e7f7ed;color:#15803d;}
.qw-banner-amber{background:#fef3c7;color:#92400e;}
.qw-banner h6{margin:0 0 4px;font-size:13px;text-transform:uppercase;letter-spacing:.04em;}
.qw-help{border:1px solid var(--border-color);border-radius:10px;margin-bottom:10px;overflow:hidden;background:var(--card-bg);}
.qw-help-head{display:flex;align-items:center;gap:8px;width:100%;padding:12px 14px;background:none;border:none;color:var(--text-color);font-size:15px;font-weight:600;cursor:pointer;text-align:left;}
.qw-help-caret{margin-left:auto;transition:transform .15s;}
.qw-help.qw-open .qw-help-caret{transform:rotate(90deg);}
.qw-help-body{padding:0 14px 14px;font-size:14px;line-height:1.5;color:var(--text-color);}
.qw-nav{position:fixed;left:0;right:0;bottom:0;display:flex;gap:10px;padding:12px 14px calc(12px + env(safe-area-inset-bottom));background:var(--bg-color);border-top:1px solid var(--border-color);z-index:5;}
.qw-nav button{flex:1;min-height:50px;border-radius:9px;border:1px solid var(--border-color);background:var(--control-bg);color:var(--text-color);font-size:16px;cursor:pointer;}
.qw-nav button.qw-primary{background:var(--primary,#2490ef);border-color:var(--primary,#2490ef);color:#fff;font-weight:600;}
.qw-nav button:disabled{opacity:.5;cursor:default;}
.qw-savestate{position:fixed;left:0;right:0;bottom:calc(74px + env(safe-area-inset-bottom));text-align:center;font-size:13px;pointer-events:none;z-index:6;}
.qw-savestate span{display:inline-block;padding:4px 12px;border-radius:12px;background:var(--control-bg);color:var(--text-muted);border:1px solid var(--border-color);}
.qw-savestate.qw-err span{background:#fde8e8;color:#b91c1c;border-color:#b91c1c;font-weight:600;pointer-events:auto;}
.qw-list-item{display:block;width:100%;text-align:left;background:var(--card-bg);border:1px solid var(--border-color);border-radius:10px;padding:14px;margin-bottom:10px;color:var(--text-color);cursor:pointer;}
.qw-list-item h5{margin:0 0 4px;font-size:16px;}
.qw-sign{margin-top:10px;border:1px solid var(--border-color);border-radius:10px;padding:12px;background:var(--card-bg);}
.qw-sign-img{max-width:100%;border:1px solid var(--border-color);border-radius:8px;background:#fff;}
.qw-empty{text-align:center;padding:40px 16px;color:var(--text-muted);}
`;

// ---------------------------------------------------------------------------
// Rendering authored text.
//
// Two helpers and never frappe.utils.xss_sanitise, whose default strategies are
// ["html", "js"] — it escapes <, >, ", ' and /, so a Text Editor field renders as visible
// tags and the inspector reads a literal <ul><li><b> instead of a list. The panel still
// LOOKS populated, which is exactly why that shipped once on the maintenance side and
// survived review. tests/test_inspection_wizard_markup.py fails the build on it now.
// ---------------------------------------------------------------------------

function qw_rich_html(html) {
	// Text Editor content: keep the markup, drop anything that could execute.
	if (!html) return "";
	const doc = new DOMParser().parseFromString(String(html), "text/html");
	doc.querySelectorAll("script, style, iframe, object, embed, link, meta").forEach((node) =>
		node.remove()
	);
	doc.querySelectorAll("*").forEach((node) => {
		Array.from(node.attributes).forEach((attr) => {
			const name = attr.name.toLowerCase();
			const value = (attr.value || "").trim().toLowerCase();
			if (name.startsWith("on") || value.startsWith("javascript:")) {
				node.removeAttribute(attr.name);
			}
		});
	});
	return doc.body.innerHTML;
}

function qw_plain_html(text) {
	// Small Text content: one item per line, so collapsing the newlines hides one.
	if (!text) return "";
	return frappe.utils.escape_html(String(text)).replace(/\n/g, "<br>");
}

// Which answers count as a failure. Mirrors quality/merge.py FAILING_ANSWERS; an out-of-range
// measurement is a failure too, and the server is the one that decides that — the wizard shows
// what came back rather than re-deriving it, so the two can never disagree on screen.
const QW_FAILING = ["Fail"];

class InspectionWizard {
	constructor(page, wrapper) {
		this.page = page;
		this.wrapper = wrapper;
		this.body = $('<div class="qw-wrap"></div>').appendTo(page.main);
		$(`<style>${QW_STYLE}</style>`).appendTo(page.main);
		this.doc = null;
		this.section_index = 0;
		// Which screen is painted or being fetched: "list", "inspection", or null.
		this.view = null;
		// The bootstrap on the wire, if any: { name, index }. A route that arrives while it
		// loads updates the index, so it lands on whichever section the URL names by then.
		this.loading = null;
		// Bumped by every fetch for a new screen. A response that comes back under an older
		// ticket is for a screen the inspector has already left and is dropped unpainted --
		// otherwise whichever of a list fetch and a record fetch lands LAST wins the screen.
		this.ticket = 0;
		// Unsaved answers, one buffer per inspection, each carrying the `modified` it was given
		// against (its save's lock); and the newest `modified` known for each inspection, which a
		// new buffer starts from. Keyed by name because a save outlives the screen it was queued
		// on: with one shared buffer, a failed save of one inspection was retried under the next
		// one's name and lock.
		this.pending = {};
		this.modified = {};
		this.saving = false;
		this.in_flight = null;
		this.retry_timer = null;
		// A repaint put off because an input had focus; see render_unless_typing.
		this.render_deferred = false;
		this.deferred_timer = null;
		this.bind_unload();
		this.bind_hide();
		this.bind_deferred_render();
		this.render_save_state();
		// No handle_route() here. on_page_show always follows on_page_load -- pageview.js
		// binds "show" and container.js fires it straight after -- and calling it from both
		// sent two bootstrap fetches on the first open, racing each other.
	}

	bind_unload() {
		// A closed tab with an unsaved answer is a check somebody will swear they did.
		$(window).on("beforeunload.qw", () => {
			if (this.has_pending() || this.saving) {
				return __("Some answers have not saved yet.");
			}
		});
	}

	// Anything waiting to be saved -- for one inspection, or for any of them.
	has_pending(name) {
		return (name ? [name] : Object.keys(this.pending)).some((key) => {
			const queued = this.pending[key];
			return (
				!!queued && (Object.keys(queued.fields).length > 0 || Object.keys(queued.rows).length > 0)
			);
		});
	}

	bind_hide() {
		// Leaving for another Desk screen -- Back from the first screen, a sidebar link --
		// commits a note that was typed but never blurred and sends what is queued, rather
		// than leaving it to a timer running on a hidden page.
		$(this.wrapper).on("hide", (event) => {
			if (event.target !== this.wrapper) return;
			this.commit_active_input();
			this.flush();
			// Coming back to the list refetches it: its counts are behind by whatever was
			// answered since.
			if (this.view === "list") this.view = null;
		});
	}

	// A measurement or note is queued on "change", which the browser fires when the input
	// loses focus. An input repainted away while it still has focus never loses it, so a value
	// typed just before Back went with it. Blurring it first queues the value.
	commit_active_input() {
		const active = document.activeElement;
		if (active && this.wrapper.contains(active)) active.blur();
	}

	// An input on this page that has focus: somebody is typing into it, and what they have
	// typed is in the input alone until it blurs.
	typing() {
		const active = document.activeElement;
		return !!active && active.tagName === "INPUT" && this.wrapper.contains(active);
	}

	// The repaint render_unless_typing put off runs once the input lets go -- not in the same
	// instant: the blur is the start of a tap somewhere else, and repainting under it replaced the
	// button that tap was landing on, which then never heard the click.
	bind_deferred_render() {
		this.body.on("focusout", () => {
			if (!this.render_deferred) return;
			clearTimeout(this.deferred_timer);
			this.deferred_timer = setTimeout(() => {
				if (this.render_deferred) this.render_unless_typing();
			}, 300);
		});
	}

	// ------------------------------------------------------------------ routing

	// Runs on every route into this page -- the first open, every section change, Back and
	// Forward -- because container.js fires "show" even when the page itself is unchanged.
	// It is the only thing that changes the screen. Everything else asks for a screen by
	// routing to it (route_to), so the screen and the history cannot disagree, and it must
	// stay idempotent: a second call for the route already showing changes nothing.
	handle_route() {
		const route = frappe.get_route() || [];
		if (route[0] !== "inspection-wizard") return;
		this.commit_active_input();

		const name = route[1] ? String(route[1]) : "";
		if (!name) {
			const legacy = this.take_legacy_inspection();
			if (legacy) {
				this.clear_screen();
				this.view = null;
				this.route_to(legacy, 0, true);
				return;
			}
			this.render_list();
			return;
		}
		const index = parseInt(route[2], 10);
		this.show_inspection(name, index > 0 ? index : 0);
	}

	// The query-string form of a link, /desk/inspection-wizard?inspection=QIR-..., which is what
	// this page used to be opened with -- or anything that set frappe.route_options, which is
	// where the router puts the query of a /desk link it intercepts. It is opened, and its
	// history entry replaced with the path form.
	//
	// Read once and consumed. Nothing in the router clears route_options on a popstate, so an
	// {inspection} left in it would reopen that inspection every time Back reached the list.
	take_legacy_inspection() {
		const options = frappe.route_options || {};
		const name = frappe.utils.get_url_arg("inspection") || options.inspection || "";
		if (options.inspection !== undefined) frappe.route_options = null;
		return name ? String(name) : "";
	}

	// The one place this page changes the URL, and it changes only the URL: the router then
	// calls handle_route, which draws the screen.
	route_to(name, index, replace) {
		const parts = ["inspection-wizard"];
		if (name) parts.push(name, String(index || 0));
		// Compared first: a set_route to where we already are still fires a route event.
		if ((frappe.get_route() || []).join("/") === parts.join("/")) return;
		// Never an object argument, and never a leftover route_options. v16's set_route moves
		// an object into route_options and writes the path WITHOUT a query string, then
		// push_state compares the query it did not write with the address bar -- so with
		// anything left in route_options, even a route to the current path pushes an entry.
		frappe.route_options = null;
		frappe.route_flags.replace_route = !!replace;
		frappe.set_route(parts);
		// push_state has already read the flag -- synchronously, inside set_route -- and v16
		// only clears it once the route's ajax has settled. Left up until then, the next
		// navigation (a sidebar link tapped while the record loads) would replace an entry too.
		frappe.route_flags.replace_route = false;
	}

	// Section tabs and Previous/Next come through here, so every section is a history entry.
	go_section(index) {
		if (!this.doc) return;
		const target = this.clamp_section(index);
		if (target === this.section_index) return;
		this.route_to(this.doc.header.name, target);
	}

	clamp_section(index) {
		const last = Math.max(0, this.sections().length - 1);
		return Math.min(Math.max(0, index || 0), last);
	}

	// A section number that is out of range or missing (a hand-typed /QIR-.../9 on a
	// four-section inspection) shows the nearest real section, and the address bar is corrected
	// in place to match, so no history entry names a screen that was never shown.
	correct_route() {
		const route = frappe.get_route() || [];
		// Only while this inspection is the page on screen: a bootstrap landing after the
		// inspector has gone elsewhere must not drag them back.
		if (!this.doc || route[0] !== "inspection-wizard" || route[1] !== this.doc.header.name) {
			return;
		}
		this.route_to(this.doc.header.name, this.section_index, true);
	}

	// Whatever is on screen is about to be replaced. The nav bar is fixed to the viewport and
	// lives on page.main, outside the .qw-wrap every screen repaints, so it has to be taken away
	// here -- left behind, its buttons floated over the list doing nothing, and "Finish and
	// submit" threw on the missing record.
	clear_screen() {
		this.ticket += 1;
		this.doc = null;
		this.loading = null;
		this.page.main.find(".qw-nav").remove();
		this.body.html(`<div class="qw-empty">${__("Loading...")}</div>`);
		return this.ticket;
	}

	show_inspection(name, index) {
		if (this.doc && this.doc.header.name === name) {
			this.show_section(index);
			return;
		}
		if (this.loading && this.loading.name === name) {
			this.loading.index = index;
			return;
		}
		this.load(name, index);
	}

	show_section(index) {
		const target = this.clamp_section(index);
		// Leaving a section sends what it queued; the debounce is for a run of taps, not for a
		// step away.
		this.flush();
		if (target !== this.section_index) {
			this.section_index = target;
			this.render();
		}
		this.correct_route();
	}

	// ------------------------------------------------------------------ loading

	load(name, index) {
		const ticket = this.clear_screen();
		this.view = "inspection";
		const loading = { name: name, index: index };
		this.loading = loading;
		// Queued answers go first, and are waited for. A bootstrap read while this inspection's
		// own save is still on the wire comes back with the old answers.
		this.flush().then(() => {
			if (ticket !== this.ticket) return;
			frappe.call({
				method: "erpnext_enhancements.api.quality_wizard.get_inspection_bootstrap",
				args: { inspection: name },
				callback: (r) => {
					if (ticket !== this.ticket) return;
					if (!r || !r.message) return;
					this.doc = r.message;
					// Answers still queued keep the lock they were given against. If the stamp read
					// now is that one, nothing happened in between and they are laid back over the
					// fresh copy. If not, somebody else saved this inspection since: taking the new
					// stamp let the queued answer overwrite theirs without a word, so the queue keeps
					// its older lock, its save stays refused ("changed elsewhere, reload"), and the
					// screen shows what the server holds, not an answer that is not going to land.
					const queued = this.has_pending(name) && this.pending[name];
					if (!queued || String(queued.base) === String(this.doc.state.modified)) {
						this.note_modified(name, this.doc.state.modified);
						this.overlay_pending(this.doc);
					}
					this.section_index = this.clamp_section(loading.index);
					this.render();
					this.correct_route();
				},
				error: () => {
					if (ticket !== this.ticket) return;
					this.body.html(
						`<div class="qw-empty">${__("This inspection could not be opened.")}</div>`
					);
				},
				// Success or failure, this fetch is over. Left set after a failure, `loading`
				// made every later route to the same inspection wait for a response that had
				// already come and gone.
				always: () => {
					if (ticket === this.ticket) this.loading = null;
				},
			});
		});
	}

	// Answers still queued for this inspection -- a save that failed and is retrying -- are laid
	// over the fresh copy, or reopening it would show the old answer beside a pill saying the
	// new one is still being saved.
	overlay_pending(doc) {
		const queued = this.pending[doc.header.name];
		if (!queued) return;
		Object.assign(doc.header, queued.fields);
		(doc.sections || []).forEach((section) => {
			section.rows.forEach((row) => {
				if (queued.rows[row.name]) Object.assign(row, queued.rows[row.name]);
			});
		});
	}

	render_list() {
		// Already showing it, or fetching it; see handle_route.
		if (this.view === "list") return;
		const ticket = this.clear_screen();
		this.view = "list";
		// Leaving an inspection sends its answers first, and waits, so the counts on the list
		// include the one just given.
		this.flush().then(() => {
			if (ticket !== this.ticket) return;
			frappe.call({
				method: "erpnext_enhancements.api.quality_wizard.get_open_inspections",
				callback: (r) => {
					if (ticket !== this.ticket) return;
					const data = (r && r.message) || {};
					const rows = data.inspections || [];
					if (!rows.length) {
						this.body.html(
							`<div class="qw-empty"><p>${__("No inspections are assigned to you.")}</p>
							 <p class="qw-muted">${__("An inspection is generated from a project milestone. Ask your project manager, or open the Quality Control workspace.")}</p></div>`
						);
						return;
					}
					const items = rows
						.map((row) => {
							const done = `${row.mandatory_answered || 0}/${row.mandatory_total || 0}`;
							return `<button class="qw-list-item" data-name="${frappe.utils.escape_html(row.name)}">
								<h5>${frappe.utils.escape_html(row.project_name || row.project || row.name)}</h5>
								<div class="qw-muted">${frappe.utils.escape_html(row.milestone || "")}</div>
								<div class="qw-muted">${__("Required checks answered")}: ${done}
								 &middot; ${frappe.utils.escape_html(row.scheduled_date || row.inspection_date || "")}</div>
							</button>`;
						})
						.join("");
					this.body.html(`<h4>${__("Your open inspections")}</h4>${items}`);
					this.body.find(".qw-list-item").on("click", (event) => {
						// Only the route changes here; handle_route opens it when the router
						// calls back. Opening it here as well raced the list fetch that the
						// router's own "show" started.
						this.route_to(String($(event.currentTarget).data("name")), 0);
					});
				},
			});
		});
	}

	// ----------------------------------------------------------------- rendering

	sections() {
		return (this.doc && this.doc.sections) || [];
	}

	current_section() {
		return this.sections()[this.section_index] || { title: "", rows: [] };
	}

	render() {
		this.render_deferred = false;
		if (!this.doc) return;
		const state = this.doc.state;
		if (state.docstatus === 1) {
			this.render_submitted();
			return;
		}
		this.body.empty();
		this.render_header();
		this.render_tabs();
		this.render_section();
		this.render_nav();
		this.render_save_state();
	}

	// For a repaint nobody asked for -- a save's answer arriving. A repaint replaces every input
	// on the section, and one that has focus holds what is being typed, which nothing has queued
	// yet; repainting it away lost the value and queued nothing. A section change sends its save
	// at once, so the answer lands one round trip after the inspector starts typing the next
	// section's first measurement. The state is already applied; only the paint waits.
	render_unless_typing() {
		if (this.typing()) {
			this.render_deferred = true;
			return;
		}
		this.render();
	}

	render_header() {
		const header = this.doc.header;
		const state = this.doc.state;
		const percent = Math.round(state.completion_percent || 0);
		$(`<div>
			<h4 style="margin-bottom:2px;">${frappe.utils.escape_html(header.project_name || header.project || "")}</h4>
			<div class="qw-muted">${frappe.utils.escape_html(header.milestone || "")} &middot;
				${frappe.utils.escape_html(header.name)}</div>
			<div class="qw-progress"><div style="width:${percent}%"></div></div>
			<div class="qw-stepline">
				<span>${__("Required checks")}: ${state.mandatory_answered || 0}/${state.mandatory_total || 0}</span>
				<span>${state.fail_count ? `<span class="qw-chip qw-chip-red">${state.fail_count} ${__("failing")}</span>` : ""}</span>
			</div>
		</div>`).appendTo(this.body);

		if (header.generation_note) {
			// Contracted criteria that named no milestone. Surfaced, never filtered away: a
			// promise that was sold and never inspected is the failure this whole programme
			// exists to end.
			$(`<div class="qw-banner qw-banner-amber"><h6>${__("Note from generation")}</h6>
				${qw_plain_html(header.generation_note)}</div>`).appendTo(this.body);
		}
		this.render_help(__("Safety"), this.doc.instructions.safety, "qw-banner-red");
	}

	render_help(title, html, _tone) {
		if (!html) return;
		const block = $(`<div class="qw-help">
			<button class="qw-help-head">${frappe.utils.escape_html(title)}
				<span class="qw-help-caret">&rsaquo;</span></button>
			<div class="qw-help-body" style="display:none;">${qw_rich_html(html)}</div>
		</div>`).appendTo(this.body);
		block.find(".qw-help-head").on("click", () => {
			block.toggleClass("qw-open");
			block.find(".qw-help-body").toggle();
		});
	}

	render_tabs() {
		const tabs = $('<div class="qw-tabs"></div>').appendTo(this.body);
		this.sections().forEach((section, index) => {
			const failing = section.rows.some(
				(row) => QW_FAILING.indexOf(row.outcome) !== -1 || row.out_of_range
			);
			const classes = [
				"qw-tab",
				index === this.section_index ? "qw-active" : "",
				failing ? "qw-has-fail" : "",
			].join(" ");
			$(`<button class="${classes}">${frappe.utils.escape_html(section.title)}</button>`)
				.appendTo(tabs)
				.on("click", () => this.go_section(index));
		});
	}

	render_section() {
		const section = this.current_section();
		if (section.location_note) {
			$(`<div class="qw-banner qw-banner-blue"><h6>${__("Where")}</h6>
				${qw_plain_html(section.location_note)}</div>`).appendTo(this.body);
		}
		section.rows.forEach((row) => this.render_row(row));
		if (this.section_index === this.sections().length - 1) {
			this.render_help(__("Before you finish"), this.doc.instructions.wrapup, "qw-banner-blue");
			this.render_wrapup();
		}
	}

	render_row(row) {
		const failing = QW_FAILING.indexOf(row.outcome) !== -1 || row.out_of_range;
		const answered = !!(row.outcome || row.measured_value || row.measured_value === 0);
		const classes = ["qw-card", failing ? "qw-bad" : answered ? "qw-done" : ""].join(" ");
		const card = $(`<div class="${classes}"></div>`).appendTo(this.body);

		$(`<div class="qw-card-title">${qw_plain_html(row.label)}${qw_required_chip(row)}</div>`).appendTo(
			card
		);
		if (row.acceptance_criteria) {
			$(`<div class="qw-card-sub"><b>${__("Passes when")}:</b> ${qw_plain_html(row.acceptance_criteria)}</div>`).appendTo(
				card
			);
		}
		if (row.method) {
			$(`<div class="qw-card-sub"><b>${__("How")}:</b> ${qw_plain_html(row.method)}</div>`).appendTo(card);
		}
		if (row.non_conformance) {
			$(`<div class="qw-card-sub"><span class="qw-chip qw-chip-red">${__("Non-conformance raised")}</span>
				${frappe.utils.escape_html(row.non_conformance)}</div>`).appendTo(card);
		}

		if (row.check_type === "Measurement") {
			this.render_measurement(card, row);
		}
		this.render_answers(card, row);
		this.render_extras(card, row);
	}

	render_measurement(card, row) {
		const wrap = $('<div class="qw-measure"></div>').appendTo(card);
		const input = $(
			`<input type="number" inputmode="decimal" step="any" value="${row.measured_value != null ? row.measured_value : ""}">`
		).appendTo(wrap);
		if (row.out_of_range) input.addClass("qw-oor");
		if (row.uom) $(`<span class="qw-uom">${frappe.utils.escape_html(row.uom)}</span>`).appendTo(wrap);

		// The contracted range, printed. An inspector who cannot see the bound is guessing at
		// what "passes" means, which is the gap this whole programme exists to close.
		const range = $(
			`<div class="qw-range ${row.out_of_range ? "qw-oor" : ""}">${__("Range")}: ${qw_range_text(row)}</div>`
		).appendTo(card);
		input.on("change", () => {
			const value = input.val() === "" ? null : parseFloat(input.val());
			row.measured_value = value;
			this.queue_row(row.name, { measured_value: value });
			range.removeClass("qw-oor");
		});
	}

	render_answers(card, row) {
		const options = row.options || [];
		if (!options.length) return;
		const seg = $('<div class="qw-seg"></div>').appendTo(card);
		options.forEach((option) => {
			const negative = QW_FAILING.indexOf(option) !== -1;
			const neutral = option === "N/A";
			const on = row.outcome === option;
			const classes = [
				on ? "qw-on" : "",
				on && negative ? "qw-neg" : "",
				on && neutral ? "qw-na" : "",
			].join(" ");
			$(`<button class="${classes}">${frappe.utils.escape_html(option)}</button>`)
				.appendTo(seg)
				.on("click", () => {
					row.outcome = row.outcome === option ? null : option;
					this.queue_row(row.name, { outcome: row.outcome });
					this.render();
				});
		});
	}

	render_extras(card, row) {
		const wrap = $('<div class="qw-row-extra"></div>').appendTo(card);
		const notes = $(
			`<input type="text" placeholder="${__("Note (optional)")}" value="${frappe.utils.escape_html(row.notes || "")}">`
		).appendTo(wrap);
		notes.on("change", () => {
			row.notes = notes.val();
			this.queue_row(row.name, { notes: row.notes });
		});

		const photo = $(
			`<button class="qw-photo-btn ${row.photo ? "qw-has-photo" : ""}" title="${__("Photo")}">&#128247;</button>`
		).appendTo(wrap);
		photo.on("click", () => this.capture_photo(row, photo));
	}

	capture_photo(row, button) {
		const inspection = this.doc.header.name;
		new frappe.ui.FileUploader({
			doctype: "Project Quality Inspection",
			docname: inspection,
			frm: null,
			allow_multiple: false,
			restrictions: { allowed_file_types: ["image/*"] },
			on_success: (file) => {
				row.photo = file.file_url;
				button.addClass("qw-has-photo");
				// Named, not taken from the screen: an upload can finish after the inspector has
				// moved on to another inspection, and the photo belongs to this one.
				this.queue_row(row.name, { photo: file.file_url }, inspection);
			},
		});
	}

	render_wrapup() {
		const header = this.doc.header;
		const card = $(`<div class="qw-card"><div class="qw-card-title">${__("Wrap up")}</div></div>`).appendTo(
			this.body
		);

		const date = $(`<div class="qw-row-extra">
			<input type="date" value="${frappe.utils.escape_html(header.inspection_date || frappe.datetime.get_today())}">
		</div>`).appendTo(card);
		// Kept on the header as well as queued, like a row's answer: the next repaint draws these
		// inputs from the header, and a value only in the queue was painted back to the old one.
		date.find("input").on("change", (event) => {
			header.inspection_date = $(event.currentTarget).val();
			this.queue_field("inspection_date", header.inspection_date);
		});

		const remarks = $(
			`<div class="qw-row-extra"><input type="text" placeholder="${__("Remarks (optional)")}" value="${frappe.utils.escape_html(header.remarks || "")}"></div>`
		).appendTo(card);
		remarks.find("input").on("change", (event) => {
			header.remarks = $(event.currentTarget).val();
			this.queue_field("remarks", header.remarks);
		});

		this.render_signature(card, __("Inspector signature"), "inspector_sign_off");
		this.render_signature(card, __("Client representative (optional)"), "client_sign_off");
	}

	render_signature(card, label, fieldname) {
		const current = this.doc.header[fieldname];
		const block = $(`<div class="qw-sign">
			<div class="qw-muted" style="margin-bottom:6px;">${frappe.utils.escape_html(label)}</div>
			${current ? `<img class="qw-sign-img" src="${frappe.utils.escape_html(current)}">` : ""}
		</div>`).appendTo(card);
		$(`<button class="btn btn-sm btn-default" style="margin-top:8px;">${current ? __("Re-sign") : __("Sign")}</button>`)
			.appendTo(block)
			.on("click", () => this.open_signature_pad(label, fieldname));
	}

	open_signature_pad(label, fieldname) {
		const doc = this.doc;
		const dialog = new frappe.ui.Dialog({
			title: label,
			fields: [{ fieldname: "signature", fieldtype: "Signature", label: label }],
			primary_action_label: __("Save"),
			primary_action: (values) => {
				dialog.hide();
				doc.header[fieldname] = values.signature;
				this.queue_field(fieldname, values.signature, doc.header.name);
				this.flush().then(() => this.render_unless_typing());
			},
		});
		dialog.show();
	}

	render_nav() {
		const nav = $('<div class="qw-nav"></div>').appendTo(this.page.main);
		this.page.main.find(".qw-nav").not(nav).remove();
		const last = this.section_index === this.sections().length - 1;

		// Both buttons route, like the tabs: every section is a history entry, so the phone's
		// Back undoes a Next. "Previous" rather than "Back", so this is not mistaken for it.
		$(`<button>${__("Previous")}</button>`)
			.appendTo(nav)
			.prop("disabled", this.section_index === 0)
			.on("click", () => this.go_section(this.section_index - 1));

		if (last) {
			$(`<button class="qw-primary">${__("Finish and submit")}</button>`)
				.appendTo(nav)
				.on("click", () => this.finish());
		} else {
			$(`<button class="qw-primary">${__("Next")}</button>`)
				.appendTo(nav)
				.on("click", () => this.go_section(this.section_index + 1));
		}
	}

	// One pill for the page's lifetime, made once. It reports the save queue, which outlives the
	// screen an answer was given on: a save still retrying must say so on the list too.
	render_save_state() {
		if (this.save_state_el) return;
		this.save_state_el = $('<div class="qw-savestate"></div>').appendTo(this.page.main);
	}

	render_submitted() {
		// Nothing left to step through, and a "Finish and submit" left on screen offered to
		// submit it again.
		this.page.main.find(".qw-nav").remove();
		const state = this.doc.state;
		const tone = state.fail_count ? "qw-banner-red" : "qw-banner-green";
		const line = state.fail_count
			? __("Submitted with {0} failing checks. A non-conformance and a corrective action have been raised for each.", [
					state.fail_count,
			  ])
			: __("Submitted. Every check passed.");
		// Router paths, which the Desk intercepts and pushes as history entries. An /app/ href is
		// a full page reload in v16 -- the router only intercepts /desk.
		const record = frappe.utils.get_form_link("Project Quality Inspection", this.doc.header.name);
		const list = frappe.router.make_url(["inspection-wizard"]);
		this.body.html(`<div class="qw-banner ${tone}">${line}</div>
			<div class="qw-empty">
				<p><a href="${record}">${__("Open the record")}</a></p>
				<p><a href="${list}">${__("Back to my inspections")}</a></p>
			</div>`);
	}

	// ------------------------------------------------------------------- saving

	// `inspection` defaults to the one on screen. The photo uploader and the signature pad pass
	// the one they were opened on, which the screen may have left by the time they finish.
	queue_field(field, value, inspection) {
		const name = inspection || (this.doc && this.doc.header.name);
		if (!name) return;
		this.queue_for(name).fields[field] = value;
		this.schedule_save();
	}

	queue_row(row_name, changes, inspection) {
		const name = inspection || (this.doc && this.doc.header.name);
		if (!name) return;
		const queued = this.queue_for(name);
		queued.rows[row_name] = Object.assign({}, queued.rows[row_name] || {}, changes);
		this.schedule_save();
	}

	// A new buffer takes its lock -- `base` -- when it is opened, from the stamp of the copy the
	// answer is being given on, and its save sends that, never whatever this.modified says by
	// the time it goes. A reopen reads a fresh stamp, and a queued answer sent under it
	// overwrote whatever somebody else saved in between, which the lock exists to refuse.
	queue_for(name) {
		if (!this.has_pending(name)) {
			this.pending[name] = { fields: {}, rows: {}, base: this.modified[name] };
		}
		return this.pending[name];
	}

	// The newest stamp known for an inspection, which a new buffer starts from. It only moves
	// forward: a bootstrap read before this inspection's last save landed would otherwise put
	// the older stamp back, and every save after it would be refused as stale. Frappe's
	// `modified` strings sort in time order.
	note_modified(name, modified) {
		if (!modified) return;
		const known = this.modified[name];
		if (!known || String(modified) > String(known)) this.modified[name] = String(modified);
	}

	schedule_save() {
		this.set_save_state("pending");
		clearTimeout(this.save_timer);
		this.save_timer = setTimeout(() => this.flush(), 900);
	}

	// Sends one inspection's queued answers -- the one on screen first -- or, while a save is
	// already on the wire, returns that one. Either way the promise settles once that request
	// has answered, and never rejects: a failure is put back and retried on its own timer.
	//
	// Everything the request needs comes from the queue, never from this.doc. The screen can
	// change under a pending save -- Back to the list clears it, opening another inspection
	// replaces it -- and reading this.doc here is what used to throw, drop the answer and leave
	// `saving` stuck true for the rest of the session.
	flush() {
		if (this.saving) return this.in_flight;
		const current = this.doc && this.doc.header.name;
		const name =
			current && this.has_pending(current)
				? current
				: Object.keys(this.pending).find((key) => this.has_pending(key));
		if (!name) return Promise.resolve();
		const queued = this.pending[name];
		delete this.pending[name];
		const patch = {
			fields: queued.fields,
			rows: Object.keys(queued.rows).map((row) => Object.assign({ name: row }, queued.rows[row])),
		};
		this.saving = true;
		this.set_save_state("saving");

		let accepted = false;
		this.in_flight = frappe
			.call({
				method: "erpnext_enhancements.api.quality_wizard.save_inspection",
				args: {
					inspection: name,
					patch: JSON.stringify(patch),
					modified: queued.base,
				},
			})
			.then((r) => {
				this.saving = false;
				accepted = true;
				if (!r || !r.message) return;
				this.note_modified(name, r.message.modified);
				// Answers queued while this one was on the wire were given on a screen already
				// showing it, so its new stamp is theirs too: our own write, not somebody else's.
				const later = this.pending[name];
				if (later && later.base === queued.base) later.base = String(r.message.modified);
				// Applied only to the inspection it came from. Applied to whichever one was on
				// screen, another inspection's `modified` failed every later save of it as stale.
				if (this.doc && this.doc.header.name === name) {
					this.apply_state(r.message);
					this.render_unless_typing();
				}
				if (this.has_pending()) {
					// Queued while this one was on the wire, when its own timer found `saving` set.
					this.schedule_save();
				} else {
					this.set_save_state("saved");
				}
			})
			.catch((error) => {
				this.saving = false;
				if (accepted) {
					// The server took this patch and the failure is in painting its answer.
					// Resending it could only be refused as stale.
					// eslint-disable-next-line no-console
					console.error(error);
					return;
				}
				// Put the patch back so nothing is lost, then say so and retry. A silent failed
				// autosave loses a whole inspection on a bad signal, and the inspector finds out
				// after they have left the site.
				const since = this.pending[name] || { fields: {}, rows: {} };
				this.pending[name] = {
					fields: Object.assign({}, patch.fields, since.fields),
					rows: patch.rows.reduce((acc, row) => {
						acc[row.name] = Object.assign({}, row, since.rows[row.name] || {});
						delete acc[row.name].name;
						return acc;
					}, since.rows),
					// The refused patch's own lock. A refusal for staleness stays one.
					base: queued.base,
				};
				this.set_save_state("error");
				this.schedule_retry();
			});
		return this.in_flight;
	}

	schedule_retry() {
		clearTimeout(this.retry_timer);
		this.retry_timer = setTimeout(() => this.flush(), 8000);
	}

	apply_state(state) {
		this.doc.state = state;
		const by_name = {};
		(state.rows || []).forEach((row) => {
			by_name[row.name] = row;
		});
		this.sections().forEach((section) => {
			section.rows.forEach((row) => {
				const fresh = by_name[row.name];
				if (!fresh) return;
				// out_of_range is the server's answer, not ours. Two implementations of the
				// same arithmetic is one more than can stay correct.
				row.out_of_range = fresh.out_of_range;
				row.outcome = fresh.outcome;
			});
		});
	}

	set_save_state(kind) {
		if (!this.save_state_el) return;
		const labels = {
			pending: __("Not saved yet"),
			saving: __("Saving..."),
			saved: __("Saved"),
			error: __("Not saved - retrying"),
		};
		this.save_state_el
			.toggleClass("qw-err", kind === "error")
			.html(`<span>${labels[kind] || ""}</span>`);
		if (kind === "saved") {
			setTimeout(() => {
				if (this.save_state_el) this.save_state_el.html("");
			}, 1600);
		}
	}

	// ------------------------------------------------------------------ finish

	finish() {
		if (!this.doc) return;
		const name = this.doc.header.name;
		const on_screen = () => !!this.doc && this.doc.header.name === name;
		this.flush().then(() => {
			// Left while it saved: nothing to confirm on a record that is no longer on screen.
			if (!on_screen()) return;
			if (this.has_pending(name)) {
				frappe.msgprint({
					title: __("Still saving"),
					message: __("Some answers have not reached the server yet. Wait for Saved, then finish."),
					indicator: "orange",
				});
				return;
			}
			frappe.confirm(
				__("Submit this inspection? Failed checks will raise a non-conformance and a corrective action, and the record can no longer be edited."),
				() => {
					frappe.call({
						method: "erpnext_enhancements.api.quality_wizard.finish_inspection",
						args: { inspection: name, modified: this.modified[name] },
						freeze: true,
						freeze_message: __("Submitting..."),
						callback: (r) => {
							if (!r || !r.message) return;
							this.note_modified(name, r.message.modified);
							if (!on_screen()) return;
							this.doc.state = r.message;
							this.render();
						},
					});
				}
			);
		});
	}
}

// ---------------------------------------------------------------------------
// Row chips.
//
// is_mandatory and requires_photo reach the submit gate whether or not anybody saw them. A
// check that is only revealed as required by a refusal — after the inspector has packed up and
// driven away — is a checklist that lied about what it wanted.
// ---------------------------------------------------------------------------

function qw_required_chip(row) {
	const chips = [];
	if (row.is_mandatory) chips.push(`<span class="qw-chip qw-chip-req">${__("Required")}</span>`);
	if (row.requires_photo) chips.push(`<span class="qw-chip qw-chip-photo">${__("Photo")}</span>`);
	if (row.source === "Carried Action") {
		chips.push(`<span class="qw-chip qw-chip-carried">${__("Re-check")}</span>`);
	}
	if (row.source === "Project Addendum") {
		chips.push(`<span class="qw-chip qw-chip-addendum">${__("Contracted")}</span>`);
	}
	return chips.join("");
}

function qw_range_text(row) {
	const low = row.min_value;
	const high = row.max_value;
	const uom = row.uom ? ` ${frappe.utils.escape_html(row.uom)}` : "";
	if (low != null && high != null) return `${low} - ${high}${uom}`;
	if (low != null) return `${__("at least")} ${low}${uom}`;
	if (high != null) return `${__("at most")} ${high}${uom}`;
	return __("not specified");
}
