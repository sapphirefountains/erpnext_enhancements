/**
 * File list enhancements.
 *
 * Two things, both Google-Drive flavoured:
 *
 *  1. Grid View is the default for the File list. Frappe's `FileView` keeps the
 *     grid/list choice in the static `frappe.views.FileView.grid_view`, seeded
 *     from `frappe.get_user_settings("File").grid_view || false`. We flip that
 *     default to `true` the first time a user lands here, so toggling back to
 *     list view still sticks.
 *
 *     We cannot use `get_user_settings("File").grid_view === undefined` to
 *     detect that "first time": `FileView.before_render()` unconditionally
 *     persists the current `grid_view` value on *every* render, so the first
 *     (list) render saves `false` and the "undefined" signal is gone forever.
 *     Instead we record our own one-time marker in `localStorage` and only
 *     force grid on the user's very first visit.
 *
 *  2. A quick-preview overlay. Clicking a file card opens an in-page preview
 *     (image / video / audio / pdf / text inline; everything else falls back to
 *     a download card) with Previous/Next pagination across the files currently
 *     listed, a Download button, and an "Open in new tab" button (which is the
 *     "preview of item in new tab" behaviour). An "Open" toolbar button does the
 *     same for the checked rows, so it works in list view too. Folders keep
 *     their normal navigation.
 *
 * `FileView` overrides `setup_view()` without calling the `listview_settings`
 * hooks, so we patch the prototype directly rather than going through
 * `frappe.listview_settings["File"]`.
 */
frappe.provide("erpnext_enhancements.file_preview");

