// Hydraulic Headroom — Design Dashboard Custom HTML Block.
//
// Issued designs ranked by flow margin, thinnest first, from
// erpnext_enhancements.api.design_dashboard.get_hydraulic_headroom.
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
    const METHOD = "erpnext_enhancements.api.design_dashboard.get_hydraulic_headroom";
    let attempts = 0;

    function getContainer() {
        return typeof root_element !== "undefined" && root_element ? root_element : document;
    }

    function waitForDOM() {
        const container = getContainer();
        if (container.querySelector("#dhh-body")) {
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
        return `<div class="dhh-muted">${text}</div>`;
    }

    function render(container, message) {
        const body = container.querySelector("#dhh-body");
        const count = container.querySelector("#dhh-count");

        if (!message || message.enabled === false) {
            count.textContent = "";
            body.innerHTML = muted(__("Hydraulic Headroom is turned off in ERPNext Enhancements Settings."));
            return;
        }
        const designs = message.designs || [];
        count.textContent = designs.length ? __("thinnest margins first") : "";
        if (!designs.length) {
            body.innerHTML = muted(__("No issued design has both a circulation requirement and a design flow recorded."));
            return;
        }

        body.innerHTML = designs
            .map((d) => {
                // The bar shows margin against a 50% "comfortable" ceiling, so a thin
                // selection reads as a short bar rather than needing the number read.
                const margin = d.margin_pct;
                const width = margin === null ? 0 : Math.max(0, Math.min(100, (margin / 50) * 100));
                const fill = d.under ? "dhh-fill-bad" : d.thin ? "dhh-fill-warn" : "dhh-fill-good";
                const tone = d.under ? "dhh-bad" : d.thin ? "dhh-warn" : "dhh-good";
                const label = margin === null ? "—" : `${margin > 0 ? "+" : ""}${num(margin, 1)}%`;
                const meta = [
                    d.pump,
                    __("{0} of {1} GPM", [num(d.design_gpm, 0), num(d.required_gpm, 0)]),
                ]
                    .filter(Boolean)
                    .map(esc)
                    .join(" · ");
                return `
                    <div class="dhh-row">
                        <a class="dhh-name" href="/app/water-feature-design/${encodeURIComponent(d.name)}">${esc(d.title)}</a>
                        <span class="dhh-meta">${meta}</span>
                        <span class="dhh-bar"><span class="dhh-fill ${fill}" style="width:${width}%"></span></span>
                        <span class="dhh-age ${tone}">${esc(label)}</span>
                    </div>`;
            })
            .join("");
    }

    function startApp(container) {
        const body = container.querySelector("#dhh-body");
        const refresh = container.querySelector("#dhh-refresh");

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
                    if (current()) body.innerHTML = muted(__("Could not load hydraulic margins."));
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
