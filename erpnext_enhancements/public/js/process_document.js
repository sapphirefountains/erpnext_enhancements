// Process Document form script: Mermaid.js preview + the in-app visual builder.
//
// Migrated from Client Script "Mermaid.js Render" (preview + mermaid.live
// deep link), extended with a "Visual Builder" dialog: a split-pane editor
// (code left, live preview right) with snippet insertion, the Sapphire
// Fountains style pack, zoom, SVG export, and apply-back-to-document.
//
// Targets: the "Process Document" doctype form.
// Loaded via: hooks.py `doctype_js["Process Document"]`.
//
// Styling: diagrams render with the Sapphire Fountains brand theme from
// window.sf_mermaid (public/js/global_enhancements/mermaid_theme.js, shipped
// in erpnext_enhancements.bundle.js). Diagrams stay on a light canvas in
// both desk themes (see that file's header); the builder chrome follows the
// desk theme through Frappe CSS variables.

frappe.ui.form.on("Process Document", {
    refresh: function (frm) {
        frm.add_custom_button(__("Visual Builder"), () => open_visual_builder(frm));
        load_and_render_mermaid(frm);
    },
    mermaid_code: function (frm) {
        load_and_render_mermaid(frm);
    },
});

// ---------------------------------------------------------------------------
// Mermaid loader (one CDN fetch per session, shared by preview and builder)
// ---------------------------------------------------------------------------

let mermaid_promise = null;

function ensure_mermaid() {
    if (window.mermaid) {
        return Promise.resolve(window.mermaid);
    }
    if (mermaid_promise) {
        return mermaid_promise;
    }
    mermaid_promise = new Promise((resolve, reject) => {
        const script = document.createElement("script");
        script.src = "https://cdn.jsdelivr.net/npm/mermaid@11.15.0/dist/mermaid.min.js";
        script.onload = () => {
            init_mermaid_theme();
            resolve(window.mermaid);
        };
        script.onerror = () => {
            mermaid_promise = null;
            reject(new Error("Failed to load Mermaid.js from CDN"));
        };
        document.head.appendChild(script);
    });
    return mermaid_promise;
}

function init_mermaid_theme() {
    if (window.sf_mermaid) {
        window.sf_mermaid.init(window.mermaid);
    } else {
        // Fallback if the global bundle did not load for some reason.
        window.mermaid.initialize({ startOnLoad: false, theme: "default" });
    }
}

let render_seq = 0;

async function render_to_html(code) {
    // Parse first: it throws a useful syntax error without touching the DOM.
    const mermaid = await ensure_mermaid();
    await mermaid.parse(code);
    const { svg } = await mermaid.render(`sf-process-diagram-${++render_seq}`, code);
    return svg;
}

// ---------------------------------------------------------------------------
// Form preview
// ---------------------------------------------------------------------------

function load_and_render_mermaid(frm) {
    const wrapper = frm.get_field("diagram").$wrapper;
    wrapper.css("overflow-x", "auto");

    const code = (frm.doc.mermaid_code || "").trim();
    const header = `
        <div class="sf-diagram-links" style="margin-bottom: 10px; font-size: 12px;">
            <a href="#" class="sf-open-builder text-muted" style="margin-right: 12px;">
                ${frappe.utils.icon("edit", "sm")} ${__("Open Visual Builder")}
            </a>
            <a href="${mermaid_live_url(code)}" target="_blank" rel="noopener" class="text-muted">
                ${frappe.utils.icon("external-link", "sm")} ${__("Edit on mermaid.live")}
            </a>
        </div>
    `;

    if (!code) {
        wrapper.html(
            header +
                `<p class="text-muted small">${__(
                    "No diagram yet — open the Visual Builder or paste Mermaid code above."
                )}</p>`
        );
        bind_builder_link(frm, wrapper);
        return;
    }

    wrapper.html(header + `<div class="sf-diagram-canvas"><div class="text-muted small">${__("Rendering…")}</div></div>`);
    bind_builder_link(frm, wrapper);

    render_to_html(code)
        .then((svg) => {
            ensure_builder_css();
            wrapper.find(".sf-diagram-canvas").html(svg);
        })
        .catch((e) => {
            wrapper
                .find(".sf-diagram-canvas")
                .html(
                    `<div class="alert alert-danger"><b>${__("Diagram error")}:</b><br><pre style="white-space: pre-wrap;">${frappe.utils.escape_html(
                        e.message || String(e)
                    )}</pre></div>`
                );
        });
}

function bind_builder_link(frm, wrapper) {
    wrapper.find(".sf-open-builder").on("click", (e) => {
        e.preventDefault();
        open_visual_builder(frm);
    });
}

function mermaid_live_url(code) {
    const payload = {
        code: code || "graph TD\n    A[Start] --> B[End]",
        mermaid: { theme: "default" },
        autoSync: true,
        updateEditor: true,
    };
    const encoded = btoa(unescape(encodeURIComponent(JSON.stringify(payload))));
    return `https://mermaid.live/edit#${encoded}`;
}

