// Process Document form script migrated from Client Script "Mermaid.js Render".
// Renders the `mermaid_code` field as a diagram and offers a Mermaid Live Editor link.
//
// Targets: the "Process Document" doctype form.
// Loaded via: hooks.py `doctype_js["Process Document"]`.
//
// Lazy-loads Mermaid.js from a CDN (once per session), renders the diagram into
// the `diagram` HTML field, and builds a deep link to mermaid.live for editing.

frappe.ui.form.on("Process Document", {
    refresh: function (frm) {
        load_and_render_mermaid(frm);
    },
    mermaid_code: function (frm) {
        load_and_render_mermaid(frm);
    },
});

// Flags to ensure the library is only loaded once per session.
let mermaid_loaded = false;
let mermaid_loading = false;

function load_and_render_mermaid(frm) {
    if (mermaid_loaded) {
        render_mermaid(frm);
        return;
    }

    if (!mermaid_loading) {
        mermaid_loading = true;

        let script = document.createElement("script");
        script.src = "https://cdn.jsdelivr.net/npm/mermaid@11.12.0/dist/mermaid.min.js";

        script.onload = () => {
            mermaid.initialize({ startOnLoad: false, theme: "default" });
            mermaid_loaded = true;
            mermaid_loading = false;
            render_mermaid(frm);
        };

        script.onerror = () => {
            console.error("Failed to load Mermaid.js script from CDN.");
            mermaid_loading = false;
            frm.get_field("diagram").$wrapper.html(
                '<div class="alert alert-danger">Could not load the Mermaid.js library. Please check your internet connection.</div>'
            );
        };

        document.head.appendChild(script);
    }
}

function render_mermaid(frm) {
    const diagram_wrapper = frm.get_field("diagram").$wrapper;
    diagram_wrapper.css("overflow-x", "auto");

    const mermaid_code = frm.doc.mermaid_code || "";

    const live_editor_config = {
        code: mermaid_code,
        mermaid: { theme: "default" },
        autoSync: true,
        updateEditor: true,
    };

    const base64_payload = btoa(unescape(encodeURIComponent(JSON.stringify(live_editor_config))));
    const editor_url = `https://mermaid.live/edit#${base64_payload}`;

    const editor_link_html = `
        <div style="margin-bottom: 10px; font-size: 12px;">
            <a href="${editor_url}" target="_blank" class="text-muted">
                <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display: inline-block; margin-right: 5px; vertical-align: text-bottom;"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path></svg>
                Create or edit diagram in a visual editor
            </a>
        </div>
    `;

    if (!frm.doc.mermaid_code) {
        diagram_wrapper.html(
            editor_link_html +
                '<p class="text-muted small">Enter Mermaid code in the field above to see a diagram.</p>'
        );
        return;
    }

    const escaped_code = frappe.utils.escape_html(mermaid_code);
    const mermaid_pre_tag = `<pre class="mermaid">${escaped_code}</pre>`;

    diagram_wrapper.html(editor_link_html + mermaid_pre_tag);

    setTimeout(() => {
        try {
            mermaid.run({
                nodes: diagram_wrapper.find(".mermaid"),
            });
        } catch (e) {
            console.error("Mermaid rendering error:", e);
            diagram_wrapper.html(
                editor_link_html +
                    `<div class="alert alert-danger"><b>Diagram Syntax Error:</b><br><pre>${e.message}</pre></div>`
            );
        }
    }, 100);
}
