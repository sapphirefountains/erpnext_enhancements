// Awaiting Sign-Off — Design Dashboard Custom HTML Block.
//
// Designs that reached Reviewed but are not yet Issued, oldest first, from
// erpnext_enhancements.api.design_dashboard.get_signoff_queue.
//
// Shadow-DOM sandbox: `root_element` is the shadow root. The workspace runs this
// whole script again, with a fresh root, only when it renders the page: the first
// visit, and coming back from a different workspace. Coming back from a form or a
// list does not (v16's Workspace.show() returns early on the workspace already
// shown), so startApp hands load() to the shared return helper,
// public/js/global_enhancements/workspace_block_return.js, which runs it again
// then. Nothing is cached across renders, and the refresh listener is bound to the
// fresh DOM each time.

(function () {
    const MAX_ATTEMPTS = 50;
    const METHOD = "erpnext_enhancements.api.design_dashboard.get_signoff_queue";
    let attempts = 0;

    function getContainer() {
        return typeof root_element !== "undefined" && root_element ? root_element : document;
    }

    function waitForDOM() {
        const container = getContainer();
        if (container.querySelector("#dsq-body")) {
            startApp(container);
        } else if (++attempts < MAX_ATTEMPTS) {
            setTimeout(waitForDOM, 100);
        }
    }

    function esc(value) {
        return frappe.utils.escape_html(value === null || value === undefined ? "" : String(value));
    }

    function money(value) {
        if (value === null || value === undefined || value === "") return "";
        try {
            return frappe.format(value, { fieldtype: "Currency" });
        } catch (e) {
            return String(value);
        }
    }

    function num(value, decimals) {
        if (value === null || value === undefined || value === "") return "—";
        return Number(value).toLocaleString(undefined, {
            minimumFractionDigits: decimals || 0,
            maximumFractionDigits: decimals || 0,
        });
    }

    function muted(text) {
        return `<div class="dsq-muted">${text}</div>`;
    }

    function render(container, message) {
        const body = container.querySelector("#dsq-body");
        const count = container.querySelector("#dsq-count");

        if (!message || message.enabled === false) {
            count.textContent = "";
            body.innerHTML = muted(__("Awaiting Sign-Off is turned off in ERPNext Enhancements Settings."));
            return;
        }
        const designs = message.designs || [];
        count.textContent = designs.length ? __("{0} waiting", [designs.length]) : "";
        if (!designs.length) {
            body.innerHTML = muted(__("Nothing is waiting on a sign-off."));
            return;
        }

        body.innerHTML = designs
            .map((d) => {
                // "Package Ready" with blockers open is the combination worth seeing:
                // it means the design believes it is done and the checks disagree.
                const state = d.blockers
                    ? `<span class="dsq-bad">${esc(__("{0} blockers", [d.blockers]))}</span>`
                    : d.ready
                      ? `<span class="dsq-good">${__("Ready")}</span>`
                      : "";
                const meta = [d.customer, state].filter(Boolean).join(" · ");
                const tone = d.waiting_days >= 14 ? "dsq-bad" : d.waiting_days >= 7 ? "dsq-warn" : "";
                return `
                    <div class="dsq-row">
                        <a class="dsq-name" href="/app/water-feature-design/${encodeURIComponent(d.name)}">${esc(d.title)}</a>
                        <span class="dsq-meta">${meta}</span>
                        <span class="dsq-age ${tone}">${esc(__("{0}d", [d.waiting_days]))}</span>
                    </div>`;
            })
            .join("");
    }

    function startApp(container) {
        const body = container.querySelector("#dsq-body");
        const refresh = container.querySelector("#dsq-refresh");

        // The refresh button and a return to the workspace can both ask while an answer
        // is on its way, so each ask takes a ticket and only the newest one draws. The
        // ticket lives on the root: an answer only ever draws into the root it was for.
        function load() {
            const ticket = (container.__ee_ticket = (container.__ee_ticket || 0) + 1);
            const current = () => container.__ee_ticket === ticket;
            refresh.disabled = true;
            frappe
                .call({ method: METHOD })
                .then((r) => {
                    if (current()) render(container, r.message);
                })
                .catch(() => {
                    if (current()) body.innerHTML = muted(__("Could not load the sign-off queue."));
                })
                .then(() => {
                    if (current()) refresh.disabled = false;
                });
        }

        refresh.addEventListener("click", load);
        load();
        // Coming back to this workspace runs nothing again (the header), so the shared
        // helper reloads the block then. A bundle cached from before the helper has no
        // helper, and the block still draws; it just keeps its first answer.
        const blocks = window.erpnext_enhancements && window.erpnext_enhancements.workspace_blocks;
        if (blocks && blocks.onWorkspaceReturn) blocks.onWorkspaceReturn(container, load);
    }

    waitForDOM();
})();