// ---------------------------------------------------------------------------
// Visual builder dialog
// ---------------------------------------------------------------------------

const STARTER_TEMPLATE = [
    "graph TD",
    "    START((Start)) --> STEP1[First step]",
    "    STEP1 --> Q1{Decision?}",
    "    Q1 --> |Yes| STEP2[Next step]",
    "    Q1 --> |No| STEP1",
    "    STEP2 --> DONE((End))",
    "",
    "    %% Sapphire Fountains styling",
    "    SF_STYLE_PACK",
    "    class START,DONE teal;",
    "    class STEP1,STEP2 sapphire;",
    "    class Q1 sky;",
].join("\n");

function builder_snippets() {
    const pack = (window.sf_mermaid && window.sf_mermaid.CLASS_PACK) || "%% sf_mermaid bundle not loaded";
    return [
        { label: __("Starter flowchart"), code: STARTER_TEMPLATE.replace("SF_STYLE_PACK", pack) },
        { label: __("Step node"), code: "    STEP[Step name]\n" },
        { label: __("Decision node"), code: "    Q{Decision?}\n" },
        { label: __("Start / End nodes"), code: "    START((Start))\n    DONE((End))\n" },
        { label: __("External system node"), code: '    SYS[("External system")]\n' },
        { label: __("Arrow with label"), code: "    A --> |What happens| B\n" },
        { label: __("Dotted arrow (background sync)"), code: "    A -.-> |Background sync| B\n" },
        {
            label: __("Subgraph (process phase)"),
            code: '    subgraph PHASE["Phase name"]\n        A --> B\n    end\n',
        },
        { label: __("Sapphire Fountains style pack"), code: `    ${pack}\n    class STEP sapphire;\n` },
    ];
}