(function () {
	const NS = erpnext_enhancements.file_preview;

	// ---- file-type detection ------------------------------------------------

	const EXT = {
		image: ["jpg", "jpeg", "png", "gif", "webp", "bmp", "svg", "ico", "avif", "apng"],
		video: ["mp4", "webm", "ogv", "mov", "m4v"],
		audio: ["mp3", "wav", "ogg", "oga", "m4a", "flac", "aac"],
		pdf: ["pdf"],
		text: ["txt", "csv", "log", "md", "markdown", "json", "xml", "yaml", "yml", "ini", "conf"],
	};

	function get_extension(filename) {
		if (!filename) return "";
		return String(filename).split("?")[0].split("#")[0].split(".").pop().toLowerCase();
	}

	function get_preview_type(file) {
		const ext = get_extension(file.file_name || file.file_url);
		for (const type of Object.keys(EXT)) {
			if (EXT[type].includes(ext)) return type;
		}
		return "other";
	}

	// ---- preview set helpers ------------------------------------------------

	// Build the ordered set of previewable (non-folder) files from a list view.
	function build_preview_set(listview) {
		return (listview.data || []).filter((d) => d && !d.is_folder && d.file_url);
	}

	function index_of(set, name) {
		return set.findIndex((d) => d.name === name);
	}

	// ---- the overlay --------------------------------------------------------

	let $overlay = null;
	let state = { set: [], index: 0 };

	function inject_styles() {
		if (document.getElementById("ee-file-preview-styles")) return;
		$("<style id='ee-file-preview-styles'>").html(`
			.ee-file-preview-overlay {
				position: fixed; inset: 0; z-index: 2050;
				background: rgba(20, 20, 22, 0.92);
				display: flex; flex-direction: column;
				color: #fff;
			}
			.ee-fp-topbar {
				display: flex; align-items: center; gap: 12px;
				padding: 10px 16px; flex: 0 0 auto;
			}
			.ee-fp-title {
				flex: 1 1 auto; min-width: 0;
				font-size: 15px; font-weight: 500;
				white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
			}
			.ee-fp-actions { display: flex; align-items: center; gap: 6px; flex: 0 0 auto; }
			.ee-fp-btn {
				background: transparent; border: 0; color: #fff;
				width: 38px; height: 38px; border-radius: 50%;
				display: inline-flex; align-items: center; justify-content: center;
				cursor: pointer; font-size: 15px; text-decoration: none;
			}
			.ee-fp-btn:hover { background: rgba(255, 255, 255, 0.15); color: #fff; }
			.ee-fp-body {
				flex: 1 1 auto; position: relative;
				display: flex; align-items: center; justify-content: center;
				overflow: hidden; padding: 0 64px;
			}
			.ee-fp-stage {
				max-width: 100%; max-height: 100%;
				display: flex; align-items: center; justify-content: center;
			}
			.ee-fp-stage img, .ee-fp-stage video { max-width: 100%; max-height: 82vh; border-radius: 4px; }
			.ee-fp-stage iframe {
				width: min(1100px, 90vw); height: 82vh; border: 0;
				background: #fff; border-radius: 4px;
			}
			.ee-fp-fallback {
				text-align: center; color: #dadce0;
				background: rgba(255,255,255,0.06);
				padding: 40px 48px; border-radius: 8px;
			}
			.ee-fp-fallback .ee-fp-fallback-icon { font-size: 56px; margin-bottom: 16px; opacity: 0.8; }
			.ee-fp-fallback .ee-fp-fallback-name { font-size: 15px; margin-bottom: 18px; word-break: break-all; }
			.ee-fp-nav {
				position: absolute; top: 50%; transform: translateY(-50%);
				width: 48px; height: 48px; border-radius: 50%;
				background: rgba(255, 255, 255, 0.12); border: 0; color: #fff;
				font-size: 20px; cursor: pointer;
				display: inline-flex; align-items: center; justify-content: center;
			}
			.ee-fp-nav:hover { background: rgba(255, 255, 255, 0.25); }
			.ee-fp-nav[disabled] { opacity: 0.25; cursor: default; }
			.ee-fp-prev { left: 12px; }
			.ee-fp-next { right: 12px; }
			.ee-fp-footer {
				flex: 0 0 auto; text-align: center;
				padding: 10px; font-size: 12px; color: #bdc1c6;
			}
			/* grid card hover affordance */
			.file-grid .file-wrapper { cursor: pointer; }
		`).appendTo("head");
	}

	function ensure_overlay() {
		if ($overlay) return $overlay;
		inject_styles();
		const icon = (name) => frappe.utils.icon(name, "sm");
		$overlay = $(`
			<div class="ee-file-preview-overlay" tabindex="-1" style="display:none;">
				<div class="ee-fp-topbar">
					<span class="ee-fp-title"></span>
					<span class="ee-fp-actions">
						<a class="ee-fp-btn ee-fp-download" title="${__("Download")}" download>
							${icon("download")}
						</a>
						<a class="ee-fp-btn ee-fp-newtab" title="${__("Open in new tab")}" target="_blank" rel="noopener">
							${icon("link-url")}
						</a>
						<button class="ee-fp-btn ee-fp-close" title="${__("Close")} (Esc)">
							${icon("close")}
						</button>
					</span>
				</div>
				<div class="ee-fp-body">
					<button class="ee-fp-nav ee-fp-prev" title="${__("Previous")}">&#10094;</button>
					<div class="ee-fp-stage"></div>
					<button class="ee-fp-nav ee-fp-next" title="${__("Next")}">&#10095;</button>
				</div>
				<div class="ee-fp-footer"></div>
			</div>
		`).appendTo(document.body);

		$overlay.find(".ee-fp-close").on("click", close_preview);
		$overlay.find(".ee-fp-prev").on("click", () => step(-1));
		$overlay.find(".ee-fp-next").on("click", () => step(1));
		// Click on the dark backdrop (but not the content) closes.
		$overlay.find(".ee-fp-body").on("click", (e) => {
			if (e.target === e.currentTarget) close_preview();
		});

		return $overlay;
	}

	function on_keydown(e) {
		if (!$overlay || $overlay.is(":hidden")) return;
		if (e.key === "Escape") close_preview();
		else if (e.key === "ArrowLeft") step(-1);
		else if (e.key === "ArrowRight") step(1);
	}

	function render_stage() {
		const file = state.set[state.index];
		if (!file) return;

		const $stage = $overlay.find(".ee-fp-stage").empty();
		const url = file.file_url;
		const type = get_preview_type(file);

		if (type === "image") {
			$stage.append($("<img>").attr("src", url).attr("alt", file.file_name || ""));
		} else if (type === "video") {
			$stage.append(
				$(`<video controls autoplay></video>`).attr("src", url)
			);
		} else if (type === "audio") {
			$stage.append($(`<audio controls autoplay style="width: min(600px, 80vw);"></audio>`).attr("src", url));
		} else if (type === "pdf" || type === "text") {
			$stage.append($("<iframe>").attr("src", url).attr("title", file.file_name || ""));
		} else {
			$stage.append(`
				<div class="ee-fp-fallback">
					<div class="ee-fp-fallback-icon">${frappe.utils.icon("file", "lg")}</div>
					<div class="ee-fp-fallback-name">${frappe.utils.escape_html(file.file_name || file.name)}</div>
					<div>${__("No inline preview available for this file type.")}</div>
				</div>
			`);
		}

		// Header + footer + action links
		$overlay.find(".ee-fp-title").text(file.file_name || file._title || file.name);
		$overlay.find(".ee-fp-download").attr("href", url).attr("download", file.file_name || "");
		$overlay.find(".ee-fp-newtab").attr("href", url);
		$overlay
			.find(".ee-fp-footer")
			.text(`${state.index + 1} ${__("of")} ${state.set.length}`);

		$overlay.find(".ee-fp-prev").prop("disabled", state.index <= 0);
		$overlay.find(".ee-fp-next").prop("disabled", state.index >= state.set.length - 1);
	}

	function step(delta) {
		const next = state.index + delta;
		if (next < 0 || next >= state.set.length) return;
		state.index = next;
		render_stage();
	}

	function open_preview(set, index) {
		if (!set || !set.length) {
			frappe.show_alert({ message: __("No previewable files."), indicator: "orange" });
			return;
		}
		ensure_overlay();
		state.set = set;
		state.index = Math.max(0, Math.min(index || 0, set.length - 1));
		render_stage();
		$overlay.css("display", "flex").focus();
		$(document).on("keydown.eeFilePreview", on_keydown);
	}

	function close_preview() {
		if (!$overlay) return;
		// Stop any playing media before tearing the stage down.
		$overlay.find(".ee-fp-stage").empty();
		$overlay.hide();
		$(document).off("keydown.eeFilePreview");
	}

	// Public entry used by the click/handlers below.
	NS.open = open_preview;
	NS.open_by_name = function (listview, name) {
		const set = build_preview_set(listview);
		open_preview(set, Math.max(0, index_of(set, name)));
	};

	// ---- wiring into the FileView -------------------------------------------

	const GRID_DEFAULT_KEY = "ee_file_grid_view_defaulted";

	function grid_default_applied() {
		try {
			return localStorage.getItem(GRID_DEFAULT_KEY) === "1";
		} catch (e) {
			return false;
		}
	}

	function mark_grid_default_applied() {
		try {
			localStorage.setItem(GRID_DEFAULT_KEY, "1");
		} catch (e) {
			// localStorage unavailable (private mode / disabled) — fall through.
		}
	}

	function apply_grid_default(listview) {
		// Force grid view the first time this user ever lands on the File list.
		// We deliberately do NOT consult `get_user_settings("File").grid_view`:
		// FileView.before_render() persists that value on every render, so it is
		// `false` after the first visit rather than `undefined`. Our own marker
		// is the only reliable "has this user seen the File list before?" signal.
		if (grid_default_applied()) return;
		mark_grid_default_applied();
		try {
			const FV = frappe.views.FileView;
			if (FV.grid_view !== true) {
				FV.grid_view = true;
				if (listview && !listview.$result.hasClass("file-grid-view")) {
					listview.refresh();
				}
			}
		} catch (e) {
			// non-fatal
		}
	}

	function add_open_button(listview) {
		if (listview.__ee_open_btn) return;
		listview.__ee_open_btn = true;
		listview.page.add_inner_button(__("Open"), () => {
			const set = build_preview_set(listview);
			const checked = listview.get_checked_items ? listview.get_checked_items(true) : [];
			let start = 0;
			if (checked && checked.length) {
				const i = set.findIndex((d) => d.name === checked[0]);
				if (i >= 0) start = i;
			}
			open_preview(set, start);
		});
	}

	function bind_card_clicks(listview) {
		if (listview.__ee_preview_bound) return;
		listview.__ee_preview_bound = true;
		// Delegated so it survives the grid re-rendering. Cards are
		// `<a class="file-wrapper" data-name="<escaped name>">`.
		listview.$result.on("click", "a.file-wrapper", function (e) {
			// Let the checkbox and copy-url controls behave normally.
			if ($(e.target).closest(".list-row-checkbox, .copy-file-url").length) return;

			const name = unescape($(this).attr("data-name") || "");
			const doc = (listview.data || []).find((d) => d.name === name);
			if (!doc || doc.is_folder) return; // folders navigate as usual

			e.preventDefault();
			e.stopPropagation();
			NS.open_by_name(listview, name);
		});
	}

	function enhance(listview) {
		if (!listview || listview.doctype !== "File") return;
		add_open_button(listview);
		bind_card_clicks(listview);
		apply_grid_default(listview);
	}

	function patch_fileview() {
		const FV = frappe.views && frappe.views.FileView;
		if (!FV) return false;
		if (!FV.__ee_patched) {
			FV.__ee_patched = true;
			const orig_setup_view = FV.prototype.setup_view;
			FV.prototype.setup_view = function () {
				orig_setup_view.apply(this, arguments);
				try {
					enhance(this);
				} catch (e) {
					console.warn("File preview enhance failed", e);
				}
			};
		}
		return true;
	}

	function init() {
		if (!patch_fileview()) {
			// FileView bundle not ready yet; retry shortly.
			setTimeout(init, 150);
			return;
		}
		// Handle a File list that is already on screen when this script loads.
		if (window.cur_list && cur_list.doctype === "File") {
			enhance(cur_list);
		}
	}

	$(document).on("app_ready", init);
	init();
})();
