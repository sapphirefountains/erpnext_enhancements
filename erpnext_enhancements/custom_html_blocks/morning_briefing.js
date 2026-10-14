// Morning Briefing — Custom HTML Block script (runs in the block's shadow-DOM
// sandbox; `root_element` is the shadow root provided by Frappe).
//
// Fetches the session user's cached daily briefing from
// erpnext_enhancements.api.briefing.get_morning_briefing and renders the
// markdown via frappe.markdown(). The Refresh button passes force=1, which
// regenerates synchronously (Gemini can take a while — spinner shown).
//
// Workspace re-renders re-run this whole script with a NEW root_element, so
// state lives on `window` (same model as the Task Dashboard block). A re-render
// is the first visit and coming back from a different workspace, not every
// return: from a form or a list, v16's Workspace.show() returns early on the
// workspace already shown. startApp hands a cached-read load to the shared return
// helper, public/js/global_enhancements/workspace_block_return.js, so a Home left
// open overnight shows today's briefing when you come back to it.
//
// Its panels (loading, empty, error, no access) come from sf_kit and its "only the
// newest answer draws" counter from blocks.ticket, both handed over by the same helper
// (custom_html_blocks/README.md). The briefing itself is still frappe.markdown().

(function () {
    const MAX_ATTEMPTS = 50;
    let attempts = 0;

    function getContainer() {
        return typeof root_element !== "undefined" && root_element ? root_element : document;
    }

    // The kit (panels, chips, escaping) and the ticket come from the shared helper,
    // workspace_block_return.js. A bundle cached from before them has neither: the block
    // then draws plain text, and every answer draws.
    function withKit(container) {
        const blocks = window.erpnext_enhancements && window.erpnext_enhancements.workspace_blocks;
        if (blocks && blocks.kit && blocks.ticket) {
            blocks.kit(container).then((sf) => startApp(container, sf, blocks.ticket));
            return;
        }
        const words = {
            loading: __("Loading…"),
            empty: __("Nothing to show"),
            error: __("Could not load"),
            "no-access": __("No access"),
        };
        const plain = (el, kind, opts) => (el.textContent = (opts && opts.title) || words[kind] || "");
        const escape = frappe.utils.escape_html;
        startApp(container, { esc: escape, chip: escape, state: { render: plain } }, () => () => true);
    }

    function waitForDOM() {
        const container = getContainer();
        if (container.querySelector("#mbr-body")) {
            withKit(container);
        } else if (++attempts < MAX_ATTEMPTS) {
            setTimeout(waitForDOM, 100);
        } else {
            console.warn("Morning Briefing: block DOM never appeared.");
        }
    }

    function startApp(container, sf, ticket) {
        const state = (window.__ee_morning_briefing = window.__ee_morning_briefing || {});

        const body = container.querySelector("#mbr-body");
        const meta = container.querySelector("#mbr-meta");
        const refresh_btn = container.querySelector("#mbr-refresh");

        function render(message) {
            if (!message || !message.available) {
                // Switched off, or nothing for today: the server's sentence, in the empty panel.
                sf.state.render(body, "empty", {
                    title: (message && message.reason) || __("Morning Briefing is not enabled."),
                });
                meta.textContent = "";
                return;
            }
            body.innerHTML = `<div class="mbr-content">${frappe.markdown(message.briefing || "")}</div>`;
            const source = message.source === "Fallback" ? " · data-only" : "";
            meta.textContent = `${message.date}${source}`;
        }

        // A return and Refresh are kept apart by the button (below); a ticket on the root
        // (blocks.ticket) also makes sure only the newest answer draws.
        function load(force) {
            const current = ticket(container);
            if (force) {
                sf.state.render(body, "loading", { title: __("Regenerating briefing…") });
            }
            refresh_btn.disabled = true;
            frappe
                .call({
                    method: "erpnext_enhancements.api.briefing.get_morning_briefing",
                    args: { force: force ? 1 : 0 },
                })
                .then((r) => {
                    if (current()) render(r.message);
                })
                .catch((err) => {
                    if (!current()) return;
                    console.error("Morning Briefing load failed:", err);
                    meta.textContent = "";
                    // Try again reads the cached briefing; it never regenerates.
                    const title = __("Could not load the briefing.");
                    if (err && err.status === 403) sf.state.render(body, "no-access");
                    else sf.state.render(body, "error", { title, on_action: () => load(false) });
                })
                .then(() => {
                    if (current()) refresh_btn.disabled = false;
                });
        }

        state.load = load;
        refresh_btn.addEventListener("click", () => load(true));

        sf.state.render(body, "loading");
        load(false);

        // Never force=1 on a return (that regenerates, and can take minutes), and never
        // while a load is still out: a Regenerate in flight must be the answer that draws.
        // The button is disabled exactly while one is.
        const blocks = window.erpnext_enhancements && window.erpnext_enhancements.workspace_blocks;
        if (blocks && blocks.onWorkspaceReturn) {
            blocks.onWorkspaceReturn(container, () => {
                if (!refresh_btn.disabled) load(false);
            });
        }
    }

    waitForDOM();
})();
