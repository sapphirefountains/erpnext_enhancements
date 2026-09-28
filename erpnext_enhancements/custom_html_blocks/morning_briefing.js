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

(function () {
    const MAX_ATTEMPTS = 50;
    let attempts = 0;

    function getContainer() {
        return typeof root_element !== "undefined" && root_element ? root_element : document;
    }

    function waitForDOM() {
        const container = getContainer();
        if (container.querySelector("#mbr-body")) {
            startApp(container);
        } else if (++attempts < MAX_ATTEMPTS) {
            setTimeout(waitForDOM, 100);
        } else {
            console.warn("Morning Briefing: block DOM never appeared.");
        }
    }

    function startApp(container) {
        const state = (window.__ee_morning_briefing = window.__ee_morning_briefing || {});

        const body = container.querySelector("#mbr-body");
        const meta = container.querySelector("#mbr-meta");
        const refresh_btn = container.querySelector("#mbr-refresh");

        function render(message) {
            if (!message || !message.available) {
                body.innerHTML = `<div class="mbr-disabled">${frappe.utils.escape_html(
                    (message && message.reason) || __("Morning Briefing is not enabled.")
                )}</div>`;
                meta.textContent = "";
                return;
            }
            body.innerHTML = `<div class="mbr-content">${frappe.markdown(message.briefing || "")}</div>`;
            const source = message.source === "Fallback" ? " · data-only" : "";
            meta.textContent = `${message.date}${source}`;
        }

        function load(force) {
            if (force) {
                body.innerHTML = `<div class="mbr-loading">${__("Regenerating briefing…")}</div>`;
            }
            refresh_btn.disabled = true;
            frappe
                .call({
                    method: "erpnext_enhancements.api.briefing.get_morning_briefing",
                    args: { force: force ? 1 : 0 },
                })
                .then((r) => render(r.message))
                .catch((err) => {
                    console.error("Morning Briefing load failed:", err);
                    body.innerHTML = `<div class="mbr-disabled">${__("Could not load the briefing.")}</div>`;
                })
                .then(() => {
                    refresh_btn.disabled = false;
                });
        }

        state.load = load;
        refresh_btn.addEventListener("click", () => load(true));

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