function open_visual_builder(frm) {
    ensure_builder_css();

    const d = new frappe.ui.Dialog({
        title: __("Process Builder — Sapphire Fountains"),
        size: "extra-large",
        fields: [{ fieldtype: "HTML", fieldname: "builder_area" }],
        primary_action_label: __("Apply to Document"),
        primary_action: () => {
            const code = d.$wrapper.find(".sf-builder-code").val();
            frm.set_value("mermaid_code", code);
            d.hide();
        },
    });
    d.$wrapper.addClass("sf-builder-dialog");

    const snippets = builder_snippets();
    const options = snippets
        .map((s, i) => `<option value="${i}">${frappe.utils.escape_html(s.label)}</option>`)
        .join("");

    d.fields_dict.builder_area.$wrapper.html(`
        <div class="sf-builder">
            <div class="sf-builder-toolbar">
                <select class="form-control sf-builder-insert" title="${__("Insert a building block at the cursor")}">
                    <option value="">${__("Insert…")}</option>
                    ${options}
                </select>
                <div class="sf-builder-toolbar-spacer"></div>
                <button class="btn btn-xs btn-default sf-zoom-out" title="${__("Zoom out")}">−</button>
                <span class="sf-zoom-label">100%</span>
                <button class="btn btn-xs btn-default sf-zoom-in" title="${__("Zoom in")}">+</button>
                <button class="btn btn-xs btn-default sf-zoom-reset">${__("Fit")}</button>
                <button class="btn btn-xs btn-default sf-copy-code">${__("Copy Code")}</button>
                <button class="btn btn-xs btn-default sf-download-svg">${__("Download SVG")}</button>
                <a class="btn btn-xs btn-default sf-live-link" target="_blank" rel="noopener">mermaid.live</a>
            </div>
            <div class="sf-builder-panes">
                <textarea class="sf-builder-code" spellcheck="false"
                    placeholder="${__("Mermaid code — use Insert… for building blocks")}"></textarea>
                <div class="sf-builder-preview">
                    <div class="sf-builder-canvas"></div>
                </div>
            </div>
            <div class="sf-builder-error hidden"></div>
        </div>
    `);

    const $area = d.fields_dict.builder_area.$wrapper;
    const $code = $area.find(".sf-builder-code");
    const $canvas = $area.find(".sf-builder-canvas");
    const $error = $area.find(".sf-builder-error");
    const $zoom_label = $area.find(".sf-zoom-label");

    let zoom = 1;
    let last_svg = "";
    let render_token = 0;

    $code.val(frm.doc.mermaid_code || "");

    const apply_zoom = () => {
        $canvas.css({ transform: `scale(${zoom})`, "transform-origin": "top left" });
        $zoom_label.text(`${Math.round(zoom * 100)}%`);
    };

    const refresh_live_link = () => {
        $area.find(".sf-live-link").attr("href", mermaid_live_url($code.val()));
    };

    const render_preview = async () => {
        const code = $code.val().trim();
        const token = ++render_token;
        refresh_live_link();
        if (!code) {
            $canvas.html(`<p class="text-muted small">${__("Type Mermaid code or use Insert… to get started.")}</p>`);
            $error.addClass("hidden");
            last_svg = "";
            return;
        }
        try {
            const svg = await render_to_html(code);
            if (token !== render_token) return; // a newer render superseded this one
            last_svg = svg;
            $canvas.html(svg);
            apply_zoom();
            $error.addClass("hidden");
        } catch (e) {
            if (token !== render_token) return;
            // Keep the last good diagram on screen; surface the error below.
            $error
                .removeClass("hidden")
                .text(`${__("Syntax error")}: ${(e.message || String(e)).split("\n")[0]}`);
        }
    };

    const debounced_render = frappe.utils.debounce(render_preview, 400);
    $code.on("input", debounced_render);

    $area.find(".sf-builder-insert").on("change", function () {
        const idx = this.value;
        this.value = "";
        if (idx === "") return;
        const snippet = snippets[Number(idx)];
        const el = $code.get(0);
        const start = el.selectionStart || 0;
        const end = el.selectionEnd || 0;
        const value = el.value;
        // Starter template replaces an empty editor; everything else inserts.
        if (!value.trim() && snippet.code.startsWith("graph")) {
            el.value = snippet.code;
        } else {
            const prefix = value.slice(0, start);
            const needs_newline = prefix && !prefix.endsWith("\n") ? "\n" : "";
            el.value = prefix + needs_newline + snippet.code + value.slice(end);
        }
        el.focus();
        debounced_render();
    });

    $area.find(".sf-zoom-in").on("click", () => {
        zoom = Math.min(2.5, zoom + 0.15);
        apply_zoom();
    });
    $area.find(".sf-zoom-out").on("click", () => {
        zoom = Math.max(0.3, zoom - 0.15);
        apply_zoom();
    });
    $area.find(".sf-zoom-reset").on("click", () => {
        zoom = 1;
        apply_zoom();
    });

    $area.find(".sf-copy-code").on("click", () => {
        frappe.utils.copy_to_clipboard($code.val());
    });

    $area.find(".sf-download-svg").on("click", () => {
        if (!last_svg) {
            frappe.show_alert({ message: __("Nothing rendered yet"), indicator: "orange" });
            return;
        }
        const blob = new Blob([last_svg], { type: "image/svg+xml" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `${frappe.scrub(frm.doc.title || "process-diagram")}.svg`;
        a.click();
        URL.revokeObjectURL(url);
    });

    d.show();
    render_preview();
}

// ---------------------------------------------------------------------------
// Builder styles (injected once; chrome uses Frappe CSS vars so both desk
// themes work, the diagram canvas stays a light brand surface — see
// mermaid_theme.js)
// ---------------------------------------------------------------------------

function ensure_builder_css() {
    if (document.getElementById("sf-process-builder-css")) return;
    const style = document.createElement("style");
    style.id = "sf-process-builder-css";
    style.textContent = `
        .sf-builder-dialog .modal-dialog { max-width: min(96vw, 1500px); }
        .sf-builder-toolbar {
            display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
            margin-bottom: 8px;
        }
        .sf-builder-toolbar .sf-builder-insert { width: auto; max-width: 260px; height: 26px; padding: 2px 8px; font-size: 12px; }
        .sf-builder-toolbar-spacer { flex: 1; }
        .sf-zoom-label { font-size: 11px; color: var(--text-muted); min-width: 38px; text-align: center; }
        .sf-builder-panes {
            display: grid; grid-template-columns: minmax(280px, 2fr) 3fr; gap: 10px;
            height: 62vh; min-height: 360px;
        }
        .sf-builder-code {
            width: 100%; height: 100%; resize: none;
            font-family: var(--font-family-monospace, monospace); font-size: 12px; line-height: 1.5;
            color: var(--text-color); background: var(--control-bg);
            border: 1px solid var(--border-color); border-radius: var(--border-radius, 6px);
            padding: 10px;
        }
        .sf-builder-preview {
            overflow: auto; border: 1px solid var(--border-color);
            border-radius: var(--border-radius, 6px);
            background: #F8F8F8; /* brand mist — light canvas in both themes */
        }
        .sf-builder-canvas { padding: 12px; width: max-content; min-width: 100%; }
        .sf-builder-error {
            margin-top: 8px; padding: 6px 10px; font-size: 12px;
            color: var(--text-color); background: var(--bg-orange, #fff3e0);
            border-left: 3px solid var(--orange-500, #ff9800); border-radius: 4px;
        }
        .sf-diagram-canvas {
            background: #F8F8F8; border: 1px solid var(--border-color);
            border-radius: var(--border-radius, 6px); padding: 12px; width: max-content;
            min-width: 100%;
        }
        @media (max-width: 991px) {
            .sf-builder-panes { grid-template-columns: 1fr; grid-template-rows: 1fr 1fr; }
        }
    `;
    document.head.appendChild(style);
}
