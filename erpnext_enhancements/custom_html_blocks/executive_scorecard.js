// Company Scorecard — Executive Dashboard Custom HTML Block.
//
// One tile per department — Good / Watch / Bad counts from its latest KPI
// Snapshot, from erpnext_enhancements.api.executive_dashboard.get_scorecard.
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
    const METHOD = "erpnext_enhancements.api.executive_dashboard.get_scorecard";
    let attempts = 0;

    function getContainer() {
        return typeof root_element !== "undefined" && root_element ? root_element : document;
    }

    function waitForDOM() {
        const container = getContainer();
        if (container.querySelector("#exs-body")) {
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
        return `<div class="exs-muted">${text}</div>`;
    }

    function render(container, message) {
        const body = container.querySelector("#exs-body");
        const count = container.querySelector("#exs-count");

        if (!message || message.enabled === false) {
            count.textContent = "";
            body.innerHTML = muted(__("Company Scorecard is turned off in ERPNext Enhancements Settings."));
            return;
        }
        const cards = message.cards || [];
        count.textContent = "";
        if (!cards.length) {
            body.innerHTML = muted(__("No departments are configured."));
            return;
        }

        body.innerHTML =
            `<div class="exs-tiles">` +
            cards
                .map((c) => {
                    // Only link where the viewer can actually open that dashboard — an
                    // Accounts Manager should not get a link into HR.
                    const href = c.linkable
                        ? `/app/${frappe.router.slug(c.department + " Dashboard")}`
                        : null;
                    const tag = href ? "a" : "div";
                    const attrs = href ? ` href="${esc(href)}"` : "";

                    if (!c.available) {
                        return `
                    <${tag} class="exs-tile"${attrs}>
                        <span class="exs-tile-label">${esc(c.department)}</span>
                        <span class="exs-tile-value">—</span>
                        <span class="exs-tile-note">${esc(c.reason || "")}</span>
                    </${tag}>`;
                    }

                    const k = c.counts || {};
                    // Bad wins the headline: an executive tile should read as its worst
                    // number, not its average.
                    const tone = k.bad ? "exs-bad" : k.watch ? "exs-warn" : "exs-good";
                    const headline = k.bad ? k.bad : k.watch ? k.watch : k.good;
                    const headlineLabel = k.bad ? __("bad") : k.watch ? __("watch") : __("good");
                    const note = [
                        __("{0} of {1} KPIs", [headline, c.total]),
                        c.stale ? __("{0}d old", [c.age_days]) : "",
                    ]
                        .filter(Boolean)
                        .join(" · ");
                    return `
                    <${tag} class="exs-tile"${attrs}>
                        <span class="exs-tile-label">${esc(c.department)}</span>
                        <span class="exs-tile-value ${tone}">${num(headline)} ${esc(headlineLabel)}</span>
                        <span class="exs-tile-note ${c.stale ? "exs-warn" : ""}">${esc(note)}</span>
                    </${tag}>`;
                })
                .join("") +
            `</div>`;
    }

    function startApp(container) {
        const body = container.querySelector("#exs-body");
        const refresh = container.querySelector("#exs-refresh");

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
                    if (current()) body.innerHTML = muted(__("Could not load the scorecard."));
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
